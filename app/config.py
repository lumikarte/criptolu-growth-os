"""Configuración central: metadata de la app y rutas de datos (CRI-256)."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "Podcast Pro AI Studio"
APP_VERSION = "0.1.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


def _load_dotenv() -> None:
    """Carga editorpro/.env en os.environ (sin pisar variables ya seteadas).

    Loader mínimo para no agregar dependencias; el .env real está gitignored.
    """
    envp = BASE_DIR / ".env"
    if not envp.is_file():
        return
    for line in envp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# Carpetas del pipeline (cada etapa escribe en la suya).
UPLOADS_DIR = DATA_DIR / "uploads"          # FP-MVP-02: archivos subidos
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"  # FP-MVP-03: transcripciones
CLIPS_DIR = DATA_DIR / "clips"              # PP-MVP-02: clips cortados
EXPORTS_DIR = DATA_DIR / "exports"          # PP-MVP-03: export por plataforma
EXPORT_SUBDIRS = ("shorts", "reels", "tiktok")

# Temporales en DISCO REAL. /tmp es un tmpfs (RAM) con cuota que se llena y rompe
# escrituras (uploads grandes spoolean a tempfile). Forzamos tempdir acá.
TMP_DIR = BASE_DIR / ".tmp"

# Validación de subidas (FP-MVP-02, CRI-259)
ALLOWED_UPLOAD_EXT = {
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac",   # audio
    ".mp4", ".mov", ".mkv", ".webm",                    # video
}
MAX_UPLOAD_BYTES = 2 * 1024 ** 3  # 2 GB

# Transcripción (FP-MVP-03). Motor activo: Groq (whisper-large-v3).
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"
DEFAULT_LANGUAGE = "es"
# Límite de tamaño de archivo de la API de Groq (~100 MB en tier dev). Avisamos antes
# de subir para dar un error claro en vez de un 413 críptico. El downsample a 16 kHz
# (que achicaría episodios largos) llega con FFmpeg en una etapa posterior.
GROQ_MAX_FILE_BYTES = 100 * 1024 ** 2

# Detección de mejores momentos (PP-MVP-01). Motor enchufable: Groq (default, reusa la
# key de transcripción) o Claude (requiere ANTHROPIC_API_KEY, mejor criterio editorial).
DETECT_DEFAULT_ENGINE = "groq"
DETECT_N_CLIPS = 5
DETECT_MIN_SEC = 15
DETECT_MAX_SEC = 60
# Groq como LLM (chat completions, API compatible con OpenAI).
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_LLM_MODEL = "llama-3.3-70b-versatile"
# Claude (API de Anthropic). Se usa el modelo más capaz por defecto.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
CLAUDE_MODEL = "claude-opus-4-8"


def ensure_dirs() -> None:
    """Crea la estructura de carpetas de datos (idempotente). Se llama al arrancar."""
    for d in (UPLOADS_DIR, TRANSCRIPTS_DIR, CLIPS_DIR, EXPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    for sub in EXPORT_SUBDIRS:
        (EXPORTS_DIR / sub).mkdir(parents=True, exist_ok=True)
