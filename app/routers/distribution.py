"""Endpoints de distribución multi-red (W-05, CRI-564).

POST /uploads/{id}/distribute      publica en Postiz las piezas APROBADAS (modo borrador)
GET  /uploads/{id}/distribution    devuelve el índice de la última distribución

Nada se publica sin pasar el gate de aprobación (W-06); ver services/distribution.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services import distribution, storage

router = APIRouter(tags=["distribution"])


@router.post("/uploads/{upload_id}/distribute")
def distribute(upload_id: str, type: str | None = None, networks: str | None = None) -> dict:
    """Publica en Postiz las piezas aprobadas de la subida (`?type=draft&networks=tiktok,x`).

    Solo salen las piezas que pasan `require_approved`; el resto va a `skipped`. Por defecto
    `type=draft` (el humano remata en Postiz); otros `type` se fuerzan a draft salvo que el
    auto-post esté habilitado (fase 2).
    """
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    nets = [n for n in networks.split(",") if n.strip()] if networks else None
    try:
        return distribution.distribute_upload(upload_id, post_type=type, networks=nets)
    except distribution.UpstreamError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except distribution.DistributionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/uploads/{upload_id}/distribution")
def get_distribution(upload_id: str) -> dict:
    """Devuelve el índice de la última distribución (distribution.json) de una subida."""
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    d = distribution.load_distribution(upload_id)
    if d is None:
        raise HTTPException(
            status_code=404, detail=f"La subida '{upload_id}' todavía no fue distribuida.")
    return d
