import importlib

import pytest
from fastapi.testclient import TestClient

from backend import app as app_module


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
