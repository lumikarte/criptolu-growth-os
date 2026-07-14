"""Tests del gate de aprobación humana (W-06, CRI-565): servicio + endpoints + guarda."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import approval, storage

from .conftest import AUTH_HEADERS, VALID_ID, make_clips, make_upload
from .test_repurpose import _valid_package


def _make_repurpose(upload_id: str = VALID_ID) -> None:
    """Escribe un repurpose.json mínimo con carrusel + feed + hilo."""
    out_dir = config.CLIPS_DIR / upload_id
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"upload_id": upload_id, "content": _valid_package()}
    storage.atomic_write_text(out_dir / "repurpose.json", json.dumps(payload))


@pytest.fixture
def client():
    with TestClient(app, headers=AUTH_HEADERS) as c:
        yield c


# --------------------------------------------------------------------------- #
# Derivación de piezas y estado
# --------------------------------------------------------------------------- #

def test_pieces_derived_from_clips_and_repurpose(iso):
    make_upload()
    make_clips()          # 2 clips
    _make_repurpose()     # carousel + feed_post + thread + captions por red
    keys = {p["key"] for p in approval.list_pieces(VALID_ID)}
    expected = {"clip:1", "clip:2", "carousel", "feed_post", "thread"}
    expected |= {f"caption:{n}" for n in config.REPURPOSE_NETWORKS}
    assert keys == expected


def test_pieces_empty_when_no_artifacts(iso):
    make_upload()
    assert approval.list_pieces(VALID_ID) == []


def test_state_defaults_all_pending(iso):
    make_upload()
    make_clips()
    st = approval.get_state(VALID_ID)
    assert st["summary"]["total"] == 2
    assert st["summary"]["pending"] == 2
    assert st["summary"]["all_approved"] is False
    assert all(p["status"] == "pending" for p in st["pieces"])


# --------------------------------------------------------------------------- #
# Decisiones y guarda
# --------------------------------------------------------------------------- #

def test_approve_marks_publishable(iso):
    make_upload()
    make_clips()
    approval.decide(VALID_ID, "clip:1", approval.APPROVED)
    assert approval.is_approved(VALID_ID, "clip:1") is True
    assert approval.is_approved(VALID_ID, "clip:2") is False
    st = approval.get_state(VALID_ID)
    assert st["summary"]["approved"] == 1
    assert st["summary"]["publishable"] == 1


def test_all_approved_true_only_when_every_piece_approved(iso):
    make_upload()
    make_clips()
    approval.decide(VALID_ID, "clip:1", approval.APPROVED)
    approval.decide(VALID_ID, "clip:2", approval.APPROVED)
    assert approval.get_state(VALID_ID)["summary"]["all_approved"] is True


def test_reject_then_reset(iso):
    make_upload()
    make_clips()
    approval.decide(VALID_ID, "clip:1", approval.REJECTED, note="fuera de foco")
    assert approval.get_state(VALID_ID)["summary"]["rejected"] == 1
    approval.decide(VALID_ID, "clip:1", approval.PENDING)
    assert approval.is_approved(VALID_ID, "clip:1") is False
    assert approval.get_state(VALID_ID)["summary"]["pending"] == 2


def test_decide_unknown_piece_raises(iso):
    make_upload()
    make_clips()
    with pytest.raises(approval.ApprovalError):
        approval.decide(VALID_ID, "clip:99", approval.APPROVED)


def test_decide_invalid_status_raises(iso):
    make_upload()
    make_clips()
    with pytest.raises(approval.ApprovalError):
        approval.decide(VALID_ID, "clip:1", "maybe")


def test_require_approved_guard(iso):
    make_upload()
    make_clips()
    with pytest.raises(approval.NotApprovedError):
        approval.require_approved(VALID_ID, "clip:1")
    approval.decide(VALID_ID, "clip:1", approval.APPROVED)
    approval.require_approved(VALID_ID, "clip:1")  # no levanta


# --------------------------------------------------------------------------- #
# Binding al contenido: una aprobación NO sobrevive a la regeneración (el fix del NO GO)
# --------------------------------------------------------------------------- #

def test_approval_goes_stale_when_content_regenerated(iso):
    make_upload()
    make_clips()
    approval.decide(VALID_ID, "clip:1", approval.APPROVED)
    assert approval.is_approved(VALID_ID, "clip:1") is True

    # regenerar los clips cambiando el contenido de clip:1 (nuevo título/momento)
    make_clips(clips=[
        {"id": 1, "score": 70, "title": "OTRO momento totalmente distinto"},
        {"id": 2, "score": 80, "title": "Insight clave"},
    ])
    # la aprobación vieja ya no vale: falla cerrado
    assert approval.is_approved(VALID_ID, "clip:1") is False
    with pytest.raises(approval.NotApprovedError):
        approval.require_approved(VALID_ID, "clip:1")
    st = {p["key"]: p["status"] for p in approval.get_state(VALID_ID)["pieces"]}
    assert st["clip:1"] == "stale"
    assert approval.get_state(VALID_ID)["summary"]["stale"] == 1


def test_reapproving_regenerated_content_clears_stale(iso):
    make_upload()
    make_clips()
    approval.decide(VALID_ID, "clip:1", approval.APPROVED)
    make_clips(clips=[{"id": 1, "score": 70, "title": "nuevo"}])
    assert approval.is_approved(VALID_ID, "clip:1") is False
    approval.decide(VALID_ID, "clip:1", approval.APPROVED)  # re-revisado y aprobado
    assert approval.is_approved(VALID_ID, "clip:1") is True


def test_captions_are_gated_pieces(iso):
    make_upload()
    _make_repurpose()
    net = config.REPURPOSE_NETWORKS[0]
    key = f"caption:{net}"
    assert approval.is_approved(VALID_ID, key) is False
    approval.decide(VALID_ID, key, approval.APPROVED)
    assert approval.is_approved(VALID_ID, key) is True


def test_note_too_long_raises(iso):
    make_upload()
    make_clips()
    with pytest.raises(approval.ApprovalError):
        approval.decide(VALID_ID, "clip:1", approval.REJECTED,
                        note="x" * (config.APPROVAL_NOTE_MAX_CHARS + 1))


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

def test_get_approvals_unknown_upload_404(iso, client):
    r = client.get(f"/uploads/{VALID_ID}/approvals")
    assert r.status_code == 404


def test_get_approvals_lists_pieces(iso, client):
    make_upload()
    make_clips()
    r = client.get(f"/uploads/{VALID_ID}/approvals")
    assert r.status_code == 200
    assert r.json()["summary"]["total"] == 2


def test_post_approve_piece_200(iso, client):
    make_upload()
    make_clips()
    r = client.post(f"/uploads/{VALID_ID}/approvals/clip:1?decision=approve")
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert approval.is_approved(VALID_ID, "clip:1") is True


def test_post_reject_with_note(iso, client):
    make_upload()
    make_clips()
    r = client.post(
        f"/uploads/{VALID_ID}/approvals/clip:1?decision=reject&note=mala+toma"
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert r.json()["note"] == "mala toma"


def test_post_invalid_decision_400(iso, client):
    make_upload()
    make_clips()
    r = client.post(f"/uploads/{VALID_ID}/approvals/clip:1?decision=publish")
    assert r.status_code == 400


def test_post_unknown_piece_400(iso, client):
    make_upload()
    make_clips()
    r = client.post(f"/uploads/{VALID_ID}/approvals/clip:99?decision=approve")
    assert r.status_code == 400
