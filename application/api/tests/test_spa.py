"""SPA catch-all route: index fallback, static files, reserved prefixes, traversal guard."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import memchentic.main as main_module


@pytest.fixture
def spa_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """An app whose ``gui/dist`` lives in a temporary directory (lifespan not started)."""
    dist = tmp_path / "gui" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa-index</html>")
    (dist / "robots.txt").write_text("spa-robots")
    (dist / "assets" / "app.js").write_text("console.log('spa-asset')")
    (tmp_path / "secret.txt").write_text("outside-dist")
    monkeypatch.setattr(main_module, "API_ROOT", tmp_path)
    return TestClient(main_module.create_app())


def test_root_and_client_routes_return_index(spa_client: TestClient) -> None:
    for path in ("/", "/groups/123", "/settings/keys"):
        res = spa_client.get(path)
        assert res.status_code == 200
        assert "spa-index" in res.text
    assert spa_client.get("/").headers["x-frame-options"] == "DENY"


def test_existing_dist_files_are_served(spa_client: TestClient) -> None:
    assert spa_client.get("/robots.txt").text == "spa-robots"
    assert "spa-asset" in spa_client.get("/assets/app.js").text


def test_reserved_prefixes_return_404_not_index(spa_client: TestClient) -> None:
    for path in ("/api/does-not-exist", "/oauth/does-not-exist"):
        res = spa_client.get(path)
        assert res.status_code == 404
        assert "spa-index" not in res.text


def test_path_traversal_returns_index_not_outside_file(spa_client: TestClient) -> None:
    res = spa_client.get("/..%2F..%2Fsecret.txt")
    assert res.status_code == 200
    assert "outside-dist" not in res.text
    assert "spa-index" in res.text
