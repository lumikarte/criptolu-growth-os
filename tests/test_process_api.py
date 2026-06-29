"""Tests del endpoint POST /uploads/{id}/process (PP-MVP-05): wiring y status codes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import pipeline

from .conftest import VALID_ID, make_upload


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_process_unknown_upload_404(iso, client):
    r = client.post(f"/uploads/{VALID_ID}/process")
    assert r.status_code == 404


def test_process_happy_path_200(iso, client, monkeypatch):
    make_upload()
    monkeypatch.setattr(
        pipeline, "process_upload",
        lambda uid, **kw: {"upload_id": uid, "stages": {}, "completed": ["transcribe"],
                           "failed": None},
    )
    r = client.post(f"/uploads/{VALID_ID}/process")
    assert r.status_code == 200
    assert r.json()["failed"] is None


def test_process_upstream_failure_maps_502_with_detail(iso, client, monkeypatch):
    make_upload()

    def boom(uid, **kw):
        raise pipeline.PipelineError("detect", "Groq caído", ["transcribe"], upstream=True)

    monkeypatch.setattr(pipeline, "process_upload", boom)
    r = client.post(f"/uploads/{VALID_ID}/process")
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["stage"] == "detect"
    assert detail["completed"] == ["transcribe"]


def test_process_validation_failure_maps_400(iso, client, monkeypatch):
    make_upload()

    def boom(uid, **kw):
        raise pipeline.PipelineError("clips", "sin momentos", ["transcribe", "detect"],
                                     upstream=False)

    monkeypatch.setattr(pipeline, "process_upload", boom)
    r = client.post(f"/uploads/{VALID_ID}/process")
    assert r.status_code == 400
    assert r.json()["detail"]["stage"] == "clips"


def test_process_forwards_force_and_platforms(iso, client, monkeypatch):
    make_upload()
    seen = {}

    def capture(uid, **kw):
        seen.update(kw)
        return {"upload_id": uid, "stages": {}, "completed": [], "failed": None}

    monkeypatch.setattr(pipeline, "process_upload", capture)
    client.post(f"/uploads/{VALID_ID}/process?force=true&platforms=tiktok,reels")
    assert seen["force"] is True
    assert seen["platforms"] == ["tiktok", "reels"]
