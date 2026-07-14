"""Tests de la distribución multi-red (W-05, CRI-564). Postiz mockeado. El test central:
una pieza NO aprobada nunca genera un request a Postiz (el gate falla cerrado)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import approval, distribution, storage

from .conftest import AUTH_HEADERS, VALID_ID, make_clips, make_upload
from .test_repurpose import _valid_package


def _make_repurpose(upload_id: str = VALID_ID) -> None:
    out_dir = config.CLIPS_DIR / upload_id
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"upload_id": upload_id, "content": _valid_package()}
    storage.atomic_write_text(out_dir / "repurpose.json", json.dumps(payload))


@pytest.fixture
def client():
    with TestClient(app, headers=AUTH_HEADERS) as c:
        yield c


@pytest.fixture
def postiz(monkeypatch):
    """Mockea el cliente Postiz y registra las llamadas (para probar el gate)."""
    calls = {"posts": [], "uploads": []}

    def fake_upload(path):
        calls["uploads"].append(str(path))
        return "media/ref"

    def fake_post(payload):
        calls["posts"].append(payload)
        return "post_" + str(len(calls["posts"]))

    monkeypatch.setattr(distribution, "_upload_media", fake_upload)
    monkeypatch.setattr(distribution, "_create_post", fake_post)
    return calls


# --------------------------------------------------------------------------- #
# EL test central: nada sale sin aprobación
# --------------------------------------------------------------------------- #

def test_unapproved_pieces_never_hit_postiz(iso, postiz):
    make_upload()
    make_clips()
    _make_repurpose()
    out = distribution.distribute_upload(VALID_ID)
    # nada aprobado → cero requests a Postiz y todo en skipped
    assert postiz["posts"] == []
    assert postiz["uploads"] == []
    assert out["results"] == []
    assert len(out["skipped"]) > 0


def test_approved_clip_and_caption_is_published(iso, postiz):
    make_upload()
    make_clips()  # clip:1, clip:2
    _make_repurpose()
    approval.decide(VALID_ID, "clip:1", approval.APPROVED)
    approval.decide(VALID_ID, "caption:tiktok", approval.APPROVED)
    out = distribution.distribute_upload(VALID_ID, networks=["tiktok"])
    # clip:1 a tiktok debe salir (clip + caption aprobados); clip:2 no
    sent = {(r["piece_key"], r["network"]) for r in out["results"]}
    assert ("clip:1", "tiktok") in sent
    assert ("clip:2", "tiktok") not in sent
    assert len(postiz["posts"]) == 1
    assert len(postiz["uploads"]) == 1  # se subió el MP4 del clip aprobado


def test_clip_without_caption_approved_is_skipped(iso, postiz):
    make_upload()
    make_clips()
    _make_repurpose()
    approval.decide(VALID_ID, "clip:1", approval.APPROVED)  # falta caption:tiktok
    out = distribution.distribute_upload(VALID_ID, networks=["tiktok"])
    assert not any(r["piece_key"] == "clip:1" for r in out["results"])
    assert postiz["posts"] == []


def test_type_forced_to_draft_without_autopost(iso, postiz, monkeypatch):
    make_upload()
    _make_repurpose()
    approval.decide(VALID_ID, "feed_post", approval.APPROVED)
    monkeypatch.setattr(config, "DISTRIBUTION_ALLOW_AUTOPOST", False)
    out = distribution.distribute_upload(VALID_ID, post_type="now", networks=["instagram_feed"])
    assert out["type"] == "draft"  # forzado
    assert all(r["status"] == "draft" for r in out["results"])


def test_autopost_allowed_keeps_type(iso, postiz, monkeypatch):
    make_upload()
    _make_repurpose()
    approval.decide(VALID_ID, "feed_post", approval.APPROVED)
    monkeypatch.setattr(config, "DISTRIBUTION_ALLOW_AUTOPOST", True)
    out = distribution.distribute_upload(VALID_ID, post_type="now", networks=["facebook"])
    assert out["type"] == "now"


def test_clip_without_media_is_skipped(iso, postiz, monkeypatch):
    make_upload()
    make_clips()
    _make_repurpose()
    # un clip aprobado pero sin archivo (media=None) no debe generar un post sin video
    orig = distribution.clipping.load_clips

    def clips_no_file(uid):
        data = orig(uid)
        for c in data["clips"]:
            c["file"] = None
        return data

    monkeypatch.setattr(distribution.clipping, "load_clips", clips_no_file)
    approval.decide(VALID_ID, "clip:1", approval.APPROVED)
    approval.decide(VALID_ID, "caption:tiktok", approval.APPROVED)
    out = distribution.distribute_upload(VALID_ID, networks=["tiktok"])
    assert postiz["posts"] == []
    assert any(s["piece_key"] == "clip:1" and "media" in s["reason"] for s in out["skipped"])


def test_invalid_type_raises(iso, postiz, monkeypatch):
    make_upload()
    _make_repurpose()
    approval.decide(VALID_ID, "feed_post", approval.APPROVED)
    monkeypatch.setattr(config, "DISTRIBUTION_ALLOW_AUTOPOST", True)
    with pytest.raises(distribution.DistributionError):
        distribution.distribute_upload(VALID_ID, post_type="publish-now-lol",
                                       networks=["facebook"])


def test_integration_id_uses_mapping(iso, postiz, monkeypatch):
    make_upload()
    _make_repurpose()
    approval.decide(VALID_ID, "feed_post", approval.APPROVED)
    monkeypatch.setattr(config, "POSTIZ_INTEGRATIONS", {"facebook": "chan_real_123"})
    distribution.distribute_upload(VALID_ID, networks=["facebook"])
    assert postiz["posts"][0]["posts"][0]["integration"]["id"] == "chan_real_123"


def test_thread_published_as_array(iso, postiz):
    make_upload()
    _make_repurpose()
    approval.decide(VALID_ID, "thread", approval.APPROVED)
    distribution.distribute_upload(VALID_ID, networks=["x"])
    # el hilo va como value[] con un content por post
    payload = postiz["posts"][0]
    value = payload["posts"][0]["value"]
    assert len(value) == len(_valid_package()["thread"]["posts"])


def test_no_repurpose_raises(iso):
    make_upload()
    with pytest.raises(distribution.DistributionError):
        distribution.distribute_upload(VALID_ID)


def test_missing_postiz_key_maps_upstream(iso, monkeypatch):
    make_upload()
    _make_repurpose()
    approval.decide(VALID_ID, "feed_post", approval.APPROVED)
    monkeypatch.setattr(config, "POSTIZ_API_KEY", None)  # sin key
    with pytest.raises(distribution.UpstreamError):
        distribution.distribute_upload(VALID_ID, networks=["facebook"])


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

def test_distribute_unknown_upload_404(iso, client):
    r = client.post(f"/uploads/{VALID_ID}/distribute")
    assert r.status_code == 404


def test_distribute_happy_and_get(iso, client, postiz):
    make_upload()
    _make_repurpose()
    approval.decide(VALID_ID, "feed_post", approval.APPROVED)
    r = client.post(f"/uploads/{VALID_ID}/distribute?networks=facebook")
    assert r.status_code == 200
    assert any(res["piece_key"] == "feed_post" for res in r.json()["results"])
    g = client.get(f"/uploads/{VALID_ID}/distribution")
    assert g.status_code == 200


def test_distribute_no_repurpose_maps_400(iso, client):
    make_upload()
    r = client.post(f"/uploads/{VALID_ID}/distribute")
    assert r.status_code == 400


def test_get_distribution_before_404(iso, client):
    make_upload()
    r = client.get(f"/uploads/{VALID_ID}/distribution")
    assert r.status_code == 404
