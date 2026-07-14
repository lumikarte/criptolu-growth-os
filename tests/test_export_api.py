"""Tests de los endpoints de export (PP-MVP-03): wiring y status codes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import export

from .conftest import AUTH_HEADERS, VALID_ID, make_clips, make_upload


@pytest.fixture
def client():
    with TestClient(app, headers=AUTH_HEADERS) as c:
        yield c


def test_post_export_unknown_upload_404(iso, client):
    r = client.post(f"/uploads/{VALID_ID}/export")
    assert r.status_code == 404


def test_get_export_without_export_404(iso, client):
    make_upload()
    r = client.get(f"/uploads/{VALID_ID}/export")
    assert r.status_code == 404
    assert "todavía no fue exportada" in r.json()["detail"]


def test_post_export_happy_path(iso, client):
    make_upload()
    make_clips()
    r = client.post(f"/uploads/{VALID_ID}/export")
    assert r.status_code == 200
    assert r.json()["n_clips"] == 2
    # Y luego se puede leer el índice.
    assert client.get(f"/uploads/{VALID_ID}/export").status_code == 200


def test_post_export_invalid_platform_maps_400(iso, client):
    make_upload()
    make_clips()
    r = client.post(f"/uploads/{VALID_ID}/export?platforms=bogus")
    assert r.status_code == 400


def test_post_export_no_clips_maps_400(iso, client):
    make_upload()  # sin clips
    r = client.post(f"/uploads/{VALID_ID}/export")
    assert r.status_code == 400
