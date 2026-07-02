"""Tests de la medición lite + Daily Brief (W-07, CRI-566)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import approval, metrics

from .conftest import VALID_ID, make_clips, make_upload

# Un segundo upload id válido (hex de 32) para probar la agregación del brief.
OTHER_ID = "b1c2d3e4f5061728394a5b6c7d8e9f00"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- #
# Registro por episodio y por pieza
# --------------------------------------------------------------------------- #

def test_record_work_and_report(iso):
    make_upload()
    make_clips()
    metrics.record_episode_work(VALID_ID, 45)
    rep = metrics.episode_report(VALID_ID)
    assert rep["episode"]["manual_minutes"] == 45
    assert rep["approval"]["n_pieces"] == 2


def test_record_work_negative_raises(iso):
    make_upload()
    with pytest.raises(metrics.MetricsError):
        metrics.record_episode_work(VALID_ID, -5)


def test_record_piece_performance(iso):
    make_upload()
    make_clips()
    metrics.record_piece_performance(VALID_ID, "clip:1", reach=1000, engagement=120)
    rep = metrics.episode_report(VALID_ID)
    assert rep["performance"]["totals"]["reach"] == 1000
    assert rep["performance"]["totals"]["engagement"] == 120
    assert rep["performance"]["totals"]["pieces_measured"] == 1


def test_record_piece_unknown_raises(iso):
    make_upload()
    make_clips()
    with pytest.raises(metrics.MetricsError):
        metrics.record_piece_performance(VALID_ID, "clip:99", reach=1, engagement=1)


def test_record_piece_negative_raises(iso):
    make_upload()
    make_clips()
    with pytest.raises(metrics.MetricsError):
        metrics.record_piece_performance(VALID_ID, "clip:1", reach=-1, engagement=0)


# --------------------------------------------------------------------------- #
# "Aprobadas sin retoque" (revisions == 1)
# --------------------------------------------------------------------------- #

def test_approved_clean_counts_only_first_time_approvals(iso):
    make_upload()
    make_clips()
    # clip:1 aprobado a la primera → sin retoque
    approval.decide(VALID_ID, "clip:1", approval.APPROVED)
    # clip:2 rechazado y luego aprobado → CON retoque (revisions == 2)
    approval.decide(VALID_ID, "clip:2", approval.REJECTED)
    approval.decide(VALID_ID, "clip:2", approval.APPROVED)
    counts = metrics._approval_counts(VALID_ID)
    assert counts["n_approved"] == 2
    assert counts["n_approved_clean"] == 1


# --------------------------------------------------------------------------- #
# Daily Brief
# --------------------------------------------------------------------------- #

def test_daily_brief_aggregates_episodes(iso):
    make_upload(upload_id=VALID_ID)
    make_clips(upload_id=VALID_ID)
    make_upload(upload_id=OTHER_ID)
    make_clips(upload_id=OTHER_ID)
    for uid in (VALID_ID, OTHER_ID):
        metrics.record_episode_work(uid, 30)
        approval.decide(uid, "clip:1", approval.APPROVED)
        metrics.record_piece_performance(uid, "clip:1", reach=500, engagement=50)

    brief = metrics.daily_brief()
    assert brief["episodes"] == 2
    assert brief["totals"]["manual_minutes"] == 60
    assert brief["totals"]["pieces"] == 4          # 2 clips x 2 episodios
    assert brief["totals"]["approved"] == 2
    assert brief["totals"]["approved_clean"] == 2
    assert brief["totals"]["reach"] == 1000
    assert brief["rates"]["approval_rate"] == round(2 / 4, 3)
    assert brief["rates"]["clean_rate"] == 1.0
    assert brief["rates"]["avg_minutes_per_episode"] == 30.0


def test_daily_brief_empty_is_safe(iso):
    brief = metrics.daily_brief()
    assert brief["episodes"] == 0
    assert brief["rates"]["approval_rate"] is None
    assert brief["rates"]["clean_rate"] is None


def test_daily_brief_date_filter(iso):
    make_upload()
    make_clips()
    metrics.record_episode_work(VALID_ID, 20)
    today = metrics._today()
    assert metrics.daily_brief(date=today)["episodes"] == 1
    assert metrics.daily_brief(date="1999-01-01")["episodes"] == 0


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

def test_endpoints_happy_path(iso, client):
    make_upload()
    make_clips()
    assert client.post(f"/uploads/{VALID_ID}/metrics/work?manual_minutes=40").status_code == 200
    r = client.post(f"/uploads/{VALID_ID}/metrics/pieces/clip:1?reach=800&engagement=90")
    assert r.status_code == 200
    rep = client.get(f"/uploads/{VALID_ID}/metrics")
    assert rep.status_code == 200
    assert rep.json()["performance"]["totals"]["reach"] == 800
    brief = client.get("/brief")
    assert brief.status_code == 200
    assert brief.json()["episodes"] == 1


def test_work_unknown_upload_404(iso, client):
    r = client.post(f"/uploads/{VALID_ID}/metrics/work?manual_minutes=10")
    assert r.status_code == 404


def test_piece_negative_maps_400(iso, client):
    make_upload()
    make_clips()
    r = client.post(f"/uploads/{VALID_ID}/metrics/pieces/clip:1?reach=-3&engagement=1")
    assert r.status_code == 400
