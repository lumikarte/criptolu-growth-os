"""Tests del repropósito multi-formato (W-03, CRI-562): servicio + endpoints.

El LLM se mockea (se reemplaza el motor en ENGINES): no se hace ninguna llamada real.
"""

from __future__ import annotations

import json
import urllib.request
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import repurpose

from .conftest import VALID_ID, make_transcript, make_upload


def _valid_package() -> dict:
    """Un paquete bien formado como el que devolvería el LLM."""
    return {
        "carousel": {
            "title": "Cómo empezar en cripto sin fundirte",
            "slides": [
                {"heading": f"Slide {i}", "body": f"Cuerpo de la slide {i}."}
                for i in range(1, 7)
            ],
        },
        "feed_post": {
            "text": "La idea más fuerte del episodio, contada en un post.",
            "hashtags": ["#Cripto", "cripto", "Bitcoin", ""],  # dup + '#' + vacío
        },
        "thread": {"posts": ["Post 1 con gancho", "Post 2 encadenado", "Post 3 cierre"]},
        "captions": {n: f"caption {n}" for n in config.REPURPOSE_NETWORKS},
    }


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def mock_engine(monkeypatch):
    """Reemplaza el motor groq por uno que devuelve un paquete fijo. Devuelve el dict usado."""
    pkg = _valid_package()
    monkeypatch.setitem(repurpose.ENGINES, "groq", lambda prompt: pkg)
    return pkg


# --------------------------------------------------------------------------- #
# Servicio
# --------------------------------------------------------------------------- #

def test_repurpose_happy_path_persists_and_normalizes(iso, mock_engine):
    make_upload()
    make_transcript()
    out = repurpose.repurpose_upload(VALID_ID)

    assert out["engine"] == "groq"
    assert out["networks"] == list(config.REPURPOSE_NETWORKS)
    content = out["content"]
    # carrusel completo
    assert content["carousel"]["title"]
    assert len(content["carousel"]["slides"]) == 6
    # hashtags normalizados: sin '#', sin vacíos, deduplicados (Cripto/cripto = 1)
    assert content["feed_post"]["hashtags"] == ["Cripto", "Bitcoin"]
    # un caption por cada red pedida
    assert set(content["captions"]) == set(config.REPURPOSE_NETWORKS)
    # persistido en repurpose.json
    saved = json.loads((config.CLIPS_DIR / VALID_ID / "repurpose.json").read_text())
    assert saved["content"]["carousel"]["title"] == content["carousel"]["title"]
    # status actualizado en la metadata
    meta = json.loads((config.UPLOADS_DIR / VALID_ID / "meta.json").read_text())
    assert meta["status"] == "repurposed"


def test_repurpose_without_transcript_raises(iso):
    make_upload()
    with pytest.raises(repurpose.RepurposeError):
        repurpose.repurpose_upload(VALID_ID)


def test_repurpose_unknown_engine_raises(iso):
    make_upload()
    make_transcript()
    with pytest.raises(repurpose.RepurposeError):
        repurpose.repurpose_upload(VALID_ID, engine="gpt5")


def test_repurpose_too_few_slides_raises(iso, monkeypatch):
    make_upload()
    make_transcript()
    bad = _valid_package()
    bad["carousel"]["slides"] = bad["carousel"]["slides"][:2]  # menos del mínimo
    monkeypatch.setitem(repurpose.ENGINES, "groq", lambda prompt: bad)
    with pytest.raises(repurpose.RepurposeError):
        repurpose.repurpose_upload(VALID_ID)


def test_repurpose_missing_caption_network_raises(iso, monkeypatch):
    make_upload()
    make_transcript()
    bad = _valid_package()
    bad["captions"].pop(config.REPURPOSE_NETWORKS[0])  # falta una red
    monkeypatch.setitem(repurpose.ENGINES, "groq", lambda prompt: bad)
    with pytest.raises(repurpose.RepurposeError):
        repurpose.repurpose_upload(VALID_ID)


def test_engine_groq_non_json_content_maps_upstream(iso, monkeypatch):
    """Si Groq devuelve un envelope OK pero con contenido no-JSON, es 502 (UpstreamError)."""
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk_fake")

    class _Resp:
        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "esto no es json {"}}]}
            ).encode()

    @contextmanager
    def fake_urlopen(req, timeout=0):
        yield _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(repurpose.UpstreamError):
        repurpose._engine_groq("prompt")


def test_repurpose_slides_capped_at_max(iso, monkeypatch):
    make_upload()
    make_transcript()
    big = _valid_package()
    big["carousel"]["slides"] = [
        {"heading": f"H{i}", "body": f"B{i}"} for i in range(1, 20)
    ]
    monkeypatch.setitem(repurpose.ENGINES, "groq", lambda prompt: big)
    out = repurpose.repurpose_upload(VALID_ID)
    assert len(out["content"]["carousel"]["slides"]) == config.REPURPOSE_MAX_SLIDES


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

def test_post_repurpose_unknown_upload_404(iso, client):
    r = client.post(f"/uploads/{VALID_ID}/repurpose")
    assert r.status_code == 404


def test_post_repurpose_happy_path_200(iso, client, mock_engine):
    make_upload()
    make_transcript()
    r = client.post(f"/uploads/{VALID_ID}/repurpose")
    assert r.status_code == 200
    assert r.json()["content"]["thread"]["posts"]


def test_post_repurpose_no_transcript_maps_400(iso, client):
    make_upload()
    r = client.post(f"/uploads/{VALID_ID}/repurpose")
    assert r.status_code == 400


def test_post_repurpose_upstream_maps_502(iso, client, monkeypatch):
    make_upload()
    make_transcript()

    def boom(prompt):
        raise repurpose.UpstreamError("Groq caído")

    monkeypatch.setitem(repurpose.ENGINES, "groq", boom)
    r = client.post(f"/uploads/{VALID_ID}/repurpose")
    assert r.status_code == 502


def test_get_repurpose_before_generating_404(iso, client):
    make_upload()
    r = client.get(f"/uploads/{VALID_ID}/repurpose")
    assert r.status_code == 404


def test_get_repurpose_after_generating_200(iso, client, mock_engine):
    make_upload()
    make_transcript()
    client.post(f"/uploads/{VALID_ID}/repurpose")
    r = client.get(f"/uploads/{VALID_ID}/repurpose")
    assert r.status_code == 200
    assert set(r.json()["content"]["captions"]) == set(config.REPURPOSE_NETWORKS)
