"""Endpoints de archivos, transcripción y detección (FP-MVP-02/03, PP-MVP-01).

POST /upload                       subir un archivo (CRI-258)
GET  /uploads                      listar subidas
GET  /uploads/{id}                 metadata de una subida
POST /uploads/{id}/transcribe      transcribir la subida (FP-MVP-03)
GET  /uploads/{id}/transcript      obtener la transcripción
POST /uploads/{id}/detect          detectar mejores momentos (PP-MVP-01)
GET  /uploads/{id}/moments         obtener los momentos detectados
POST /uploads/{id}/clips           cortar los clips verticales con FFmpeg (PP-MVP-02)
GET  /uploads/{id}/clips           obtener el índice de clips cortados
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..services import clipping, detection, storage, transcription

router = APIRouter(tags=["files"])


@router.post("/upload", status_code=201)
async def upload(file: UploadFile = File(...)) -> dict:
    """Sube un archivo de audio/video, lo valida, lo guarda y registra su metadata."""
    try:
        return await storage.save_upload(file)
    except storage.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/uploads")
def list_uploads() -> list[dict]:
    """Lista las subidas registradas (más recientes primero)."""
    return storage.list_uploads()


@router.get("/uploads/{upload_id}")
def get_upload(upload_id: str) -> dict:
    """Devuelve la metadata de una subida puntual."""
    meta = storage.load_metadata(upload_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    return meta


@router.post("/uploads/{upload_id}/transcribe")
def transcribe_upload(upload_id: str, language: str | None = None) -> dict:
    """Transcribe la subida con Groq (whisper-large-v3) y guarda el resultado.

    `language` opcional (ISO, p.ej. 'es'); por defecto usa el idioma configurado.
    """
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    try:
        return transcription.transcribe_upload(upload_id, language=language)
    except transcription.UpstreamError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except transcription.TranscriptionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/uploads/{upload_id}/transcript")
def get_transcript(upload_id: str) -> dict:
    """Devuelve la transcripción de una subida (transcript.json)."""
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    transcript = transcription.load_transcript(upload_id)
    if transcript is None:
        raise HTTPException(
            status_code=404,
            detail=f"La subida '{upload_id}' todavía no fue transcrita.",
        )
    return transcript


@router.post("/uploads/{upload_id}/detect")
def detect_moments(
    upload_id: str, engine: str | None = None, n_clips: int | None = None,
) -> dict:
    """Detecta los mejores momentos con un LLM y guarda moments.json.

    `engine` opcional ('groq' por defecto, 'claude' si hay ANTHROPIC_API_KEY);
    `n_clips` opcional (cantidad máxima de clips a proponer).
    """
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    try:
        return detection.detect_moments(upload_id, engine=engine, n_clips=n_clips)
    except detection.UpstreamError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except detection.DetectError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/uploads/{upload_id}/moments")
def get_moments(upload_id: str) -> dict:
    """Devuelve los momentos detectados (moments.json) de una subida."""
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    moments = detection.load_moments(upload_id)
    if moments is None:
        raise HTTPException(
            status_code=404,
            detail=f"La subida '{upload_id}' todavía no tiene momentos detectados.",
        )
    return moments


@router.post("/uploads/{upload_id}/clips")
def cut_clips(upload_id: str) -> dict:
    """Corta los momentos detectados en clips verticales 9:16 con FFmpeg (PP-MVP-02)."""
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    try:
        return clipping.cut_clips(upload_id)
    except clipping.UpstreamError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except clipping.ClipError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/uploads/{upload_id}/clips")
def get_clips(upload_id: str) -> dict:
    """Devuelve el índice de clips cortados (clips.json) de una subida."""
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    clips = clipping.load_clips(upload_id)
    if clips is None:
        raise HTTPException(
            status_code=404,
            detail=f"La subida '{upload_id}' todavía no tiene clips cortados.",
        )
    return clips
