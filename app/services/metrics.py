"""Medición lite + Daily Brief (W-07, CRI-566).

Cierra el loop de Success Metrics del PRD instrumentando lo mínimo accionable:

- Por PIEZA: alcance (reach) y engagement, cargados a mano (o por la distribución de W-05
  cuando exista). Se guardan en ``data/clips/<id>/metrics.json``.
- Por EPISODIO (subida): minutos de trabajo manual (cuánto costó producir), y —derivados
  del gate de aprobación (W-06)— nº de piezas, nº aprobadas y nº "aprobadas sin retoque"
  (aprobadas en la primera y única decisión: ``revisions == 1``), la señal de calidad del
  motor.
- DAILY BRIEF: agrega los episodios (opcionalmente los de una fecha) en un parte corto:
  cuánto se produjo, qué proporción se aprobó sin retoque y el alcance/engagement total.

Todo se apoya en artefactos ya existentes; este módulo no publica nada.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .. import config
from . import approval, storage


class MetricsError(RuntimeError):
    """Falla de validación de métricas (se traduce a HTTP 400 en el router)."""


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _metrics_path(upload_id: str):
    return config.CLIPS_DIR / upload_id / "metrics.json"


def _load(upload_id: str) -> dict:
    """Devuelve metrics.json de una subida ({} si no existe o id inválido)."""
    if not storage.valid_upload_id(upload_id):
        return {}
    f = _metrics_path(upload_id)
    if not f.is_file():
        return {}
    return json.loads(f.read_text(encoding="utf-8"))


def _save(upload_id: str, data: dict) -> None:
    out_dir = config.CLIPS_DIR / upload_id
    out_dir.mkdir(parents=True, exist_ok=True)
    storage.atomic_write_text(
        _metrics_path(upload_id), json.dumps(data, ensure_ascii=False, indent=2)
    )


def _non_negative(value, field: str) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError) as e:
        raise MetricsError(f"'{field}' debe ser un número.") from e
    if n < 0:
        raise MetricsError(f"'{field}' no puede ser negativo.")
    return n


def record_episode_work(upload_id: str, manual_minutes: float) -> dict:
    """Registra los minutos de trabajo manual del episodio. Devuelve el bloque guardado."""
    minutes = _non_negative(manual_minutes, "manual_minutes")
    data = _load(upload_id)
    data["episode"] = {"manual_minutes": minutes, "recorded_at": _today()}
    _save(upload_id, data)
    return data["episode"]


def record_piece_performance(
    upload_id: str, piece_key: str, *, reach: float, engagement: float,
) -> dict:
    """Registra alcance/engagement de una pieza publicada. Valida que la pieza exista.

    Lanza MetricsError si la pieza no está entre las publicables de la subida (evita
    ensuciar el brief con claves inventadas).
    """
    pieces = {p["key"] for p in approval.list_pieces(upload_id)}
    if piece_key not in pieces:
        raise MetricsError(
            f"Pieza '{piece_key}' no existe en la subida (¿generaste clips/repurpose?)."
        )
    entry = {
        "reach": _non_negative(reach, "reach"),
        "engagement": _non_negative(engagement, "engagement"),
        "recorded_at": _today(),
    }
    data = _load(upload_id)
    data.setdefault("performance", {})[piece_key] = entry
    _save(upload_id, data)
    return {"key": piece_key, **entry}


def _approval_counts(upload_id: str) -> dict:
    """Deriva del gate: total de piezas, aprobadas y aprobadas sin retoque (revisions==1)."""
    state = approval.get_state(upload_id)
    approved_clean = sum(
        1 for p in state["pieces"]
        if p["status"] == approval.APPROVED and p.get("revisions", 0) == 1
    )
    return {
        "n_pieces": state["summary"]["total"],
        "n_approved": state["summary"]["approved"],
        "n_approved_clean": approved_clean,
    }


def episode_report(upload_id: str) -> dict:
    """Reporte de un episodio: trabajo + aprobación + performance por pieza y totales."""
    data = _load(upload_id)
    perf = data.get("performance", {})
    totals = {
        "reach": sum(p.get("reach", 0) for p in perf.values()),
        "engagement": sum(p.get("engagement", 0) for p in perf.values()),
        "pieces_measured": len(perf),
    }
    return {
        "upload_id": upload_id,
        "episode": data.get("episode"),           # None si no se registró el trabajo
        "approval": _approval_counts(upload_id),
        "performance": {"by_piece": perf, "totals": totals},
    }


def daily_brief(date: str | None = None) -> dict:
    """Agrega todos los episodios con métricas en un parte. Si `date` (YYYY-MM-DD) se pasa,
    solo cuenta los episodios cuyo trabajo se registró ese día.

    Devuelve totales de producción, tasas de aprobación/sin-retoque y alcance/engagement.
    """
    episodes = []
    for meta in storage.list_uploads():
        uid = meta.get("id", "")
        data = _load(uid)
        if not data:
            continue
        ep = data.get("episode")
        if date is not None and (not ep or ep.get("recorded_at") != date):
            continue
        counts = _approval_counts(uid)
        perf = data.get("performance", {})
        episodes.append({
            "upload_id": uid,
            "manual_minutes": (ep or {}).get("manual_minutes", 0),
            **counts,
            "reach": sum(p.get("reach", 0) for p in perf.values()),
            "engagement": sum(p.get("engagement", 0) for p in perf.values()),
        })

    n_ep = len(episodes)
    tot_pieces = sum(e["n_pieces"] for e in episodes)
    tot_approved = sum(e["n_approved"] for e in episodes)
    tot_clean = sum(e["n_approved_clean"] for e in episodes)
    tot_minutes = sum(e["manual_minutes"] for e in episodes)
    return {
        "date": date or "all",
        "episodes": n_ep,
        "totals": {
            "pieces": tot_pieces,
            "approved": tot_approved,
            "approved_clean": tot_clean,
            "manual_minutes": tot_minutes,
            "reach": sum(e["reach"] for e in episodes),
            "engagement": sum(e["engagement"] for e in episodes),
        },
        "rates": {
            # proporción de piezas aprobadas y aprobadas sin retoque (0-1, None si no hay piezas)
            "approval_rate": round(tot_approved / tot_pieces, 3) if tot_pieces else None,
            "clean_rate": round(tot_clean / tot_approved, 3) if tot_approved else None,
            "avg_minutes_per_episode": round(tot_minutes / n_ep, 1) if n_ep else None,
        },
        "by_episode": episodes,
    }
