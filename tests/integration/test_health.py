from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_stable_json(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    body = response.json()
    assert set(body) == {"status", "service"}
    assert body["status"] == "ok"
    assert body["service"] == "watchdog"


def test_health_says_nothing_a_stranger_could_use(client: TestClient) -> None:
    """It is the one route with no sign-in in front of it, so it says the minimum.

    The version, the environment and the configured model provider were all here
    once. Each of them is worth having before attacking a site, and each is still
    visible inside the application to somebody who has signed in.
    """
    body = client.get("/health").json()

    assert "version" not in body
    assert "environment" not in body
    assert "llm_provider" not in body


def test_health_is_repeatable(client: TestClient) -> None:
    assert client.get("/health").json() == client.get("/health").json()
