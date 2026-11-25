"""Staff management (Phase 1, F7 named tests): manager scope, audit, 403s."""
import pytest
import sqlalchemy as sa

from app.auth import create_access_token
from app.models import AuditEvent, User
from tests.conftest import test_password_hash

STAFF = "/api/v1/staff"


def _headers(user: User) -> dict:
    tok, _ = create_access_token(user.id, user.business_id)
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def manager(db_session, two_businesses):
    u = User(business_id=two_businesses["a"], full_name="مصطفى",
             email="manager@test.local", role="manager",
             password_hash=test_password_hash())
    db_session.add(u)
    db_session.commit()
    return u


def _create(client, h, role="cashier", email="c1@test.local"):
    return client.post(STAFF, headers=h, json={
        "full_name": "موظف", "email": email, "role": role, "password": "temp-pass-1"})


def test_manager_creating_owner_or_manager_is_rejected(client, manager):
    h = _headers(manager)
    assert _create(client, h, role="owner", email="o@test.local").status_code == 403
    assert _create(client, h, role="manager", email="m@test.local").status_code == 403


def test_manager_cannot_edit_himself_or_an_owner(client, manager, db_session):
    h = _headers(manager)
    assert client.patch(f"{STAFF}/{manager.id}", headers=h,
                        json={"full_name": "x"}).status_code == 403
    owner = db_session.scalar(sa.select(User).where(
        User.business_id == manager.business_id, User.role == "owner"))
    assert client.patch(f"{STAFF}/{owner.id}", headers=h,
                        json={"full_name": "x"}).status_code == 403
    # a cashier may be edited, but never promoted to owner or manager
    r = _create(client, h)
    assert r.status_code == 201
    cid = r.json()["id"]
    assert client.patch(f"{STAFF}/{cid}", headers=h,
                        json={"role": "owner"}).status_code == 403
    assert client.patch(f"{STAFF}/{cid}", headers=h,
                        json={"role": "manager"}).status_code == 403
    assert client.patch(f"{STAFF}/{cid}", headers=h,
                        json={"full_name": "موظف جديد"}).status_code == 200


def test_manager_creating_cashier_succeeds_and_is_audited(client, manager, db_session):
    h = _headers(manager)
    r = _create(client, h, email="c2@test.local")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "cashier" and body["must_change_password"] is True
    audit = db_session.scalar(sa.select(AuditEvent).where(
        AuditEvent.entity == "user", AuditEvent.action == "create"))
    assert audit is not None and audit.actor_id == manager.id
    # the new cashier can log in and is then forced to change the password
    from tests.helpers import auth_headers  # noqa: F401
    login = client.post("/api/v1/auth/login", json={
        "business_slug": "test-biz-a", "email": "c2@test.local",
        "password": "temp-pass-1"})
    assert login.status_code == 200
    me = client.get("/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert me.json().get("must_change_password") is True


def test_other_roles_get_403_on_every_staff_route(client, two_businesses, db_session):
    for role in ("cashier", "kitchen", "storekeeper", "delivery"):
        u = User(business_id=two_businesses["a"], full_name="x",
                 email=f"{role}@test.local", role=role,
                 password_hash=test_password_hash())
        db_session.add(u)
        db_session.commit()
        h = _headers(u)
        assert client.get(STAFF, headers=h).status_code == 403
        assert _create(client, h, email=f"z-{role}@test.local").status_code == 403
        assert client.patch(f"{STAFF}/{u.id}", headers=h,
                            json={"full_name": "y"}).status_code == 403
        assert client.post(f"{STAFF}/{u.id}/deactivate", headers=h).status_code == 403


def test_deactivation_revokes_sessions_and_is_audited(client, manager, db_session):
    h = _headers(manager)
    cid = _create(client, h, email="c3@test.local").json()["id"]
    login = client.post("/api/v1/auth/login", json={
        "business_slug": "test-biz-a", "email": "c3@test.local",
        "password": "temp-pass-1"})
    token = login.json()["access_token"]
    r = client.post(f"{STAFF}/{cid}/deactivate", headers=h)
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert client.get("/api/v1/auth/me",
                      headers={"Authorization": f"Bearer {token}"}).status_code == 401
    audit = db_session.scalar(sa.select(AuditEvent).where(
        AuditEvent.entity == "user",
        AuditEvent.payload["new"]["is_active"].as_boolean() == sa.false()))
    assert audit is not None and audit.payload["sessions_revoked"] >= 1


def test_staff_list_is_tenant_scoped(client, two_businesses):
    from tests.helpers import auth_headers
    ha = auth_headers(two_businesses["a"])
    hb = auth_headers(two_businesses["b"])
    _create(client, ha, email="scoped@test.local")
    emails_a = {u["email"] for u in client.get(STAFF, headers=ha).json()}
    emails_b = {u["email"] for u in client.get(STAFF, headers=hb).json()}
    assert "scoped@test.local" in emails_a and "scoped@test.local" not in emails_b
