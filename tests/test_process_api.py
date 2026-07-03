"""Tests del endpoint POST /uploads/{id}/process — ahora asíncrono (CRI-603).

El POST **encola** un job y devuelve 202 con `job_id`; el resultado/errores se consultan por
`GET /jobs/{job_id}`. En modo **eager** (config.JOBS_EAGER=True) el job corre inline en el
mismo proceso, sin Redis ni worker, así la suite no necesita infraestructura. Se sigue
mockeando `pipeline.process_upload` igual que antes (el mock de FFmpeg/LLM no cambia).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import jobs, pipeline

from .conftest import VALID_ID, make_upload


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def eager(monkeypatch):
    """Corre los jobs inline (sin Redis) para poder ver el resultado apenas vuelve el POST."""
    monkeypatch.setattr(config, "JOBS_EAGER", True)


def _ok(uid, on_progress=None, **kw):
    # simula un pipeline exitoso; ejercita el callback de progreso si viene.
    if on_progress:
        on_progress("transcribe", "running")
        on_progress("transcribe", "done")
    return {"upload_id": uid, "stages": {}, "completed": ["transcribe"], "failed": None}


def test_process_unknown_upload_404(iso, eager, client):
    r = client.post(f"/uploads/{VALID_ID}/process")
    assert r.status_code == 404


def test_process_enqueues_and_returns_202(iso, eager, client, monkeypatch):
    make_upload()
    monkeypatch.setattr(pipeline, "process_upload", _ok)
    r = client.post(f"/uploads/{VALID_ID}/process")
    assert r.status_code == 202
    body = r.json()
    assert body["upload_id"] == VALID_ID
    assert body["poll"] == f"/jobs/{body['job_id']}"
    # en eager el job ya terminó al volver el POST
    j = client.get(body["poll"])
    assert j.status_code == 200
    assert j.json()["status"] == "finished"
    assert j.json()["result"]["completed"] == ["transcribe"]


def test_process_progress_tracked_in_job(iso, eager, client, monkeypatch):
    make_upload()
    monkeypatch.setattr(pipeline, "process_upload", _ok)
    body = client.post(f"/uploads/{VALID_ID}/process").json()
    job = client.get(body["poll"]).json()
    assert job["stages"]["transcribe"] == "done"
    assert "transcribe" in job["completed"]


def test_process_upstream_failure_recorded_in_job(iso, eager, client, monkeypatch):
    make_upload()

    def boom(uid, on_progress=None, **kw):
        raise pipeline.PipelineError("detect", "Groq caído", ["transcribe"], upstream=True)

    monkeypatch.setattr(pipeline, "process_upload", boom)
    body = client.post(f"/uploads/{VALID_ID}/process").json()
    job = client.get(body["poll"]).json()
    assert job["status"] == "failed"
    assert job["failed"]["stage"] == "detect"
    assert job["failed"]["upstream"] is True


def test_process_validation_failure_recorded_in_job(iso, eager, client, monkeypatch):
    make_upload()

    def boom(uid, on_progress=None, **kw):
        raise pipeline.PipelineError("clips", "sin momentos", ["transcribe", "detect"],
                                     upstream=False)

    monkeypatch.setattr(pipeline, "process_upload", boom)
    body = client.post(f"/uploads/{VALID_ID}/process").json()
    job = client.get(body["poll"]).json()
    assert job["status"] == "failed"
    assert job["failed"]["stage"] == "clips"
    assert job["failed"]["upstream"] is False


def test_process_forwards_force_and_platforms(iso, eager, client, monkeypatch):
    make_upload()
    seen = {}

    def capture(uid, on_progress=None, **kw):
        seen.update(kw)
        return {"upload_id": uid, "stages": {}, "completed": [], "failed": None}

    monkeypatch.setattr(pipeline, "process_upload", capture)
    client.post(f"/uploads/{VALID_ID}/process?force=true&platforms=tiktok,reels")
    assert seen["force"] is True
    assert seen["platforms"] == ["tiktok", "reels"]


def test_process_409_when_active_job(iso, eager, client, monkeypatch):
    make_upload()
    # sembrar un job activo (started) para esta subida
    jobs._save({**_seed_job(), "status": "started"})
    r = client.post(f"/uploads/{VALID_ID}/process")
    assert r.status_code == 409
    assert r.json()["detail"]["status"] == "started"


def test_process_force_bypasses_active_job(iso, eager, client, monkeypatch):
    make_upload()
    jobs._save({**_seed_job(), "status": "started"})
    monkeypatch.setattr(pipeline, "process_upload", _ok)
    r = client.post(f"/uploads/{VALID_ID}/process?force=true")
    assert r.status_code == 202


def _seed_job() -> dict:
    return {
        "job_id": "0" * 32, "upload_id": VALID_ID, "status": "queued", "params": {},
        "stages": {s: "pending" for s in jobs.STAGES}, "completed": [],
        "current_stage": None, "failed": None, "result": None,
        "enqueued_at": "x", "started_at": None, "finished_at": None,
    }


# --------------------------------------------------------------------------- #
# Encolado REAL con fakeredis (ejercita el camino RQ, sin Redis corriendo)
# --------------------------------------------------------------------------- #

def test_generic_exception_records_stage(iso, eager, client, monkeypatch):
    make_upload()

    def boom(uid, on_progress=None, **kw):
        if on_progress:
            on_progress("clips", "running")   # estábamos en 'clips' cuando reventó
        raise ValueError("bug inesperado")

    monkeypatch.setattr(pipeline, "process_upload", boom)
    body = client.post(f"/uploads/{VALID_ID}/process").json()
    job = client.get(body["poll"]).json()
    assert job["status"] == "failed"
    assert job["failed"]["stage"] == "clips"          # recuperada del progreso, no None
    assert job["failed"]["upstream"] is False


def test_enqueue_failure_marks_job_failed_not_orphan(iso, monkeypatch):
    make_upload()
    monkeypatch.setattr(config, "JOBS_EAGER", False)
    # simular Redis inaccesible al encolar
    monkeypatch.setattr("redis.Redis.from_url",
                        lambda url: (_ for _ in ()).throw(ConnectionError("no redis")))
    with pytest.raises(Exception):
        jobs.enqueue(VALID_ID, force=False)
    # el job NO debe quedar 'queued' (envenenaría el 409); queda failed → no activo
    assert jobs.active_job_for(VALID_ID) is None


def test_enqueue_real_queue_with_fakeredis(iso, monkeypatch):
    fakeredis = pytest.importorskip("fakeredis")
    rq = pytest.importorskip("rq")
    make_upload()
    monkeypatch.setattr(config, "JOBS_EAGER", False)
    fake = fakeredis.FakeStrictRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda url: fake)
    monkeypatch.setattr(pipeline, "process_upload", _ok)

    rec = jobs.enqueue(VALID_ID, force=False)
    assert rec["status"] == "queued"  # todavía no corrió (no es eager)

    q = rq.Queue(config.JOBS_QUEUE_NAME, connection=fake)
    rq.SimpleWorker([q], connection=fake).work(burst=True)  # procesa la cola en este proceso

    done = jobs.load(rec["job_id"])
    assert done["status"] == "finished"
    assert done["result"]["completed"] == ["transcribe"]
