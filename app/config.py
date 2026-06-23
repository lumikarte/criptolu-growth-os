"""Configuración central: metadata de la app y rutas de datos (CRI-256)."""

from __future__ import annotations

from pathlib import Path

APP_NAME = "Podcast Pro AI Studio"
APP_VERSION = "0.1.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# Carpetas del pipeline (cada etapa escribe en la suya).
UPLOADS_DIR = DATA_DIR / "uploads"          # FP-MVP-02: archivos subidos
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"  # FP-MVP-03: transcripciones
CLIPS_DIR = DATA_DIR / "clips"              # PP-MVP-02: clips cortados
EXPORTS_DIR = DATA_DIR / "exports"          # PP-MVP-03: export por plataforma
EXPORT_SUBDIRS = ("shorts", "reels", "tiktok")


def ensure_dirs() -> None:
    """Crea la estructura de carpetas de datos (idempotente). Se llama al arrancar."""
    for d in (UPLOADS_DIR, TRANSCRIPTS_DIR, CLIPS_DIR, EXPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    for sub in EXPORT_SUBDIRS:
        (EXPORTS_DIR / sub).mkdir(parents=True, exist_ok=True)
