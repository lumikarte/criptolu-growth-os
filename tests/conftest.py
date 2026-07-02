"""Fixtures de test: aíslan las carpetas de datos en un tmp y arman subidas falsas.

Las rutas del pipeline son atributos de ``app.config`` que los servicios leen en tiempo
de ejecución, así que monkeypatchearlas redirige todo el almacenamiento a un tmp por test
(sin tocar el ``data/`` real del repo).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import config
from app.services import storage

# Un upload_id válido es hex de 32 chars (ver storage.valid_upload_id).
VALID_ID = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Redirige UPLOADS/TRANSCRIPTS/CLIPS/EXPORTS_DIR a un tmp aislado por test."""
    up, tr, cl, ex, tmp = (
        tmp_path / "uploads", tmp_path / "transcripts",
        tmp_path / "clips", tmp_path / "exports", tmp_path / ".tmp",
    )
    for d in (up, tr, cl, ex, tmp):
        d.mkdir()
    for sub in config.EXPORT_SUBDIRS:
        (ex / sub).mkdir()
    monkeypatch.setattr(config, "UPLOADS_DIR", up)
    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", tr)
    monkeypatch.setattr(config, "CLIPS_DIR", cl)
    monkeypatch.setattr(config, "EXPORTS_DIR", ex)
    monkeypatch.setattr(config, "TMP_DIR", tmp)
    # Voz de marca (W-04): aislar el archivo del perfil para no tocar el data/ real.
    monkeypatch.setattr(config, "BRAND_VOICE_FILE", tmp_path / "brand_voice.md")
    return tmp_path


def make_upload(*, upload_id: str = VALID_ID, ext: str = ".mp4") -> str:
    """Crea data/uploads/<id>/ con un source falso y su meta.json. Devuelve el id."""
    dest = config.UPLOADS_DIR / upload_id
    dest.mkdir(parents=True, exist_ok=True)
    src = dest / f"source{ext}"
    src.write_bytes(b"fake-media-bytes")
    meta = {
        "id": upload_id,
        "original_filename": f"ep{ext}",
        "stored_path": str(src),
        "ext": ext,
        "size_bytes": src.stat().st_size,
        "status": "moments",
    }
    storage.atomic_write_text(dest / "meta.json", json.dumps(meta))
    return upload_id


def make_moments(*, upload_id: str = VALID_ID, clips: list[dict] | None = None) -> None:
    """Escribe data/clips/<id>/moments.json con los clips dados (o un set por defecto)."""
    if clips is None:
        clips = [
            {"id": 1, "start": 5.0, "end": 25.0, "duration": 20.0, "score": 90,
             "title": "Gancho", "reason": "r", "quote": "q"},
            {"id": 2, "start": 40.0, "end": 70.0, "duration": 30.0, "score": 80,
             "title": "Insight", "reason": "r", "quote": "q"},
        ]
    out_dir = config.CLIPS_DIR / upload_id
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"upload_id": upload_id, "n_clips": len(clips), "clips": clips}
    storage.atomic_write_text(out_dir / "moments.json", json.dumps(payload))


def make_transcript(*, upload_id: str = VALID_ID, segments: list[dict] | None = None) -> None:
    """Escribe data/transcripts/<id>/transcript.json (insumo de los subtítulos)."""
    if segments is None:
        segments = [{
            "id": 0, "start": 6.0, "end": 9.0, "text": "hola mundo esto es",
            "words": [
                {"start": 6.0, "end": 6.4, "word": "hola"},
                {"start": 6.4, "end": 6.9, "word": " mundo"},
                {"start": 7.5, "end": 8.0, "word": " esto"},
                {"start": 8.0, "end": 8.4, "word": " es"},
            ],
        }]
    out_dir = config.TRANSCRIPTS_DIR / upload_id
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"upload_id": upload_id, "language": "es", "segments": segments, "text": "x"}
    storage.atomic_write_text(out_dir / "transcript.json", json.dumps(payload))


def make_clips(*, upload_id: str = VALID_ID, clips: list[dict] | None = None) -> None:
    """Crea data/clips/<id>/clip_N.mp4 falsos + clips.json (insumo de export)."""
    if clips is None:
        clips = [
            {"id": 1, "score": 90, "title": "Gancho brutal"},
            {"id": 2, "score": 80, "title": "Insight clave"},
        ]
    out_dir = config.CLIPS_DIR / upload_id
    out_dir.mkdir(parents=True, exist_ok=True)
    enriched = []
    for c in clips:
        f = out_dir / f"clip_{c['id']}.mp4"
        f.write_bytes(b"fake-clip")
        enriched.append({**c, "filename": f.name, "file": str(f)})
    payload = {"upload_id": upload_id, "mode": "video", "n_clips": len(enriched),
               "clips": enriched}
    storage.atomic_write_text(out_dir / "clips.json", json.dumps(payload))
