from __future__ import annotations

from fastapi.testclient import TestClient


def test_index_renders_the_template(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Scaffold is alive" in response.text


def test_page_loads_assets_from_our_own_static_folder(client: TestClient) -> None:
    html = client.get("/").text

    assert "/static/js/htmx.min.js" in html
    assert "/static/css/watchdog.css" in html
    assert "//unpkg.com" not in html
    assert "//cdn." not in html


def test_vendored_assets_are_served(client: TestClient) -> None:
    htmx = client.get("/static/js/htmx.min.js")
    css = client.get("/static/css/watchdog.css")

    assert htmx.status_code == 200
    assert "htmx" in htmx.text[:2000]
    assert css.status_code == 200


def test_htmx_fragment_is_returned(client: TestClient) -> None:
    response = client.get("/partials/ping", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert "HTMX is working" in response.text
    assert "<html" not in response.text
