"""Smoke tests — ensure the app boots and core routes respond correctly."""

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_openapi_schema(client: TestClient) -> None:
    response = client.get("/docs")
    assert response.status_code == 200


def test_unauthenticated_darwins_returns_401(client: TestClient) -> None:
    response = client.get("/api/v1/darwins/")
    assert response.status_code in (401, 422)
