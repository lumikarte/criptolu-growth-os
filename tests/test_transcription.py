"""Tests del downsample previo a transcribir (FP-MVP-03b, CRI-496).

FFmpeg/ffprobe se mockean: se valida que se transcribe el WAV/OGG downsampleado (no el
source), que el temporal se limpia siempre, y el manejo de errores (sin audio, binario
ausente, timeout, artefacto aún sobredimensionado).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app import config
from app.services import transcription

from .conftest import VALID_ID, make_upload


def _fake_engine(path, *, model, language):
    # Se debe transcribir el artefacto downsampleado (.ogg), nunca el source.
    assert path.suffix == config.AUDIO_DOWNSAMPLE_EXT
    return [{"id": 0, "start": 0.0, "end": 1.0, "text": "hola mundo", "words": []}]


@pytest.fixture
def fake_tools(monkeypatch):
    """Mockea _run: ffprobe dice que hay audio; ffmpeg 'crea' el .ogg downsampleado."""
    calls: list[list[str]] = []

    def fake_run(cmd, *, timeout):
        calls.append(cmd)
        if cmd[0] == "ffprobe":
            return "audio\n"
        if cmd[0] == "ffmpeg":
            Path(cmd[-1]).write_bytes(b"x" * 1024)  # ogg chico
            return ""
        return ""

    monkeypatch.setattr(transcription, "_run", fake_run)
    monkeypatch.setitem(transcription.ENGINES, "groq", _fake_engine)
    return calls


def test_transcribe_downsamples_cleans_and_persists(iso, fake_tools):
    make_upload(ext=".mp4")
    out = transcription.transcribe_upload(VALID_ID, language="es")

    assert out["n_segments"] == 1
    # Se llamó a ffmpeg para downsamplear y a ffprobe para chequear audio.
    assert any(c[0] == "ffmpeg" for c in fake_tools)
    assert any(c[0] == "ffprobe" for c in fake_tools)
    # Transcript persistido y metadata avanzada.
    assert (config.TRANSCRIPTS_DIR / VALID_ID / "transcript.json").is_file()
    meta = json.loads((config.UPLOADS_DIR / VALID_ID / "meta.json").read_text())
    assert meta["status"] == "transcribed"
    # El temporal se limpió.
    assert not list(config.TMP_DIR.glob("audio16k_*"))


def test_transcribe_no_audio_stream_raises(iso, monkeypatch):
    make_upload(ext=".mp4")
    monkeypatch.setattr(transcription, "_run", lambda cmd, *, timeout: "")  # ffprobe: sin audio
    with pytest.raises(transcription.TranscriptionError, match="no tiene pista de audio"):
        transcription.transcribe_upload(VALID_ID)


def test_transcribe_cleans_temp_on_engine_failure(iso, fake_tools, monkeypatch):
    make_upload(ext=".mp4")

    def boom(path, *, model, language):
        raise transcription.UpstreamError("Groq caído")

    monkeypatch.setitem(transcription.ENGINES, "groq", boom)
    with pytest.raises(transcription.UpstreamError):
        transcription.transcribe_upload(VALID_ID)
    assert not list(config.TMP_DIR.glob("audio16k_*"))  # temp limpiado pese al fallo


def test_transcribe_rejects_oversized_after_downsample(iso, fake_tools, monkeypatch):
    make_upload(ext=".mp4")
    monkeypatch.setattr(config, "GROQ_MAX_FILE_BYTES", 10)  # el ogg de 1024 lo supera
    with pytest.raises(transcription.TranscriptionError, match="tras el downsample"):
        transcription.transcribe_upload(VALID_ID)
    assert not list(config.TMP_DIR.glob("audio16k_*"))


def test_has_audio_stream_parses_ffprobe(iso, monkeypatch):
    monkeypatch.setattr(transcription, "_run", lambda cmd, *, timeout: "audio\n")
    assert transcription._has_audio_stream(Path("x")) is True
    monkeypatch.setattr(transcription, "_run", lambda cmd, *, timeout: "\n")
    assert transcription._has_audio_stream(Path("x")) is False


def test_run_missing_binary_maps_upstream():
    with pytest.raises(transcription.UpstreamError, match="No se encontró"):
        transcription._run(["definitely-not-a-real-binary-xyz"], timeout=5)


def test_run_timeout_maps_upstream(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1)

    monkeypatch.setattr(transcription.subprocess, "run", boom)
    with pytest.raises(transcription.UpstreamError, match="tiempo límite"):
        transcription._run(["ffmpeg", "-i", "x"], timeout=1)
