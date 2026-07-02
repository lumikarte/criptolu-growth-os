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

# Downsample previo a transcribir (FP-MVP-03b, CRI-496). FFmpeg extrae el audio a 16 kHz
# mono COMPRIMIDO antes de subirlo a Groq, para que un podcast largo entre en el tope y
# suba rápido. PCM no sirve (1 h ≈ 110 MB > tope); Opus 24 kbps ≈ 11 MB/h y la calidad ASR
# para voz es indistinguible. El temporal vive en TMP_DIR (disco real).
AUDIO_DOWNSAMPLE_RATE = 16000          # Hz, lo que espera Whisper
AUDIO_DOWNSAMPLE_CODEC = "libopus"     # comprimido; PCM no baja del límite de 100 MB
AUDIO_DOWNSAMPLE_BITRATE = "24k"       # ~11 MB/h, voz nítida para ASR
AUDIO_DOWNSAMPLE_EXT = ".ogg"          # contenedor Opus que Whisper acepta
FFMPEG_TIMEOUT = 900                   # s; downsamplear un episodio largo puede tardar
FFPROBE_TIMEOUT = 30                   # s; solo lee metadatos

# Clips (PP-MVP-02). FFmpeg corta cada momento en un video vertical 9:16 listo para
# Reels/TikTok/Shorts. Si el source tiene video se recorta a 9:16; si es solo-audio se
# genera un waveform sobre fondo para que el clip siga siendo postable (video).
CLIP_WIDTH = 1080
CLIP_HEIGHT = 1920
CLIP_PRESET = "veryfast"                 # x264: balance velocidad/calidad para MVP
CLIP_WAVE_COLOR = "0xC026D3"             # magenta CriptoLú para el waveform de audios
CLIP_BG_COLOR = "0x1A0033"               # morado profundo de fondo del waveform

# Subtítulos quemados en los clips (PP-MVP-04, CRI-498). SRT + force_style vía libass.
# El texto va SIEMPRE en el archivo SRT (nunca inline en el filtro) → sin superficie de
# inyección al filtergraph. Las líneas se re-chunkean desde los word-timestamps para que
# sean cortas y legibles en vertical (no el bloque largo típico de Whisper).
SUB_FONT = "DejaVu Sans"                 # fuente garantizada en el sistema (no usar Arial)
SUB_FONTSIZE = 18                        # unidades de libass; tunable
SUB_OUTLINE = 3                          # contorno grueso → legible sobre cualquier fondo
SUB_SHADOW = 1
SUB_MARGIN_V = 80                        # margen inferior (Alignment=2, centrado abajo)
SUB_MAX_CHARS = 32                       # corte de línea: ancho legible en 9:16
SUB_MAX_WORDS = 7                        # corte de línea: palabras por cue
SUB_MAX_DUR = 2.5                        # corte de línea: segundos por cue

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

# Repropósito multi-formato (W-03, CRI-562). El DIFERENCIAL: de 1 misma transcripción el
# LLM genera varios formatos de texto además de los clips — carrusel (slides), post de
# feed, hilo (X/Threads) y un caption por red. Motor enchufable igual que la detección
# (groq default / claude). El texto se valida y normaliza en código; el LLM solo redacta.
REPURPOSE_DEFAULT_ENGINE = "groq"
REPURPOSE_MIN_SLIDES = 5                  # carrusel: mínimo de slides útiles
REPURPOSE_MAX_SLIDES = 8                  # carrusel: máximo (se recorta si el LLM se pasa)
REPURPOSE_MAX_THREAD_POSTS = 8            # hilo: tope de posts
# El payload de respuesta es grande (carrusel + feed + hilo + N captions en un JSON); sin
# un tope holgado el LLM puede truncar y devolver JSON inválido. Holgado a propósito.
REPURPOSE_MAX_TOKENS = 6000
REPURPOSE_THREAD_CHAR_LIMIT = 280        # X/Threads: largo por post (solo aviso, no corta)
# Redes para las que se pide un caption adaptado (tono/largo/hashtags por red).
REPURPOSE_NETWORKS = ("tiktok", "reels", "shorts", "instagram_feed", "x")


def ensure_dirs() -> None:
    """Crea la estructura de carpetas de datos (idempotente). Se llama al arrancar."""
    for d in (UPLOADS_DIR, TRANSCRIPTS_DIR, CLIPS_DIR, EXPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    for sub in EXPORT_SUBDIRS:
        (EXPORTS_DIR / sub).mkdir(parents=True, exist_ok=True)
