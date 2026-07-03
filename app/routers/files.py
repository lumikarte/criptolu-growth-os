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
POST /uploads/{id}/export          exportar los clips a carpetas por plataforma (PP-MVP-03)
GET  /uploads/{id}/export          obtener el índice de exports
POST /uploads/{id}/repurpose       repropósito multi-formato desde la transcripción (W-03)
GET  /uploads/{id}/repurpose       obtener el paquete multi-formato
POST /uploads/{id}/carousel/render renderizar el carrusel a slides PNG de marca (CRI-604)
GET  /uploads/{id}/carousel        obtener el índice del carrusel renderizado
GET  /uploads/{id}/carousel/slide_{n}.png   servir una slide PNG
POST /uploads/{id}/process         orquestar transcribe→detect→clips→export (PP-MVP-05)
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import config
from ..services import (
    carousel_render,
    clipping,
    detection,
    export,
    jobs,
    repurpose,
    storage,
    transcription,
)

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
def cut_clips(upload_id: str, subtitles: bool | None = None) -> dict:
    """Corta los momentos detectados en clips verticales 9:16 con FFmpeg (PP-MVP-02/04).

    `subtitles` opcional: si se omite, se queman captions cuando hay transcripción (si no,
    se omiten); `true` los exige (400 si falta transcripción); `false` los desactiva.
    """
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    try:
        return clipping.cut_clips(upload_id, subtitles=subtitles)
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


@router.post("/uploads/{upload_id}/export")
def export_clips(upload_id: str, platforms: str | None = None) -> dict:
    """Exporta los clips a las carpetas de plataforma con naming consistente (PP-MVP-03).

    `platforms` opcional: lista separada por comas (`shorts,reels,tiktok`); por defecto
    exporta a las tres. Una plataforma desconocida devuelve 400.
    """
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    plats = [p for p in platforms.split(",") if p.strip()] if platforms else None
    try:
        return export.export_clips(upload_id, platforms=plats)
    except export.ExportError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/uploads/{upload_id}/export")
def get_export(upload_id: str) -> dict:
    """Devuelve el índice de exports (export.json) de una subida."""
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    exp = export.load_export(upload_id)
    if exp is None:
        raise HTTPException(
            status_code=404,
            detail=f"La subida '{upload_id}' todavía no fue exportada.",
        )
    return exp


@router.post("/uploads/{upload_id}/repurpose")
def repurpose_upload(upload_id: str, engine: str | None = None) -> dict:
    """Genera el paquete multi-formato (carrusel, feed, hilo, captions) y lo guarda (W-03).

    Requiere que la subida ya esté transcrita. `engine` opcional ('groq' por defecto,
    'claude' si hay ANTHROPIC_API_KEY). El diferencial: de 1 fuente, texto para varias redes.
    """
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    try:
        return repurpose.repurpose_upload(upload_id, engine=engine)
    except repurpose.UpstreamError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except repurpose.RepurposeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/uploads/{upload_id}/repurpose")
def get_repurpose(upload_id: str) -> dict:
    """Devuelve el paquete multi-formato (repurpose.json) de una subida."""
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    pkg = repurpose.load_repurpose(upload_id)
    if pkg is None:
        raise HTTPException(
            status_code=404,
            detail=f"La subida '{upload_id}' todavía no tiene contenido repurposado.",
        )
    return pkg


@router.post("/uploads/{upload_id}/carousel/render")
def render_carousel(upload_id: str) -> dict:
    """Renderiza el carrusel del repurpose a slides PNG de marca y guarda el índice (CRI-604)."""
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    try:
        return carousel_render.render_carousel(upload_id)
    except carousel_render.UpstreamError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except carousel_render.CarouselError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/uploads/{upload_id}/carousel")
def get_carousel(upload_id: str) -> dict:
    """Devuelve el índice del carrusel renderizado (carousel.json) de una subida."""
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    car = carousel_render.load_carousel(upload_id)
    if car is None:
        raise HTTPException(
            status_code=404,
            detail=f"La subida '{upload_id}' todavía no tiene carrusel renderizado.",
        )
    return car


@router.get("/uploads/{upload_id}/carousel/slide_{index}.png")
def get_carousel_slide(upload_id: str, index: int) -> FileResponse:
    """Sirve una slide PNG puntual del carrusel (valida id e índice; anti path traversal)."""
    if not storage.valid_upload_id(upload_id):
        raise HTTPException(status_code=404, detail="Subida no encontrada.")
    if index < 0:
        raise HTTPException(status_code=404, detail="Índice de slide inválido.")
    # El nombre se construye desde un entero validado (no del input crudo) → sin traversal.
    slide = config.CLIPS_DIR / upload_id / "carousel" / f"slide_{index:02d}.png"
    if not slide.is_file():
        raise HTTPException(status_code=404, detail="Slide no encontrada.")
    return FileResponse(slide, media_type="image/png")


@router.post("/uploads/{upload_id}/process", status_code=202)
def process_upload(
    upload_id: str,
    language: str | None = None,
    engine: str | None = None,
    n_clips: int | None = None,
    subtitles: bool | None = None,
    platforms: str | None = None,
    force: bool = False,
) -> dict:
    """Encola el pipeline completo (transcribe→detect→clips→export) y devuelve 202 (CRI-603).

    **No bloquea**: el trabajo pesado (FFmpeg + Groq/Claude, minutos) corre en un worker
    aparte. Devuelve un `job_id`; seguí el avance con `GET /jobs/{job_id}`. `?force=true`
    rehace todo (resume por defecto). 409 si ya hay un job activo para esa subida (salvo
    `force`). Passthrough: `language, engine, n_clips, subtitles, platforms`.
    """
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    active = jobs.active_job_for(upload_id)
    if active and not force:
        raise HTTPException(
            status_code=409,
            detail={"message": "Ya hay un job activo para esta subida.",
                    "job_id": active["job_id"], "status": active["status"]},
        )
    plats = [p for p in platforms.split(",") if p.strip()] if platforms else None
    rec = jobs.enqueue(
        upload_id, language=language, engine=engine, n_clips=n_clips,
        subtitles=subtitles, platforms=plats, force=force,
    )
    return {
        "job_id": rec["job_id"], "upload_id": upload_id,
        "status": rec["status"], "poll": f"/jobs/{rec['job_id']}",
    }
