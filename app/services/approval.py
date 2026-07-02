"""Gate de aprobación humana antes de publicar (W-06, CRI-565).

Requisito no funcional del PRD: NADA se publica sin un OK humano explícito. Este servicio
es el gate. Cada pieza publicable de una subida (cada clip + el carrusel + el post de feed
+ el hilo) arranca en estado ``pending`` y solo pasa a ``approved`` con una decisión
explícita. La distribución (W-05) DEBE consultar ``is_approved`` antes de publicar: una
pieza no aprobada no sale.

El estado vive en ``data/clips/<id>/approvals.json`` como un mapa
``piece_key -> {status, note, updated_at}``. Las piezas se derivan de los artefactos
existentes (clips.json / repurpose.json), así que el gate refleja lo que realmente hay.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .. import config
from . import clipping, repurpose, storage

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
_DECISIONS = {APPROVED, REJECTED, PENDING}  # PENDING = resetear una decisión previa

# Formatos de texto del repropósito que son piezas publicables por sí mismas.
_TEXT_PIECES = ("carousel", "feed_post", "thread")


class ApprovalError(RuntimeError):
    """Falla de validación del gate (se traduce a HTTP 400 en el router)."""


class NotApprovedError(RuntimeError):
    """La pieza no está aprobada: la distribución no puede publicarla. La usa W-05."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_pieces(upload_id: str) -> list[dict]:
    """Deriva las piezas publicables de una subida desde sus artefactos.

    Devuelve una lista de {key, kind, label} ordenada (clips por ranking, luego textos).
    Vacía si no hay id válido ni artefactos.
    """
    if not storage.valid_upload_id(upload_id):
        return []
    pieces: list[dict] = []

    clips = clipping.load_clips(upload_id)
    if clips:
        for c in clips.get("clips", []):
            pieces.append({
                "key": f"clip:{c['id']}",
                "kind": "clip",
                "label": (c.get("title") or f"Clip {c['id']}").strip(),
            })

    pkg = repurpose.load_repurpose(upload_id)
    if pkg:
        content = pkg.get("content", {})
        labels = {
            "carousel": content.get("carousel", {}).get("title") or "Carrusel",
            "feed_post": "Post de feed",
            "thread": "Hilo",
        }
        for key in _TEXT_PIECES:
            if content.get(key):
                pieces.append({"key": key, "kind": key, "label": str(labels[key]).strip()})
    return pieces


def _piece_keys(upload_id: str) -> set[str]:
    return {p["key"] for p in list_pieces(upload_id)}


def load_approvals(upload_id: str) -> dict:
    """Devuelve el mapa de decisiones guardado (key -> {status, note, updated_at}), o {}."""
    if not storage.valid_upload_id(upload_id):
        return {}
    f = config.CLIPS_DIR / upload_id / "approvals.json"
    if not f.is_file():
        return {}
    return json.loads(f.read_text(encoding="utf-8"))


def _save_approvals(upload_id: str, decisions: dict) -> None:
    out_dir = config.CLIPS_DIR / upload_id
    out_dir.mkdir(parents=True, exist_ok=True)
    storage.atomic_write_text(
        out_dir / "approvals.json", json.dumps(decisions, ensure_ascii=False, indent=2)
    )


def get_state(upload_id: str) -> dict:
    """Estado del gate: cada pieza con su status (pending por defecto) + resumen.

    {upload_id, pieces: [{key, kind, label, status, note, updated_at}],
     summary: {total, approved, rejected, pending, all_approved, publishable}}
    `all_approved` es True solo si hay piezas y todas están aprobadas.
    `publishable` es la cantidad de piezas aprobadas (lo que la distribución podría sacar).
    """
    pieces = list_pieces(upload_id)
    decisions = load_approvals(upload_id)
    counts = {APPROVED: 0, REJECTED: 0, PENDING: 0}
    enriched = []
    for p in pieces:
        d = decisions.get(p["key"], {})
        status = d.get("status", PENDING)
        if status not in _DECISIONS:
            status = PENDING
        counts[status] += 1
        enriched.append({
            **p,
            "status": status,
            "note": d.get("note", ""),
            "updated_at": d.get("updated_at"),
        })
    total = len(pieces)
    return {
        "upload_id": upload_id,
        "pieces": enriched,
        "summary": {
            "total": total,
            "approved": counts[APPROVED],
            "rejected": counts[REJECTED],
            "pending": counts[PENDING],
            "all_approved": total > 0 and counts[APPROVED] == total,
            "publishable": counts[APPROVED],
        },
    }


def decide(upload_id: str, piece_key: str, status: str, note: str = "") -> dict:
    """Registra una decisión sobre una pieza. Devuelve el estado actualizado de esa pieza.

    Lanza ApprovalError si el status es inválido o la pieza no existe en la subida.
    `status=pending` resetea una decisión previa. `note` es opcional (motivo del rechazo).
    """
    if status not in _DECISIONS:
        raise ApprovalError(
            f"Decisión inválida '{status}'. Válidas: {', '.join(sorted(_DECISIONS))}."
        )
    if piece_key not in _piece_keys(upload_id):
        raise ApprovalError(
            f"Pieza '{piece_key}' no existe en la subida (¿generaste clips/repurpose?)."
        )
    decisions = load_approvals(upload_id)
    entry = {"status": status, "note": (note or "").strip(), "updated_at": _now()}
    decisions[piece_key] = entry
    _save_approvals(upload_id, decisions)
    return {"key": piece_key, **entry}


def is_approved(upload_id: str, piece_key: str) -> bool:
    """True solo si esa pieza tiene una decisión APPROVED guardada. Guarda para W-05."""
    return load_approvals(upload_id).get(piece_key, {}).get("status") == APPROVED


def require_approved(upload_id: str, piece_key: str) -> None:
    """Lanza NotApprovedError si la pieza no está aprobada. La distribución la llama antes
    de publicar para garantizar el gate."""
    if not is_approved(upload_id, piece_key):
        raise NotApprovedError(
            f"La pieza '{piece_key}' no está aprobada; no se puede publicar sin OK humano."
        )
