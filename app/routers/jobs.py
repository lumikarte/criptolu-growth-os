"""Endpoint de consulta de jobs del pipeline asíncrono (CRI-603).

GET /jobs/{job_id}   estado + progreso por etapa de un job encolado por POST /process.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services import jobs

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    """Devuelve el estado del job (queued/started/finished/failed) y su progreso por etapa."""
    rec = jobs.load(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' no encontrado.")
    return rec
