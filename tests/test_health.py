from app.config import settings


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_reports_environment_flags(client):
    resp = client.get("/health")
    body = resp.json()
    assert body["app_env"] == settings.app_env
    assert body["llm_mode"] == settings.llm_mode
    # non-secret only: no key in the payload may leak configuration beyond the two flags
    assert set(body) == {"status", "db", "app_env", "llm_mode"}
