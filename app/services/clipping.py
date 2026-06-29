"""Corte de clips verticales con FFmpeg (PP-MVP-02).

Toma los momentos detectados (``data/clips/<id>/moments.json``) y el archivo original
de la subida, y corta cada momento en un video vertical 9:16 listo para publicar en
TikTok/Reels/Shorts. Escribe ``data/clips/<id>/clip_<n>.mp4`` + un índice ``clips.json``.

Decisión audio-vs-video (un clip 9:16 ES video):
  - source con pista de VIDEO  → recorte/relleno a 1080x1920 re-encodeando (no ``-c copy``,
    que cortaría en keyframes y derivaría los tiempos).
  - source SOLO audio          → se genera un waveform (``showwaves``) sobre un fondo de
    marca, así el clip sigue siendo un video posteable.
  - source sin pista usable     → se falla cerrado con un error claro.

Sin dependencias extra: usa ffmpeg/ffprobe por subprocess (ya requeridos por el proyecto).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import uuid4

from .. import config
from . import detection, storage, transcription


class ClipError(RuntimeError):
    """Falla de validación al cortar clips (se traduce a HTTP 400 en el router)."""


class UpstreamError(ClipError):
    """Falla de la herramienta externa (ffmpeg/ffprobe ausente o que falla) → HTTP 502."""


def _run(cmd: list[str], *, timeout: int) -> str:
    """Ejecuta un comando y devuelve stdout; aborta con mensaje claro si falla.

    Distingue 'no está instalado', 'colgado' (timeout) y 'corrió y falló' para dar un 502
    entendible en vez de un 500 críptico (mismo criterio que transcription._run).
    ``subprocess.TimeoutExpired`` no es subclase de CalledProcessError → handler propio.
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


def probe_streams(src: Path) -> dict:
    """Inspecciona el archivo con ffprobe: ``{has_video, has_audio, duration}``."""
    raw = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(src),
    ], timeout=config.FFPROBE_TIMEOUT)
    data = json.loads(raw)
    streams = data.get("streams", [])
    duration = float(data.get("format", {}).get("duration", 0.0) or 0.0)
    return {
        "has_video": any(s.get("codec_type") == "video" for s in streams),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "duration": round(duration, 3),
    }


def _escape_sub_path(path: Path) -> str:
    """Escapa la ruta del .srt para el parser de filtros de FFmpeg (subtitles=).

    El texto del caption NUNCA viaja por acá (va en el cuerpo del SRT); solo la ruta y el
    force_style, que son constantes nuestras. Igual escapamos `\\`, `:` y `'` como defensa.
    """
    s = str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return s


def _subtitles_filter(srt: Path) -> str:
    """Construye el filtro `subtitles=` con el estilo 9:16 (force_style)."""
    style = (
        f"FontName={config.SUB_FONT},FontSize={config.SUB_FONTSIZE},"
        f"Outline={config.SUB_OUTLINE},Shadow={config.SUB_SHADOW},"
        f"Alignment=2,MarginV={config.SUB_MARGIN_V}"
    )
    return f"subtitles='{_escape_sub_path(srt)}':force_style='{style}'"


def _video_cmd(src: Path, start: float, dur: float, out: Path, srt: Path | None = None) -> list[str]:
    """Recorta [start, start+dur] y reescala/recorta a 9:16 manteniendo el audio.

    Si `srt` viene, quema los subtítulos AL FINAL del filtro (sobre los frames 1080×1920,
    así FontSize/MarginV están en píxeles de salida).
    """
    w, h = config.CLIP_WIDTH, config.CLIP_HEIGHT
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1"
    )
    if srt is not None:
        vf += "," + _subtitles_filter(srt)
    return [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", config.CLIP_PRESET, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out),
    ]


