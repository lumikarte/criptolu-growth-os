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

# Auto-crop 9:16 face-tracked (CRI-128). El crop actual toma la franja CENTRAL; con esto la
# x del crop se calcula sobre la cara detectada (YuNet/OpenCV, ONNX ~345 KB, sin torch/GPU).
# APAGADO por default: es la primera dep binaria y opcional (import perezoso). Ante cualquier
# fallo (sin OpenCV, sin cara, timeout) se cae al crop central de siempre (face_tracked=false).
AUTOCROP_ENABLED = os.environ.get("AUTOCROP_ENABLED") == "1"
AUTOCROP_MODEL = str(BASE_DIR / "app" / "models" / "face_detection_yunet_2023mar.onnx")
AUTOCROP_SAMPLE_FPS = 3.0                 # cuántos frames/s muestrear para detectar caras
AUTOCROP_MIN_SCORE = 0.6                  # confianza mínima YuNet para aceptar una cara
AUTOCROP_SMOOTH_ALPHA = 0.25              # EMA del paneo (0=congelado, 1=sigue crudo)
AUTOCROP_DEADZONE = 0.06                  # fracción de ancho: no mover si el centro varía menos
AUTOCROP_MODE = "smooth"                  # "static" (una x por clip) | "smooth" (paneo sendcmd)

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

# Diarización — quién habla (W-02, CRI-561). Groq transcribe (word-level) pero no diariza;
# esta capa enriquece transcript.json con un speaker por segmento/word. Motor enchufable:
#  - 'assemblyai' (default): cloud vía urllib, sin GPU ni token HF (re-transcribe y se usa
#    solo el speaker+tiempo, mapeado por solapamiento sobre los words de Groq).
#  - 'pyannote' (opcional): local, requiere torch + token HF + modelo gated (import perezoso).
DIARIZE_DEFAULT_ENGINE = "assemblyai"
ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY")
ASSEMBLYAI_BASE_URL = "https://api.assemblyai.com/v2"
ASSEMBLYAI_POLL_INTERVAL = 3             # s entre polls del estado de la transcripción
ASSEMBLYAI_MAX_POLLS = 200               # tope de polls (~10 min) antes de rendirse
HF_TOKEN = os.environ.get("HF_TOKEN")    # solo para el engine 'pyannote'
PYANNOTE_MODEL = "pyannote/speaker-diarization-community-1"

# Distribución multi-red (W-05, CRI-564). Publica en Postiz self-host SOLO las piezas que
# pasan el gate de aprobación (W-06), en modo BORRADOR. El gate vive en editorpro; Postiz es
# un "dumb sink". Auto-post directo (schedule/now) = fase 2, detrás de un flag apagado.
POSTIZ_BASE_URL = os.environ.get("POSTIZ_BASE_URL", "http://localhost:4007/public/v1")
POSTIZ_API_KEY = os.environ.get("POSTIZ_API_KEY")
POSTIZ_TIMEOUT = 60
DISTRIBUTION_DEFAULT_TYPE = "draft"
DISTRIBUTION_ALLOW_AUTOPOST = os.environ.get("DISTRIBUTION_ALLOW_AUTOPOST") == "1"
DISTRIBUTION_TYPES = ("draft", "schedule", "now")  # allowlist de type aceptados por Postiz
# Mapa red → id del canal conectado en Postiz. La API espera el id propio del canal, no el
# nombre de la red; en prod se completa (uno por app OAuth conectada). Vacío → usa el nombre
# de la red como placeholder (sirve para dev/tests; NO publica de verdad hasta completarlo).
POSTIZ_INTEGRATIONS: dict[str, str] = {}
# Qué piezas van a qué redes. Para clips, la red se aparea con su caption (caption:<red>),
# así el clip solo sale a una red si el clip Y su caption de esa red están aprobados.
DISTRIBUTION_TARGETS = {
    "clip": ("tiktok", "reels", "shorts"),
    "feed_post": ("instagram_feed", "facebook", "linkedin"),
    "thread": ("x", "threads"),
}

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

# Voz de marca (W-04, CRI-563). Perfil de 1 página (tono, hacé/no hagás, ejemplos) que se
# inyecta en cada caption para que el contenido suene como la marca — el moat, como prompt.
# Single-tenant: vive en un archivo editable (gitignored); si no existe, se usa el default.
BRAND_VOICE_FILE = DATA_DIR / "brand_voice.md"
BRAND_VOICE_MAX_CHARS = 8000              # tope defensivo del perfil (cabe de sobra en 1 pág)

