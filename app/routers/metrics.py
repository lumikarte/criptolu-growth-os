"""Endpoints de medición lite + Daily Brief (W-07, CRI-566).

POST /uploads/{id}/metrics/work            registrar minutos de trabajo manual del episodio
POST /uploads/{id}/metrics/pieces/{piece}  registrar alcance/engagement de una pieza
GET  /uploads/{id}/metrics                 reporte del episodio
GET  /brief                                Daily Brief (agrega episodios; ?date=YYYY-MM-DD)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services import metrics, storage

router = APIRouter(tags=["metrics"])


@router.post("/uploads/{upload_id}/metrics/work")
def record_work(upload_id: str, manual_minutes: float) -> dict:
    """Registra los minutos de trabajo manual del episodio (`?manual_minutes=`)."""
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    try:
        return metrics.record_episode_work(upload_id, manual_minutes)
    except metrics.MetricsError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/uploads/{upload_id}/metrics/pieces/{piece_key}")
def record_piece(upload_id: str, piece_key: str, reach: float, engagement: float) -> dict:
    """Registra alcance/engagement de una pieza publicada (`?reach=&engagement=`)."""
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    try:
        return metrics.record_piece_performance(
            upload_id, piece_key, reach=reach, engagement=engagement
        )
    except metrics.MetricsError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/uploads/{upload_id}/metrics")
def get_metrics(upload_id: str) -> dict:
    """Reporte de medición del episodio (trabajo + aprobación + performance)."""
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    return metrics.episode_report(upload_id)


@router.get("/brief")
def daily_brief(date: str | None = None) -> dict:
    """Daily Brief: agrega los episodios con métricas. `?date=YYYY-MM-DD` filtra por día."""
    return metrics.daily_brief(date=date)