def _waveform_cmd(src: Path, start: float, dur: float, out: Path, srt: Path | None = None) -> list[str]:
    """Genera un video 9:16 con waveform de marca sobre fondo (para sources solo-audio)."""
    w, h = config.CLIP_WIDTH, config.CLIP_HEIGHT
    # Fondo sólido de marca + waveform centrado encima; el audio se recorta con -ss/-t.
    chain = (
        f"[bg][wave]overlay=(W-w)/2:(H-h)/2:shortest=1,format=yuv420p"
    )
    if srt is not None:
        chain += "," + _subtitles_filter(srt)
    filt = (
        f"color=c={config.CLIP_BG_COLOR}:s={w}x{h}:d={dur:.3f}[bg];"
        f"[0:a]showwaves=s={w}x{int(h / 3)}:mode=cline:colors={config.CLIP_WAVE_COLOR}[wave];"
        f"{chain}[v]"
    )
    return [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
        "-filter_complex", filt,
        "-map", "[v]", "-map", "0:a",
        "-c:v", "libx264", "-preset", config.CLIP_PRESET,
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out),
    ]


def _caption_cues(transcript: dict, clip_start: float, clip_end: float) -> list[dict]:
    """Arma cues de subtítulo (0-based al clip) re-chunkeando los word-timestamps.

    Toma las palabras dentro de [clip_start, clip_end], las agrupa en líneas cortas
    (por nº de palabras, caracteres o duración) y desplaza los tiempos restando clip_start
    para que sean relativos al clip ya recortado. Si no hay words, cae a nivel de segmento.
    """
    dur = clip_end - clip_start

    def _shift(t: float) -> float:
        return round(min(max(t - clip_start, 0.0), dur), 3)

    words: list[tuple[float, float, str]] = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []):
            ws, we = w.get("start"), w.get("end")
            txt = (w.get("word") or "").strip()
            if ws is None or we is None or not txt:
                continue
            if we <= clip_start or ws >= clip_end:  # fuera de la ventana
                continue
            words.append((ws, we, txt))

    cues: list[dict] = []
    if words:
        group: list[tuple[float, float, str]] = []
        for ws, we, txt in words:
            tentative = " ".join(t for _, _, t in group + [(ws, we, txt)])
            span = we - group[0][0] if group else 0.0
            # Cerrar la línea actual antes de agregar si se pasaría de los límites.
            if group and (
                len(group) >= config.SUB_MAX_WORDS
                or len(tentative) > config.SUB_MAX_CHARS
                or span > config.SUB_MAX_DUR
            ):
                cues.append({
                    "start": _shift(group[0][0]),
                    "end": _shift(group[-1][1]),
                    "text": " ".join(t for _, _, t in group),
                })
                group = []
            group.append((ws, we, txt))
        if group:
            cues.append({
                "start": _shift(group[0][0]),
                "end": _shift(group[-1][1]),
                "text": " ".join(t for _, _, t in group),
            })
    else:
        # Fallback sin word-timestamps: una cue por segmento solapado con la ventana.
        for seg in transcript.get("segments", []):
            ss, se = seg.get("start"), seg.get("end")
            txt = (seg.get("text") or "").strip()
            if ss is None or se is None or not txt:
                continue
            if se <= clip_start or ss >= clip_end:
                continue
            cues.append({"start": _shift(ss), "end": _shift(se), "text": txt})

    # Descartar cues degeneradas (start >= end tras el clamp).
    return [c for c in cues if c["end"] > c["start"]]


