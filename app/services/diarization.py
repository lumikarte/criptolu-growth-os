"""Diarización — etiquetar quién habla (W-02, CRI-561).

Groq (whisper-large-v3) da la transcripción con word-timestamps pero NO diariza. Esta capa
la enriquece: agrega un ``speaker`` a cada word y segmento de ``transcript.json``, más un
bloque ``diarization`` con el resumen. No reemplaza a Groq — corre aparte y mapea los turnos
de speaker (gruesos) sobre los words ya existentes por **solapamiento temporal**.

Motor enchufable (``ENGINES``), igual patrón que ``detection.py``:
  - ``assemblyai`` (default): API cloud vía urllib, sin GPU ni token HF. Sube el audio,
    pide ``speaker_labels`` y devuelve los turnos [{start,end,speaker}]. Se descarta su
    transcripción; solo se usa el speaker+tiempo.
  - ``pyannote`` (opcional): local con pyannote.audio (import perezoso). Requiere ``torch``,
    un token HF y aceptar el modelo gated. Sin fricción de setup solo si ya lo tenés.

Los campos ``speaker`` son OPCIONALES y retro-compatibles: un transcript sin diarizar sigue
funcionando igual en todo el pipeline (subtítulos, detección, etc.).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from .. import config
from . import storage, transcription


class DiarizeError(RuntimeError):
    """Falla de validación al diarizar (se traduce a HTTP 400 en el router)."""


class UpstreamError(DiarizeError):
    """Falla de la dependencia externa (AssemblyAI/pyannote, red, key) → HTTP 502."""


# --------------------------------------------------------------------------- #
# Mapeo de turnos de speaker sobre los words (el corazón testeable)
# --------------------------------------------------------------------------- #

def _speaker_at(turns: list[dict], t: float) -> str | None:
    """Speaker cuyo turno cubre el instante `t`; si ninguno, el turno más cercano."""
    if not turns:
        return None
    for turn in turns:
        if turn["start"] - 1e-6 <= t <= turn["end"] + 1e-6:
            return turn["speaker"]
    # ninguno lo cubre (hueco entre turnos) → el más cercano por distancia al intervalo.
    def dist(turn: dict) -> float:
        if t < turn["start"]:
            return turn["start"] - t
        if t > turn["end"]:
            return t - turn["end"]
        return 0.0
    return min(turns, key=dist)["speaker"]


def _assign_speakers(segments: list[dict], turns: list[dict]) -> list[str]:
    """Asigna speaker a cada word (por el midpoint de la word) y a cada segmento (voto
    mayoritario de sus words). Muta `segments` in-place. Devuelve la lista de speakers vistos.

    El mapeo es a nivel de TURNO (grueso), robusto ante timestamps que no coinciden exacto
    entre Groq y el diarizador: se compara el punto medio de cada word con los intervalos.
    """
    turns = sorted(turns, key=lambda x: x["start"])
    seen: list[str] = []

    def note(sp: str | None) -> None:
        if sp is not None and sp not in seen:
            seen.append(sp)

    for seg in segments:
        words = seg.get("words") or []
        counts: dict[str, int] = {}
        for w in words:
            mid = (w["start"] + w["end"]) / 2.0
            sp = _speaker_at(turns, mid)
            w["speaker"] = sp
            note(sp)
            if sp is not None:
                counts[sp] = counts.get(sp, 0) + 1
        if counts:
            seg["speaker"] = max(counts, key=counts.get)  # voto mayoritario
        else:
            # segmento sin words → speaker por el midpoint del segmento
            mid = (seg["start"] + seg["end"]) / 2.0
            sp = _speaker_at(turns, mid)
            seg["speaker"] = sp
            note(sp)
    return seen


# --------------------------------------------------------------------------- #
# Motores (preparan el audio y devuelven los turnos [{start,end,speaker}])
# --------------------------------------------------------------------------- #

def _prepare_audio(upload_id: str) -> Path:
    """Regenera el .ogg 16 kHz mono desde el source (el audio de la subida no persiste).

    El caller es responsable de borrar el archivo. Reusa el downsample de transcription.
    """
    meta = storage.load_metadata(upload_id)
    if meta is None:
        raise DiarizeError(f"Subida '{upload_id}' no encontrada.")
    src = Path(meta["stored_path"])
    if not src.is_file():
        raise DiarizeError(f"Falta el archivo de la subida '{upload_id}': {src}")
    out = config.TMP_DIR / f"diar16k_{uuid4().hex}{config.AUDIO_DOWNSAMPLE_EXT}"
    try:
        transcription._downsample_16k(src, out)
    except transcription.UpstreamError as e:
        raise UpstreamError(str(e)) from e
    return out


def _http_json(req: urllib.request.Request, *, what: str) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise UpstreamError(f"{what} HTTP {e.code}: {e.read().decode()[:300]}") from e
    except urllib.error.URLError as e:
        raise UpstreamError(f"Red al contactar {what}: {e}") from e


def _engine_assemblyai(upload_id: str) -> list[dict]:
    """Diariza con AssemblyAI: sube el audio, pide speaker_labels, poll, devuelve turnos."""
    if not config.ASSEMBLYAI_API_KEY:
        raise UpstreamError(
            "Falta ASSEMBLYAI_API_KEY. Ponela en editorpro/.env para el engine 'assemblyai'."
        )
    headers = {"authorization": config.ASSEMBLYAI_API_KEY}
    audio = _prepare_audio(upload_id)
    try:
        # 1) subir el audio
        up = urllib.request.Request(
            f"{config.ASSEMBLYAI_BASE_URL}/upload", data=audio.read_bytes(),
            headers={**headers, "content-type": "application/octet-stream"},
        )
        audio_url = _http_json(up, what="AssemblyAI upload").get("upload_url")
        if not audio_url:
            raise UpstreamError("AssemblyAI no devolvió upload_url.")
        # 2) pedir transcripción con diarización
        create = urllib.request.Request(
            f"{config.ASSEMBLYAI_BASE_URL}/transcript",
            data=json.dumps({"audio_url": audio_url, "speaker_labels": True}).encode(),
            headers={**headers, "content-type": "application/json"},
        )
        tid = _http_json(create, what="AssemblyAI transcript").get("id")
        if not tid:
            raise UpstreamError("AssemblyAI no devolvió id de transcripción.")
        # 3) poll hasta completed/error
        poll_url = f"{config.ASSEMBLYAI_BASE_URL}/transcript/{tid}"
        for _ in range(config.ASSEMBLYAI_MAX_POLLS):
            data = _http_json(urllib.request.Request(poll_url, headers=headers),
                              what="AssemblyAI poll")
            status = data.get("status")
            if status == "completed":
                return _turns_from_assemblyai(data)
            if status == "error":
                raise UpstreamError(f"AssemblyAI falló: {data.get('error')}")
            time.sleep(config.ASSEMBLYAI_POLL_INTERVAL)
        raise UpstreamError("AssemblyAI no completó a tiempo (timeout de polling).")
    finally:
        audio.unlink(missing_ok=True)


def _turns_from_assemblyai(data: dict) -> list[dict]:
    """Extrae [{start,end,speaker}] de las utterances de AssemblyAI (ms → s)."""
    turns = []
    for u in data.get("utterances") or []:
        turns.append({
            "start": u["start"] / 1000.0, "end": u["end"] / 1000.0,
            "speaker": f"SPEAKER_{u['speaker']}",
        })
    return turns


def _engine_pyannote(upload_id: str) -> list[dict]:
    """Diariza local con pyannote.audio (import perezoso). Requiere torch + token HF gated."""
    if not config.HF_TOKEN:
        raise UpstreamError(
            "Falta HF_TOKEN para el engine 'pyannote' (y aceptar el modelo gated)."
        )
    try:
        from pyannote.audio import Pipeline
    except ImportError as e:
        raise UpstreamError(
            "Engine 'pyannote' no disponible: falta la dep. 'pip install pyannote.audio torch' "
            "(o usá el engine 'assemblyai')."
        ) from e
    audio = _prepare_audio(upload_id)
    try:
        pipe = Pipeline.from_pretrained(config.PYANNOTE_MODEL, use_auth_token=config.HF_TOKEN)
        diarization = pipe(str(audio))
        turns = [
            {"start": float(seg.start), "end": float(seg.end), "speaker": str(label)}
            for seg, _, label in diarization.itertracks(yield_label=True)
        ]
        if not turns:
            raise UpstreamError(
                "pyannote no devolvió speakers (¿modelo gated no aceptado?)."
            )
        return turns
    except UpstreamError:
        raise
    except Exception as e:  # noqa: BLE001 — cualquier fallo del pipeline local → 502 claro
        raise UpstreamError(f"Falló pyannote: {e}") from e
    finally:
        audio.unlink(missing_ok=True)


ENGINES = {
    "assemblyai": _engine_assemblyai,  # cloud, default (sin GPU/token HF)
    "pyannote": _engine_pyannote,      # local, opcional (torch + token HF)
}


def diarize_upload(upload_id: str, *, engine: str | None = None) -> dict:
    """Diariza una subida ya transcrita y enriquece transcript.json con speakers.

    Devuelve {upload_id, engine, n_speakers, speakers}. Lanza DiarizeError si no hay
    transcripción o el motor/entrada es inválido; UpstreamError (502) si el motor externo falla.
    """
    transcript = transcription.load_transcript(upload_id)
    if transcript is None:
        raise DiarizeError(
            f"La subida '{upload_id}' no tiene transcripción. Transcribila primero."
        )
    segments = transcript.get("segments") or []
    if not segments:
        raise DiarizeError("La transcripción no tiene segmentos para diarizar.")

    eng = engine or config.DIARIZE_DEFAULT_ENGINE
    if eng not in ENGINES:
        raise DiarizeError(f"Motor desconocido '{eng}'. Disponibles: {', '.join(ENGINES)}")

    turns = ENGINES[eng](upload_id)
    if not turns:
        raise UpstreamError("El diarizador no devolvió ningún turno de speaker.")
    speakers = _assign_speakers(segments, turns)

    model = "universal" if eng == "assemblyai" else config.PYANNOTE_MODEL
    transcript["diarization"] = {
        "engine": eng, "model": model,
        "n_speakers": len(speakers), "speakers": speakers,
    }
    out_dir = config.TRANSCRIPTS_DIR / upload_id
    out_dir.mkdir(parents=True, exist_ok=True)
    storage.atomic_write_text(
        out_dir / "transcript.json", json.dumps(transcript, ensure_ascii=False, indent=2)
    )
    storage.update_metadata(upload_id, {
        "status": "diarized",
        "diarization": {"engine": eng, "n_speakers": len(speakers)},
    })
    return {
        "upload_id": upload_id, "engine": eng,
        "n_speakers": len(speakers), "speakers": speakers,
    }