# Gate de aprobación humana (W-06, CRI-565): tope del texto de la nota de decisión.
APPROVAL_NOTE_MAX_CHARS = 1000

# Render del carrusel a PNG (CRI-604). Materializa content.carousel de repurpose.json como
# slides de imagen con la identidad CriptoLú, listas para subir a IG/LinkedIn. Render propio
# con Pillow (sin browser headless): liviano, determinista, control total de marca.
CAROUSEL_WIDTH = 1080
CAROUSEL_HEIGHT = 1350                    # 4:5 — formato de carrusel recomendado IG/feed
CAROUSEL_BG_COLOR = "#1A0033"             # mismo morado de marca que CLIP_BG_COLOR
CAROUSEL_ACCENT_COLOR = "#C026D3"         # mismo magenta CriptoLú que CLIP_WAVE_COLOR
CAROUSEL_TEXT_COLOR = "#FFFFFF"
CAROUSEL_MUTED_COLOR = "#B98FD9"          # violeta suave para pie/numeración
CAROUSEL_MARGIN = 96                      # margen interior en px
# Fuentes bundleadas en el repo → render determinista, no depende de /usr/share/fonts del
# deploy. Se cae a la fuente por defecto de PIL si faltaran (con aviso).
CAROUSEL_FONT_BOLD = str(BASE_DIR / "app" / "assets" / "fonts" / "DejaVuSans-Bold.ttf")
CAROUSEL_FONT_REG = str(BASE_DIR / "app" / "assets" / "fonts" / "DejaVuSans.ttf")
CAROUSEL_TITLE_SIZE = 84                  # tamaño inicial del título (se auto-reduce si no entra)
CAROUSEL_HEADING_SIZE = 64
CAROUSEL_BODY_SIZE = 44
CAROUSEL_MIN_FONT_SIZE = 24               # piso del auto-shrink
# Versión de la plantilla visual. Bump → cambia el fingerprint de la pieza 'carousel' en el
# gate (W-06), así una aprobación previa caduca (stale) si se retoca el diseño de marca.
CAROUSEL_TEMPLATE_VERSION = "1.0"
DEFAULT_BRAND_VOICE = """\
# Voz de marca — CriptoLú

**Quién es:** CriptoLú enseña cripto en español LatAm de forma clara, cercana y sin humo.
Educa, no promete hacerse rico rápido.

**Tono:** cercano y directo, como una amiga que sabe del tema y te lo explica sin
condescendencia. Con energía, algo de humor, cero solemnidad de banco.

**Hacé:**
- Hablá en español LatAm natural (vos, no tú).
- Explicá el término técnico la primera vez que aparece.
- Priorizá lo práctico y accionable; ejemplos concretos.
- Sé honesta sobre el riesgo: cripto es volátil.

**No hagás:**
- No prometas rendimientos ni des consejos financieros ("esto va a subir").
- No uses jerga sin explicar ni traducciones robóticas del inglés.
- No metas miedo (FOMO) ni urgencia falsa.
- Nada de mayúsculas gritonas ni exceso de emojis.
"""


# Pipeline asíncrono con cola de jobs (CRI-603). POST /process encola y devuelve 202 con un
# job_id; el trabajo pesado (FFmpeg + LLM) corre en un worker aparte para no bloquear el
# request ni morir por timeout de proxy. Cola sobre Redis/Valkey vía RQ (sync, fork-per-job).
# El ESTADO/PROGRESO del job vive en data/jobs/<id>.json (fuente de verdad, sobrevive a
# caídas de Redis); Redis solo lleva la mecánica de cola.
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
JOBS_QUEUE_NAME = "podcast-pipeline"
JOBS_DIR = DATA_DIR / "jobs"
JOB_TIMEOUT = 30 * 60                    # s; tope duro por job (episodio largo + FFmpeg + LLM)
# Modo eager: ejecuta el job inline en el mismo proceso (sin Redis ni worker). Para dev/test.
JOBS_EAGER = os.environ.get("JOBS_EAGER") == "1"


def ensure_dirs() -> None:
    """Crea la estructura de carpetas de datos (idempotente). Se llama al arrancar."""
    for d in (UPLOADS_DIR, TRANSCRIPTS_DIR, CLIPS_DIR, EXPORTS_DIR, JOBS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    for sub in EXPORT_SUBDIRS:
        (EXPORTS_DIR / sub).mkdir(parents=True, exist_ok=True)