def cut_clips(upload_id: str, *, subtitles: bool | None = None) -> dict:
    """Corta en clips 9:16 los momentos detectados de una subida. Devuelve el índice.

    Lee ``moments.json``, decide modo video/waveform según el source y escribe cada clip
    en ``data/clips/<id>/clip_<n>.mp4`` más un ``clips.json``. Lanza ClipError si no hay
    momentos o el source no sirve; UpstreamError si ffmpeg/ffprobe no está o falla.

    ``subtitles`` controla los captions quemados (PP-MVP-04):
      - None  (default): se queman si hay transcripción; si no, se omiten con aviso.
      - True  (explícito): se exige transcripción → ClipError si falta.
      - False: nunca se queman.
    """
    meta = storage.load_metadata(upload_id)
    if meta is None:
        raise ClipError(f"Subida '{upload_id}' no encontrada.")

    moments = detection.load_moments(upload_id)
    if moments is None:
        raise ClipError(
            f"La subida '{upload_id}' no tiene momentos detectados. Corré /detect primero."
        )
    clips_in = moments.get("clips") or []
    if not clips_in:
        raise ClipError("No hay momentos para cortar (moments.json sin clips).")

    src = Path(meta["stored_path"])
    if not src.is_file():
        raise ClipError(f"Falta el archivo de la subida '{upload_id}': {src}")

    info = probe_streams(src)
    if info["has_video"]:
        mode, build = "video", _video_cmd
    elif info["has_audio"]:
        mode, build = "waveform", _waveform_cmd
    else:
        raise ClipError(
            "El archivo no tiene pista de video ni de audio usable; no se puede cortar."
        )

    # Política de subtítulos: quemarlos requiere transcripción. Si se pidieron explícitos y
    # falta, error claro; si es el default, se omiten sin romper.
    want_subs = subtitles is not False
    transcript = transcription.load_transcript(upload_id) if want_subs else None
    if want_subs and transcript is None:
        if subtitles is True:
            raise ClipError(
                f"Se pidieron subtítulos pero la subida '{upload_id}' no tiene "
                f"transcripción. Transcribila primero (/transcribe)."
            )
        want_subs = False  # default sin transcript → cortar sin captions

    out_dir = config.CLIPS_DIR / upload_id
    out_dir.mkdir(parents=True, exist_ok=True)
    # Limpiar clips de una corrida anterior para no dejar clip_N.mp4 obsoletos si esta
    # produce menos momentos (clips.json es la fuente de verdad, pero los MP4 sueltos no).
    for old in out_dir.glob("clip_*.mp4"):
        old.unlink()
    src_dur = info["duration"] or 0.0

    rendered: list[dict] = []
    for c in clips_in:
        # Re-coerción defensiva: moments.json lo escribe `detection`, pero si el artefacto
        # local se manipula, un id no-entero podría derivar el nombre fuera de out_dir y un
        # campo faltante daría KeyError→500. Forzamos tipos y devolvemos 400 si no cuadran.
        try:
            cid = int(c["id"])
            start = float(c["start"])
            end_raw = float(c["end"])
        except (KeyError, TypeError, ValueError) as e:
            raise ClipError(f"moments.json con un clip inválido: {e}") from e
        # Acotar el fin a la duración real del archivo (los timestamps vienen del transcript).
        end = min(end_raw, src_dur) if src_dur else end_raw
        dur = round(end - start, 3)
        if dur <= 0:
            continue  # momento fuera de rango del archivo: se omite
        out = out_dir / f"clip_{cid}.mp4"

        # Subtítulos: SRT temporal con tiempos 0-based al clip. El texto va EN el archivo
        # (nunca inline en el filtro). Se limpia sí o sí tras renderizar.
        srt_path: Path | None = None
        used_subs = False
        if want_subs and transcript is not None:
            cues = _caption_cues(transcript, start, end)
            if cues:
                srt_path = config.TMP_DIR / f"sub_{uuid4().hex}.srt"
                srt_path.parent.mkdir(parents=True, exist_ok=True)
                storage.atomic_write_text(srt_path, transcription.to_srt(cues))
                used_subs = True
        try:
            _run(build(src, start, dur, out, srt_path), timeout=config.FFMPEG_TIMEOUT)
        finally:
            if srt_path is not None:
                srt_path.unlink(missing_ok=True)

        rendered.append({
            "id": cid,
            "filename": out.name,
            "file": str(out),
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": dur,
            "score": c.get("score"),
            "title": c.get("title", ""),
            "subtitles": used_subs,
        })

    if not rendered:
        raise ClipError("Ningún momento cae dentro de la duración del archivo.")

    payload = {
        "upload_id": upload_id,
        "mode": mode,
        "width": config.CLIP_WIDTH,
        "height": config.CLIP_HEIGHT,
        "subtitles": want_subs,
        "n_clips": len(rendered),
        "clips": rendered,
    }
    storage.atomic_write_text(
        out_dir / "clips.json", json.dumps(payload, ensure_ascii=False, indent=2)
    )
    storage.update_metadata(upload_id, {
        "status": "clips",
        "clips": {"dir": str(out_dir), "n_clips": len(rendered), "mode": mode},
    })
    return payload


def load_clips(upload_id: str) -> dict | None:
    """Devuelve clips.json de una subida, o None si no existe (o el id es inválido)."""
    if not storage.valid_upload_id(upload_id):
        return None
    out_file = config.CLIPS_DIR / upload_id / "clips.json"
    if not out_file.is_file():
        return None
    return json.loads(out_file.read_text(encoding="utf-8"))
