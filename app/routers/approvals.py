"""Endpoints del gate de aprobación humana (W-06, CRI-565).

GET  /uploads/{id}/approvals              estado del gate (piezas + status + resumen)
POST /uploads/{id}/approvals/{piece}      decidir sobre una pieza (?decision=approve|reject|reset)

Ninguna pieza se publica (W-05) sin quedar aprobada acá. `piece` es el key de la pieza
tal como aparece en el GET (p. ej. `clip:1`, `carousel`, `feed_post`, `thread`).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services import approval, storage

router = APIRouter(tags=["approvals"])

# La UI/CLI manda un verbo cómodo; lo mapeamos al status interno.
_DECISION_MAP = {
    "approve": approval.APPROVED,
    "reject": approval.REJECTED,
    "reset": approval.PENDING,
}


@router.get("/uploads/{upload_id}/approvals")
def get_approvals(upload_id: str) -> dict:
    """Devuelve el estado del gate: cada pieza publicable con su status y el resumen."""
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    return approval.get_state(upload_id)


@router.post("/uploads/{upload_id}/approvals/{piece_key}")
def decide_piece(
    upload_id: str, piece_key: str, decision: str, note: str | None = None,
) -> dict:
    """Registra la decisión humana sobre una pieza (`?decision=approve|reject|reset`)."""
    if storage.load_metadata(upload_id) is None:
        raise HTTPException(status_code=404, detail=f"Subida '{upload_id}' no encontrada.")
    status = _DECISION_MAP.get(decision)
    if status is None:
        raise HTTPException(
            status_code=400,
            detail=f"decision inválida '{decision}'. Válidas: {', '.join(_DECISION_MAP)}.",
        )
    try:
        return approval.decide(upload_id, piece_key, status, note=note or "")
    except approval.ApprovalError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
