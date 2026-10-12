import pytest
from fastapi.testclient import TestClient

from backend import app as app_module
from backend import errors


@pytest.fixture(autouse=True)
def clean_adapter_errors():
    """`last_adapter_errors` is process-wide; keep tests independent of each other."""
    errors.last_adapter_errors.clear()
    yield
    errors.last_adapter_errors.clear()


@pytest.fixture()
def client():
    return TestClient(app_module.app)


def test_health_returns_200(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["errors"] == {}


def test_health_ok_without_token_configured(monkeypatch, client):
    monkeypatch.delenv("TRIPTYCH_TOKEN", raising=False)
    response = client.get("/api/health")
    assert response.status_code == 200


def test_health_reports_recorded_adapter_errors(client):
    errors.record_adapter_error("narou", "timeout")
    errors.record_adapter_error("kakuyomu", "robots.txt disallows fetching /search")

    body = client.get("/api/health").json()

    assert body["status"] == "ok"
    assert set(body["errors"]) == {"narou", "kakuyomu"}
    narou = body["errors"]["narou"]
    assert narou["source"] == "narou"
    assert narou["message"] == "timeout"
    assert narou["occurredAt"]  # ISO 8601 timestamp
    assert body["errors"]["kakuyomu"]["message"] == "robots.txt disallows fetching /search"


def test_health_keeps_only_the_latest_error_per_source(client):
    errors.record_adapter_error("narou", "first")
    errors.record_adapter_error("narou", "second")

    body = client.get("/api/health").json()

    assert list(body["errors"]) == ["narou"]
    assert body["errors"]["narou"]["message"] == "second"


def test_health_drops_an_error_once_it_is_cleared(client):
    errors.record_adapter_error("narou", "timeout")
    errors.clear_adapter_error("narou")

    assert client.get("/api/health").json()["errors"] == {}


def test_health_reads_the_shared_errors_module():
    # The old app-local dict is gone: `backend.errors` is the only store.
    assert not hasattr(app_module, "last_adapter_errors")


def test_api_requires_bearer_token_when_configured(monkeypatch):
    monkeypatch.setenv("TRIPTYCH_TOKEN", "secret123")
    client = TestClient(app_module.app)

    unauthenticated = client.get("/api/health")
    assert unauthenticated.status_code == 401

    wrong_token = client.get(
        "/api/health", headers={"Authorization": "Bearer wrong"}
    )
    assert wrong_token.status_code == 401

    authenticated = client.get(
        "/api/health", headers={"Authorization": "Bearer secret123"}
    )
    assert authenticated.status_code == 200


def test_default_bind_host_is_localhost():
    assert app_module.DEFAULT_HOST == "127.0.0.1"
