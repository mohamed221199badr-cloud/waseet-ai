"""JWT login: the tenant comes from the token, never from a header."""

import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.config import settings
from tests.conftest import TEST_PASSWORD
from tests.helpers import auth_headers

LOGIN = "/api/v1/auth/login"


def _login(client, slug="test-biz-a", email="a@test.local", password=TEST_PASSWORD):
    return client.post(LOGIN, json={"business_slug": slug, "email": email, "password": password})


def test_login_success_and_me(client, two_businesses):
    r = _login(client)
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and body["expires_in"] > 0
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["business_id"] == str(two_businesses["a"])
    assert me.json()["role"] == "owner"


def test_email_is_case_insensitive(client, two_businesses):
    assert _login(client, email="A@Test.Local").status_code == 200


def test_bad_credentials_all_look_the_same(client, two_businesses):
    wrong_pw = _login(client, password="wrong-password")
    no_user = _login(client, email="nobody@test.local")
    no_biz = _login(client, slug="no-such-business")
    # user of business A with business B's slug
    cross = _login(client, slug="test-biz-b", email="a@test.local")
    for r in (wrong_pw, no_user, no_biz, cross):
        assert r.status_code == 401
        assert r.json() == {"detail": "Invalid credentials"}


def test_user_without_password_cannot_login(client, two_businesses, db_session):
    from app.models import User

    db_session.query(User).filter_by(email="a@test.local").update({"password_hash": None})
    db_session.commit()
    assert _login(client).status_code == 401


def test_x_business_id_header_alone_no_longer_works(client, two_businesses):
    r = client.get("/api/v1/products", headers={"X-Business-ID": str(two_businesses["a"])})
    assert r.status_code == 401


def test_header_cannot_override_token_tenant(client, two_businesses):
    """Token of business A + X-Business-ID of business B must still see only A."""
    headers = auth_headers(two_businesses["a"]) | {"X-Business-ID": str(two_businesses["b"])}
    skus = {p["sku"] for p in client.get("/api/v1/products", headers=headers).json()}
    assert skus == {"A-001"}


def _token(claims: dict, secret=None, algorithm="HS256") -> dict:
    return {"Authorization": "Bearer " + jwt.encode(
        claims, secret or settings.jwt_secret, algorithm=algorithm)}


def _claims(two, **over):
    token = auth_headers(two["a"])["Authorization"].split(" ")[1]
    c = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    c.update(over)
    return c


def test_expired_tampered_and_malformed_tokens_are_401(client, two_businesses):
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    expired = _token(_claims(two_businesses, exp=past))
    wrong_secret = _token(_claims(two_businesses), secret="x" * 40)
    no_exp = _claims(two_businesses)
    no_exp.pop("exp")
    missing_exp = _token(no_exp)
    alg_none = {"Authorization": "Bearer " + jwt.encode(
        _claims(two_businesses), key=None, algorithm="none")}
    for headers in (expired, wrong_secret, missing_exp, alg_none,
                    {"Authorization": "Bearer garbage"}, {"Authorization": "Basic abc"}):
        assert client.get("/api/v1/products", headers=headers).status_code == 401


def test_token_with_mismatched_business_is_401(client, two_businesses):
    """sub is user A but bid claims business B: both are re-checked against the database."""
    forged = _token(_claims(two_businesses, bid=str(two_businesses["b"])))
    assert client.get("/api/v1/products", headers=forged).status_code == 401
    ghost = _token(_claims(two_businesses, sub=str(uuid.uuid4())))
    assert client.get("/api/v1/products", headers=ghost).status_code == 401

