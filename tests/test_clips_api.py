"""Tests de los endpoints de clips (PP-MVP-02): wiring, status codes y manejo de errores."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import clipping

from .conftest import AUTH_HEADERS, VALID_ID, make_upload


@pytest.fixture
def client():
    with TestClient(app, headers=AUTH_HEADERS) as c:
        yield c


def test_post_clips_unknown_upload_404(iso, client):
    r = client.post(f"/uploads/{VALID_ID}/clips")
    assert r.status_code == 404


def test_get_clips_without_clips_404(iso, client):
    make_upload()  # existe la subida pero todavía no hay clips
    r = client.get(f"/uploads/{VALID_ID}/clips")
    assert r.status_code == 404
    assert "todavía no tiene clips" in r.json()["detail"]


def test_post_clips_happy_path(iso, client, monkeypatch):
    make_upload()
    monkeypatch.setattr(
        clipping, "cut_clips",
        lambda uid, **kw: {"upload_id": uid, "mode": "video", "n_clips": 2, "clips": []},
    )
    r = client.post(f"/uploads/{VALID_ID}/clips")
    assert r.status_code == 200
    assert r.json()["mode"] == "video"


def test_post_clips_upstream_error_maps_502(iso, client, monkeypatch):
    make_upload()

    def _boom(uid, **kw):
        raise clipping.UpstreamError("No se encontró 'ffmpeg'.")

    monkeypatch.setattr(clipping, "cut_clips", _boom)
    r = client.post(f"/uploads/{VALID_ID}/clips")
    assert r.status_code == 502


def test_post_clips_validation_error_maps_400(iso, client, monkeypatch):
    make_upload()

    def _boom(uid, **kw):
        raise clipping.ClipError("No hay momentos para cortar.")

    monkeypatch.setattr(clipping, "cut_clips", _boom)
    r = client.post(f"/uploads/{VALID_ID}/clips")
    assert r.status_code == 400
