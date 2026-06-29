"""Transcripción de subidas con timestamps (FP-MVP-03).

Motor activo: API de Groq (whisper-large-v3). Sin dependencias extra: usa urllib y
arma el multipart a mano. Toma el archivo guardado de una subida
(``data/uploads/<id>/source.<ext>``) y produce, en ``data/transcripts/<id>/``:
  - transcript.json  (segmentos con start/end + palabras con timestamps + texto plano)
  - transcript.srt   (subtítulos para previsualizar)

El motor es enchufable (``ENGINES``): mañana se puede sumar uno local o self-host que
devuelva la misma estructura de segmentos sin tocar el resto del pipeline.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from .. import config
from . import storage

# Groq está detrás de Cloudflare y bloquea el User-Agent por defecto de Python
# (error 1010). Con un UA de navegador las requests pasan.
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Content-Type por extensión (Whisper igualmente detecta por el filename).
_CONTENT_TYPES = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac",
    ".mp4": "video/mp4", ".mov": "video/quicktime",
    ".mkv": "video/x-matroska", ".webm": "video/webm",
}


class TranscriptionError(RuntimeError):
    """Falla de validación al transcribir (se traduce a HTTP 400 en el router)."""


class UpstreamError(TranscriptionError):
    """Falla de la dependencia externa (Groq caído, red, key faltante) → HTTP 502."""


def _run(cmd: list[str], *, timeout: int) -> str:
    """Ejecuta ffmpeg/ffprobe y devuelve stdout; mapea fallas a UpstreamError (502).

    Distingue binario ausente, timeout (cuelgue) y fallo de ejecución, para no terminar en
    un 500 críptico. ``subprocess.TimeoutExpired`` no es subclase de CalledProcessError, así
    que necesita su propio handler.
    """
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=timeout
        )
    except FileNotFoundError as e:
        raise UpstreamError(
            f"No se encontró '{cmd[0]}'. ¿Está instalado ffmpeg? (apt install ffmpeg)"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise UpstreamError(f"'{cmd[0]}' excedió el tiempo límite ({timeout}s).") from e
    except subprocess.CalledProcessError as e:
        raise UpstreamError(f"Falló {cmd[0]}: {(e.stderr or '').strip()[:400]}") from e
    return out.stdout


def _has_audio_stream(src: Path) -> bool:
    """True si el archivo tiene al menos una pista de audio (ffprobe)."""
    raw = _run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(src),
        ],
        timeout=config.FFPROBE_TIMEOUT,
    )
    return "audio" in raw


def _downsample_16k(src: Path, dst: Path) -> None:
    """Extrae el audio a 16 kHz mono comprimido (Opus) — el insumo que sube a Groq."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg", "-y", "-i", str(src),
            "-vn", "-ac", "1", "-ar", str(config.AUDIO_DOWNSAMPLE_RATE),
            "-c:a", config.AUDIO_DOWNSAMPLE_CODEC, "-b:a", config.AUDIO_DOWNSAMPLE_BITRATE,
            str(dst),
        ],
        timeout=config.FFMPEG_TIMEOUT,
    )


def _fmt_ts(seconds: float) -> str:
    """Segundos -> 'HH:MM:SS,mmm' (formato de tiempo SRT)."""
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(segments: list[dict]) -> str:
    """Convierte segmentos a texto SRT."""
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_fmt_ts(seg['start'])} --> {_fmt_ts(seg['end'])}")
        lines.append(seg["text"].strip())
        lines.append("")
    return "\n".join(lines)


def _multipart(
    fields: list[tuple[str, str]], file_field: str, filename: str,
    file_bytes: bytes, content_type: str,
) -> tuple[str, bytes]:
    """Arma un cuerpo multipart/form-data. `fields` admite claves repetidas."""
    boundary = "----podcastpro" + uuid4().hex
    parts = []
    for k, v in fields:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
        f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode()
    )
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return boundary, b"".join(parts)


