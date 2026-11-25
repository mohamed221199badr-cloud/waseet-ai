"""F3: refresh rotation, theft detection, logout, password change, login rate limit."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import settings
from app.models import User
from app.models_roles import LoginAttempt, UserSession
from tests.conftest import TEST_PASSWORD

LOGIN = "/api/v1/auth/login"
BODY = {"business_slug": "test-biz-a", "email": "a@test.local", "password": TEST_PASSWORD}


def _login(client, **over):
    return client.post(LOGIN, json={**BODY, **over})


def test_login_returns_refresh_and_me_works(client, two_businesses):
    r = _login(client)
    assert r.status_code == 200, r.text
    tok = r.json()
    assert tok["access_token"] and tok["refresh_token"] and tok["expires_in"] <= 15 * 60
    me = client.get("/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {tok['access_token']}"})
    assert me.status_code == 200 and me.json()["must_change_password"] is False


def test_refresh_rotates_and_old_token_is_killed(client, two_businesses, db_session):
    tok = _login(client).json()
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    assert r.status_code == 200, r.text
    tok2 = r.json()
    assert tok2["refresh_token"] != tok["refresh_token"]
    # the rotated-out token cannot be used again: the whole family dies (theft signal)
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    assert r.status_code == 401
    assert client.post("/api/v1/auth/refresh",
                       json={"refresh_token": tok2["refresh_token"]}).status_code == 401
    sessions = db_session.scalars(select(UserSession)).all()
    assert sessions and all(s.revoked_at is not None for s in sessions)


def test_logout_revokes_only_the_presented_token(client, two_businesses):
    tok = _login(client).json()
    assert client.post("/api/v1/auth/logout",
                       json={"refresh_token": tok["refresh_token"]}).status_code == 204
    assert client.post("/api/v1/auth/refresh",
                       json={"refresh_token": tok["refresh_token"]}).status_code == 401
    # idempotent: unknown tokens are ignored
    assert client.post("/api/v1/auth/logout",
                       json={"refresh_token": "x" * 40}).status_code == 204


def test_change_password_revokes_sessions_and_old_password_dies(client, two_businesses):
    tok = _login(client).json()
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    assert client.post("/api/v1/auth/change-password", headers=h, json={
        "current_password": TEST_PASSWORD, "new_password": "new-pass-456"}).status_code == 204
    assert client.post("/api/v1/auth/refresh",
                       json={"refresh_token": tok["refresh_token"]}).status_code == 401
    assert _login(client).status_code == 401
    assert _login(client, password="new-pass-456").status_code == 200
    # restore for other tests
    tok = _login(client, password="new-pass-456").json()
    client.post("/api/v1/auth/change-password",
                headers={"Authorization": f"Bearer {tok['access_token']}"}, json={
                    "current_password": "new-pass-456", "new_password": TEST_PASSWORD})


def test_lockout_at_fifth_failure_same_answer_for_unknown_email(client, two_businesses):
    # five failures pass through as ordinary 401s; the NEXT attempt in the window is 429
    for _ in range(settings.login_max_failures_per_email):
        assert _login(client, password="wrong").status_code == 401
    r = _login(client, password="wrong")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    # the lock counts the email, so even the right password is refused inside the window
    assert _login(client).status_code == 429
    # unknown emails are counted too and look identical (no account enumeration)
    for _ in range(settings.login_max_failures_per_email):
        r = _login(client, email="ghost@test.local", password="wrong")
        assert r.status_code == 401
    assert _login(client, email="ghost@test.local", password="wrong").status_code == 429



def test_window_expiry_frees_the_email(client, two_businesses, db_session):
    for _ in range(settings.login_max_failures_per_email):
        _login(client, password="wrong")
    assert _login(client).status_code == 429
    old = datetime.utcnow() - timedelta(minutes=settings.login_window_minutes + 1)
    db_session.execute(
        LoginAttempt.__table__.update().values(at=old))
    db_session.commit()
    assert _login(client).status_code == 200


def test_two_sessions_share_the_db_count(client, two_businesses, db_session):
    """The limit is DB-backed: attempts written directly by 'another worker' count."""
    for _ in range(settings.login_max_failures_per_email):
        db_session.add(LoginAttempt(email_norm="a@test.local", ip="10.0.0.9", success=False))
    db_session.commit()
    assert _login(client).status_code == 429


def test_deactivated_user_token_and_login_are_refused(client, two_businesses, db_session):
    tok = _login(client).json()
    user = db_session.scalar(select(User).where(User.business_id == two_businesses["a"]))
    user.is_active = False
    db_session.commit()
    assert client.get("/api/v1/auth/me",
                      headers={"Authorization": f"Bearer {tok['access_token']}"}
                      ).status_code == 401
    assert _login(client).status_code == 401
    assert client.post("/api/v1/auth/refresh",
                       json={"refresh_token": tok["refresh_token"]}).status_code == 401


def test_unknown_business_and_garbage_tokens(client, two_businesses):
    assert _login(client, business_slug="no-such-biz").status_code == 401
    assert client.post("/api/v1/auth/refresh",
                       json={"refresh_token": str(uuid.uuid4())}).status_code == 401
