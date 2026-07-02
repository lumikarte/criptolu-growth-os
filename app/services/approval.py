"""Gate de aprobación humana antes de publicar (W-06, CRI-565).

Requisito no funcional del PRD: NADA se publica sin un OK humano explícito. Este servicio
es el gate. Cada pieza publicable de una subida (cada clip + el carrusel + el post de feed
+ el hilo + cada caption por red) arranca en ``pending`` y solo pasa a ``approved`` con una
decisión explícita. La distribución (W-05) DEBE consultar ``is_approved`` antes de
publicar: una pieza no aprobada no sale.

La aprobación se ATA AL CONTENIDO, no al slot: cada decisión guarda el ``fingerprint`` (hash)
de lo que se aprobó. Si el contenido se regenera (p. ej. ``/process?force=true`` o re-cortar
clips reasigna ids), el fingerprint deja de coincidir y la aprobación vieja queda ``stale``
→ vuelve a requerir revisión. Así el gate no se puede burlar reusando un OK previo sobre
contenido nuevo que nadie vio: ``is_approved`` falla cerrado ante cualquier cambio.

El estado vive en ``data/clips/<id>/approvals.json`` como un mapa
``piece_key -> {status, note, updated_at, fingerprint}``. Las piezas y sus fingerprints se
derivan de los artefactos vigentes (clips.json / repurpose.json).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .. import config
from . import clipping, repurpose, storage

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
STALE = "stale"  # DERIVADO (nunca se setea): había decisión pero el contenido cambió.
_DECISIONS = {APPROVED, REJECTED, PENDING}  # decisiones que un humano puede setear

# Formatos de texto del repropósito que son piezas publicables por sí mismas.
_TEXT_PIECES = ("carousel", "feed_post", "thread")


class ApprovalError(RuntimeError):
    """Falla de validación del gate (se traduce a HTTP 400 en el router)."""


class NotApprovedError(RuntimeError):
    """La pieza no está aprobada: la distribución no puede publicarla. La usa W-05."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fingerprint(obj) -> str:
    """Hash estable del contenido de una pieza (para atar la aprobación a lo aprobado)."""
    canonical = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def list_pieces(upload_id: str) -> list[dict]:
    """Deriva las piezas publicables de una subida desde sus artefactos.

    Devuelve una lista de {key, kind, label, fingerprint} ordenada (clips por ranking,
    luego textos, luego captions por red). Vacía si no hay id válido ni artefactos.
    El fingerprint ata la aprobación al contenido: si cambia, la aprobación caduca.
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
                # el fingerprint del clip incluye su contenido definitorio (no solo el id):
                # si al regenerar cambia el título/tiempos, la aprobación previa caduca.
                "fingerprint": _fingerprint(c),
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
                pieces.append({
                    "key": key, "kind": key, "label": str(labels[key]).strip(),
                    "fingerprint": _fingerprint(content[key]),
                })
        # Los captions por red también son contenido publicable → cada uno es pieza gateada.
        captions = content.get("captions") or {}
        for net in config.REPURPOSE_NETWORKS:
            if captions.get(net):
                pieces.append({
                    "key": f"caption:{net}", "kind": "caption",
                    "label": f"Caption {net}", "fingerprint": _fingerprint(captions[net]),
                })
    return pieces


def _piece_index(upload_id: str) -> dict[str, dict]:
    """Mapa key -> pieza (con fingerprint) de las piezas vigentes."""
    return {p["key"]: p for p in list_pieces(upload_id)}


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


def _effective_status(decision: dict | None, current_fp: str) -> str:
    """Status efectivo de una pieza: PENDING si no hay decisión; STALE si la decisión es
    sobre otro contenido (el fingerprint no coincide); si no, la decisión guardada."""
    if not decision:
        return PENDING
    status = decision.get("status")
    if status not in _DECISIONS:
        return PENDING
    if status == PENDING:
        return PENDING
    # approved/rejected solo valen para el contenido exacto que se decidió.
    if decision.get("fingerprint") != current_fp:
        return STALE
    return status


def get_state(upload_id: str) -> dict:
    """Estado del gate: cada pieza con su status efectivo + resumen.

    {upload_id, pieces: [{key, kind, label, fingerprint, status, note, updated_at}],
     summary: {total, approved, rejected, pending, stale, all_approved, publishable}}
    `status` puede ser stale (había un OK/rechazo pero el contenido cambió → re-revisar).
    `all_approved` es True solo si hay piezas y TODAS están aprobadas sobre el contenido
    vigente. `publishable` = piezas efectivamente aprobadas (lo que la distribución sacaría).
    """
    pieces = list_pieces(upload_id)
    decisions = load_approvals(upload_id)
    counts = {APPROVED: 0, REJECTED: 0, PENDING: 0, STALE: 0}
    enriched = []
    for p in pieces:
        d = decisions.get(p["key"])
        status = _effective_status(d, p["fingerprint"])
        counts[status] += 1
        enriched.append({
            **p,
            "status": status,
            "note": (d or {}).get("note", ""),
            "updated_at": (d or {}).get("updated_at"),
            "revisions": int((d or {}).get("revisions", 0)),
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
            "stale": counts[STALE],
            "all_approved": total > 0 and counts[APPROVED] == total,
            "publishable": counts[APPROVED],
        },
    }


def decide(upload_id: str, piece_key: str, status: str, note: str = "") -> dict:
    """Registra una decisión sobre una pieza. Devuelve el estado actualizado de esa pieza.

    Lanza ApprovalError si el status es inválido o la pieza no existe en la subida.
    `status=pending` resetea una decisión previa. `note` es opcional (motivo del rechazo).
    La decisión queda atada al fingerprint del contenido vigente de la pieza.
    """
    if status not in _DECISIONS:
        raise ApprovalError(
            f"Decisión inválida '{status}'. Válidas: {', '.join(sorted(_DECISIONS))}."
        )
    piece = _piece_index(upload_id).get(piece_key)
    if piece is None:
        raise ApprovalError(
            f"Pieza '{piece_key}' no existe en la subida (¿generaste clips/repurpose?)."
        )
    note = (note or "").strip()
    if len(note) > config.APPROVAL_NOTE_MAX_CHARS:
        raise ApprovalError(
            f"La nota supera el máximo de {config.APPROVAL_NOTE_MAX_CHARS} caracteres."
        )
    decisions = load_approvals(upload_id)
    prev = decisions.get(piece_key) or {}
    entry = {
        "status": status,
        "note": note,
        "updated_at": _now(),
        "fingerprint": piece["fingerprint"],
        # nº de veces que se decidió esta pieza. Sirve a la medición (W-07): "aprobada sin
        # retoque" = aprobada en la primera y única decisión (revisions == 1).
        "revisions": int(prev.get("revisions", 0)) + 1,
    }
    decisions[piece_key] = entry
    _save_approvals(upload_id, decisions)
    return {"key": piece_key, **entry}


def is_approved(upload_id: str, piece_key: str) -> bool:
    """True solo si la pieza está aprobada SOBRE SU CONTENIDO VIGENTE. Guarda para W-05.

    Falla cerrado: id/pieza inexistente, sin decisión, rechazada, o aprobada sobre un
    contenido que después cambió (fingerprint distinto) → False.
    """
    piece = _piece_index(upload_id).get(piece_key)
    if piece is None:
        return False
    d = load_approvals(upload_id).get(piece_key)
    return _effective_status(d, piece["fingerprint"]) == APPROVED


def require_approved(upload_id: str, piece_key: str) -> None:
    """Lanza NotApprovedError si la pieza no está aprobada sobre su contenido vigente.
    La distribución (W-05) la llama antes de publicar para garantizar el gate."""
    if not is_approved(upload_id, piece_key):
        raise NotApprovedError(
            f"La pieza '{piece_key}' no está aprobada (o el contenido cambió); "
            f"no se puede publicar sin OK humano."
        )