def _engine_groq(path: Path, *, model: str, language: str | None) -> list[dict]:
    """Transcribe un archivo con la API de Groq y devuelve segmentos con word-timestamps."""
    if not config.GROQ_API_KEY:
        raise UpstreamError(
            "Falta GROQ_API_KEY. Ponela en editorpro/.env (GROQ_API_KEY=gsk_...)."
        )

    ext = path.suffix.lower()
    content_type = _CONTENT_TYPES.get(ext, "application/octet-stream")
    fields = [
        ("model", model),
        ("response_format", "verbose_json"),
        ("temperature", "0"),
        ("timestamp_granularities[]", "segment"),
        ("timestamp_granularities[]", "word"),
    ]
    if language:
        fields.append(("language", language))

    boundary, body = _multipart(fields, "file", path.name, path.read_bytes(), content_type)
    req = urllib.request.Request(
        config.GROQ_API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {config.GROQ_API_KEY}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": _BROWSER_UA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise UpstreamError(f"Groq HTTP {e.code}: {e.read().decode()[:300]}") from e
    except urllib.error.URLError as e:
        raise UpstreamError(f"Red al contactar Groq: {e}") from e

    all_words = data.get("words") or []
    segments: list[dict] = []
    for s in data.get("segments", []):
        s0, s1 = s["start"], s["end"]
        words = [
            {"start": round(w["start"], 3), "end": round(w["end"], 3), "word": w.get("word", "")}
            for w in all_words
            if w.get("start", -1) >= s0 - 1e-6 and w.get("end", -1) <= s1 + 1e-6
        ]
        segments.append({
            "id": s.get("id"),
            "start": round(s0, 3),
            "end": round(s1, 3),
            "text": s["text"].strip(),
            "words": words,
        })
    return segments


ENGINES = {
    "groq": _engine_groq,  # API Groq (whisper-large-v3) — motor activo
}


def transcribe_upload(
    upload_id: str, *, language: str | None = None, engine: str = "groq",
) -> dict:
    """Transcribe una subida ya registrada y guarda transcript.json + transcript.srt.

    Devuelve un resumen {upload_id, engine, model, n_segments, chars, ...}.
    Lanza TranscriptionError si la subida no existe, el archivo falta, es demasiado
    grande para Groq o el motor falla.
    """
    meta = storage.load_metadata(upload_id)
    if meta is None:
        raise TranscriptionError(f"Subida '{upload_id}' no encontrada.")

    src = Path(meta["stored_path"])
    if not src.is_file():
        raise TranscriptionError(f"Falta el archivo de la subida '{upload_id}': {src}")

    if engine not in ENGINES:
        raise TranscriptionError(
            f"Motor desconocido '{engine}'. Disponibles: {', '.join(ENGINES)}"
        )

    if not _has_audio_stream(src):
        raise TranscriptionError(
            f"La subida '{upload_id}' no tiene pista de audio; no se puede transcribir."
        )

    lang = language if language is not None else config.DEFAULT_LANGUAGE
    model = config.GROQ_MODEL

    # Downsample a 16 kHz mono comprimido antes de subir: así un episodio largo entra en el
    # tope de Groq (el source de hasta 2 GB se acepta; lo que viaja es el .ogg chico). El
    # límite se reevalúa sobre el artefacto comprimido. Temp único en TMP_DIR, limpiado sí o sí.
    audio16k = config.TMP_DIR / f"audio16k_{uuid4().hex}{config.AUDIO_DOWNSAMPLE_EXT}"
    try:
        _downsample_16k(src, audio16k)
        size = audio16k.stat().st_size
        if size > config.GROQ_MAX_FILE_BYTES:
            raise TranscriptionError(
                f"El audio sigue demasiado grande tras el downsample "
                f"({size // (1024 ** 2)} MB): supera el máximo de "
                f"{config.GROQ_MAX_FILE_BYTES // (1024 ** 2)} MB de la API de Groq."
            )
        segments = ENGINES[engine](audio16k, model=model, language=lang)
    finally:
        audio16k.unlink(missing_ok=True)

    full_text = " ".join(s["text"] for s in segments).strip()

    transcript = {
        "upload_id": upload_id,
        "engine": engine,
        "model": model,
        "language": lang,
        "segments": segments,
        "text": full_text,
    }
    out_dir = config.TRANSCRIPTS_DIR / upload_id
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "transcript.json"
    srt_path = out_dir / "transcript.srt"
    storage.atomic_write_text(
        json_path, json.dumps(transcript, ensure_ascii=False, indent=2)
    )
    storage.atomic_write_text(srt_path, to_srt(segments))

    storage.update_metadata(upload_id, {
        "status": "transcribed",
        "transcript": {
            "json": str(json_path),
            "srt": str(srt_path),
            "n_segments": len(segments),
            "language": lang,
        },
    })

    return {
        "upload_id": upload_id,
        "engine": engine,
        "model": model,
        "language": lang,
        "n_segments": len(segments),
        "chars": len(full_text),
    }


def load_transcript(upload_id: str) -> dict | None:
    """Devuelve el transcript.json de una subida, o None si no existe (o el id es inválido)."""
    if not storage.valid_upload_id(upload_id):
        return None
    json_path = config.TRANSCRIPTS_DIR / upload_id / "transcript.json"
    if not json_path.is_file():
        return None
    return json.loads(json_path.read_text(encoding="utf-8"))
