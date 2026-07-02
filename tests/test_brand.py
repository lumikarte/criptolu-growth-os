"""Tests de la voz de marca (W-04, CRI-563): servicio, endpoints e inyección en repurpose."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import brand, repurpose

from .conftest import VALID_ID, make_transcript, make_upload
from .test_repurpose import _valid_package


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- #
# Servicio
# --------------------------------------------------------------------------- #

def test_load_returns_default_when_no_file(iso):
    assert not brand.is_customized()
    assert brand.load_brand_voice() == config.DEFAULT_BRAND_VOICE.strip()


def test_save_then_load_roundtrip(iso):
    brand.save_brand_voice("  # Mi marca\nHablá canchero.  ")
    assert brand.is_customized()
    assert brand.load_brand_voice() == "# Mi marca\nHablá canchero."


def test_save_empty_raises(iso):
    with pytest.raises(brand.BrandError):
        brand.save_brand_voice("   ")


def test_save_too_long_raises(iso):
    with pytest.raises(brand.BrandError):
        brand.save_brand_voice("x" * (config.BRAND_VOICE_MAX_CHARS + 1))


def test_empty_file_counts_as_default(iso):
    config.BRAND_VOICE_FILE.write_text("   \n  ", encoding="utf-8")
    assert not brand.is_customized()
    assert brand.load_brand_voice() == config.DEFAULT_BRAND_VOICE.strip()


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

def test_get_brand_voice_default(iso, client):
    r = client.get("/brand-voice")
    assert r.status_code == 200
    assert r.json()["customized"] is False
    assert "CriptoLú" in r.json()["voice"]


def test_put_then_get_brand_voice(iso, client):
    r = client.put("/brand-voice", json={"voice": "# Voz propia\nSé directa."})
    assert r.status_code == 200
    assert r.json()["customized"] is True
    r2 = client.get("/brand-voice")
    assert r2.json()["voice"] == "# Voz propia\nSé directa."
    assert r2.json()["customized"] is True


def test_put_empty_brand_voice_400(iso, client):
    r = client.put("/brand-voice", json={"voice": "  "})
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# Inyección en el repropósito
# --------------------------------------------------------------------------- #

def test_repurpose_injects_saved_brand_voice(iso, monkeypatch):
    make_upload()
    make_transcript()
    brand.save_brand_voice("# Marca test\nDECÍ SIEMPRE 'che'.")

    seen = {}

    def spy(prompt):
        seen["prompt"] = prompt
        return _valid_package()

    monkeypatch.setitem(repurpose.ENGINES, "groq", spy)
    out = repurpose.repurpose_upload(VALID_ID)

    assert "DECÍ SIEMPRE 'che'." in seen["prompt"]
    assert out["brand_voice"] == {"applied": True, "customized": True}


def test_repurpose_uses_default_voice_when_none_saved(iso, monkeypatch):
    make_upload()
    make_transcript()
    seen = {}
    monkeypatch.setitem(
        repurpose.ENGINES, "groq",
        lambda prompt: (seen.update(prompt=prompt) or _valid_package()),
    )
    out = repurpose.repurpose_upload(VALID_ID)
    assert "VOZ DE MARCA" in seen["prompt"]  # el default también se inyecta
    assert out["brand_voice"]["applied"] is True
    assert out["brand_voice"]["customized"] is False
