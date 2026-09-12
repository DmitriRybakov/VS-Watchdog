from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_stable_json(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    body = response.json()
    assert set(body) == {"status", "service", "version", "environment", "llm_provider"}
    assert body["status"] == "ok"
    assert body["service"] == "watchdog"


def test_health_is_repeatable(client: TestClient) -> None:
    assert client.get("/health").json() == client.get("/health").json()
