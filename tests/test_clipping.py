"""Tests del servicio de corte de clips (PP-MVP-02).

FFmpeg/ffprobe se mockean: no se ejecuta nada real, se valida la lógica (modo
video/waveform, clamping de duración, índice escrito, manejo de errores y rutas).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app import config
from app.services import clipping

from .conftest import VALID_ID, make_moments, make_upload


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    """Reemplaza clipping._run: 'crea' el archivo de salida y registra los comandos."""
    calls: list[list[str]] = []

    def _fake(cmd: list[str], *, timeout=None) -> str:
        calls.append(cmd)
        if cmd[0] == "ffmpeg":
            Path(cmd[-1]).write_bytes(b"fake-clip")  # el último arg es el out path
        return ""

    monkeypatch.setattr(clipping, "_run", _fake)
    return calls


def _probe(monkeypatch, *, video: bool, audio: bool, duration: float = 100.0):
    monkeypatch.setattr(
        clipping, "probe_streams",
        lambda src: {"has_video": video, "has_audio": audio, "duration": duration},
    )


def test_cut_clips_video_mode(iso, monkeypatch, fake_ffmpeg):
    make_upload(ext=".mp4")
    make_moments()
    _probe(monkeypatch, video=True, audio=True)

    out = clipping.cut_clips(VALID_ID)

    assert out["mode"] == "video"
    assert out["n_clips"] == 2
    assert out["width"] == 1080 and out["height"] == 1920
    # Los archivos de clip y el índice existen.
    clip_dir = config.CLIPS_DIR / VALID_ID
    assert (clip_dir / "clip_1.mp4").is_file()
    assert (clip_dir / "clip_2.mp4").is_file()
    assert (clip_dir / "clips.json").is_file()
    # El comando de video recorta a 9:16 (filtro crop).
    assert any("crop=1080:1920" in " ".join(c) for c in fake_ffmpeg)
    # La metadata avanzó a 'clips'.
    meta = json.loads((config.UPLOADS_DIR / VALID_ID / "meta.json").read_text())
    assert meta["status"] == "clips"
    assert meta["clips"]["mode"] == "video"


def test_cut_clips_waveform_mode_for_audio_only(iso, monkeypatch, fake_ffmpeg):
    make_upload(ext=".mp3")
    make_moments()
    _probe(monkeypatch, video=False, audio=True)

    out = clipping.cut_clips(VALID_ID)

    assert out["mode"] == "waveform"
    assert out["n_clips"] == 2
    # El waveform usa el filtro showwaves.
    assert any("showwaves" in " ".join(c) for c in fake_ffmpeg)


def test_cut_clips_clamps_end_to_source_duration(iso, monkeypatch, fake_ffmpeg):
    make_upload()
    # Un clip dentro de la duración (end 25) y otro que excede (end 70 > dur 30).
    make_moments(clips=[
        {"id": 1, "start": 5.0, "end": 25.0, "score": 90, "title": "A"},
        {"id": 2, "start": 40.0, "end": 70.0, "score": 80, "title": "B"},
    ])
    _probe(monkeypatch, video=True, audio=True, duration=30.0)

    out = clipping.cut_clips(VALID_ID)

    # El clip 2 arranca en 40 > 30 (duración), así que se omite; queda solo el 1.
    assert out["n_clips"] == 1
    assert out["clips"][0]["id"] == 1
    assert out["clips"][0]["end"] == 25.0


def test_cut_clips_without_moments_raises(iso):
    make_upload()  # sin moments.json
    with pytest.raises(clipping.ClipError, match="momentos"):
        clipping.cut_clips(VALID_ID)


def test_cut_clips_unknown_upload_raises(iso):
    with pytest.raises(clipping.ClipError, match="no encontrada"):
        clipping.cut_clips(VALID_ID)


def test_cut_clips_invalid_moment_raises_clip_error(iso, monkeypatch, fake_ffmpeg):
    make_upload()
    # moments.json manipulado: clip sin 'end' → ClipError (400), no KeyError/500.
    make_moments(clips=[{"id": 1, "start": 5.0, "score": 90, "title": "A"}])
    _probe(monkeypatch, video=True, audio=True)
    with pytest.raises(clipping.ClipError, match="clip inválido"):
        clipping.cut_clips(VALID_ID)


def test_cut_clips_cleans_stale_clips(iso, monkeypatch, fake_ffmpeg):
    make_upload()
    make_moments(clips=[{"id": 1, "start": 5.0, "end": 25.0, "score": 90, "title": "A"}])
    _probe(monkeypatch, video=True, audio=True)
    # Dejar un clip viejo de una corrida previa con id alto que ya no se produce.
    stale = config.CLIPS_DIR / VALID_ID / "clip_9.mp4"
    stale.write_bytes(b"old")
    clipping.cut_clips(VALID_ID)
    assert not stale.exists()  # se limpió
    assert (config.CLIPS_DIR / VALID_ID / "clip_1.mp4").is_file()


def test_cut_clips_no_usable_stream_raises(iso, monkeypatch, fake_ffmpeg):
    make_upload()
    make_moments()
    _probe(monkeypatch, video=False, audio=False)
    with pytest.raises(clipping.ClipError, match="no tiene pista"):
        clipping.cut_clips(VALID_ID)


def test_run_raises_upstream_when_ffmpeg_missing(iso):
    # _run real contra un binario inexistente debe dar UpstreamError (no un crash).
    with pytest.raises(clipping.UpstreamError, match="No se encontró"):
        clipping._run(["definitely-not-a-real-binary-xyz", "-version"], timeout=5)


def test_run_timeout_maps_upstream(monkeypatch):
    # Un ffmpeg colgado (TimeoutExpired) debe dar UpstreamError 502, no colgar el worker.
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1)

    monkeypatch.setattr(clipping.subprocess, "run", boom)
    with pytest.raises(clipping.UpstreamError, match="tiempo límite"):
        clipping._run(["ffmpeg", "-i", "x"], timeout=1)


def test_load_clips_invalid_id_returns_none(iso):
    assert clipping.load_clips("../etc/passwd") is None
    assert clipping.load_clips(VALID_ID) is None  # todavía no hay clips.json
