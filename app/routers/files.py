"""Endpoints de gestión de archivos (FP-MVP-02).

POST /upload        subir un archivo (CRI-258)
GET  /uploads       listar subidas
GET  /uploads/{id}  metadata de una subida
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..services import storage

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
