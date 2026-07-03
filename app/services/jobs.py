"""Cola de jobs para el pipeline asíncrono (CRI-603).

`POST /uploads/{id}/process` deja de correr el pipeline dentro del request: **encola** un
job y devuelve 202 con un `job_id`. El trabajo pesado (FFmpeg + LLM, minutos) corre en un
worker aparte (ver `app/worker.py`), así ningún proxy corta la conexión y el trabajo
sobrevive a reinicios.

El **estado/progreso** del job es la fuente de verdad y vive en `data/jobs/<job_id>.json`
(escritura atómica, mismo patrón que el resto del repo). Redis/RQ solo lleva la mecánica de
cola. `GET /jobs/{id}` lee ese JSON.

Modo **eager** (`config.JOBS_EAGER`): ejecuta el job inline en el proceso actual, sin Redis
ni worker — para dev y tests. Así la suite no necesita un Redis corriendo.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from .. import config
from . import pipeline, storage

# Un job_id válido es hex de uuid4 (32 chars). Validar antes de construir rutas → anti
# path traversal, igual criterio que storage.valid_upload_id para las subidas.
_JOB_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")

STAGES = ("transcribe", "detect", "clips", "export")
_ACTIVE = ("queued", "started")


class JobError(RuntimeError):
    """Falla de validación del subsistema de jobs (→ 400/404 en el router)."""


def valid_job_id(job_id: str) -> bool:
    return bool(_JOB_ID_RE.match(job_id or ""))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(job_id: str):
    return config.JOBS_DIR / f"{job_id}.json"


def load(job_id: str) -> dict | None:
    """Devuelve el registro del job, o None si no existe (o el id es inválido)."""
    if not valid_job_id(job_id):
        return None
    f = _path(job_id)
    if not f.is_file():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def _save(rec: dict) -> None:
    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    storage.atomic_write_text(
        _path(rec["job_id"]), json.dumps(rec, ensure_ascii=False, indent=2)
    )


def update(job_id: str, patch: dict) -> dict | None:
    """Mezcla `patch` en el registro del job y lo persiste. None si no existe."""
    rec = load(job_id)
    if rec is None:
        return None
    rec.update(patch)
    _save(rec)
    return rec


def active_job_for(upload_id: str) -> dict | None:
    """Devuelve un job activo (queued/started) de esa subida, o None. Para el 409."""
    if not config.JOBS_DIR.is_dir():
        return None
    for f in config.JOBS_DIR.glob("*.json"):
        rec = json.loads(f.read_text(encoding="utf-8"))
        if rec.get("upload_id") == upload_id and rec.get("status") in _ACTIVE:
            return rec
    return None


def enqueue(upload_id: str, **params) -> dict:
    """Crea el registro del job (queued) y lo despacha. Devuelve el registro.

    En modo eager corre el job inline (y devuelve el registro ya finished/failed). Si no,
    lo empuja a la cola RQ (import perezoso de redis/rq) para que lo tome el worker.
    """
    job_id = uuid4().hex
    rec = {
        "job_id": job_id,
        "upload_id": upload_id,
        "status": "queued",
        "params": params,
        "stages": {s: "pending" for s in STAGES},
        "completed": [],
        "current_stage": None,
        "failed": None,
        "result": None,
        "enqueued_at": _now(),
        "started_at": None,
        "finished_at": None,
    }
    _save(rec)

    if config.JOBS_EAGER:
        run_job(job_id)          # inline, sin Redis
        return load(job_id)

    # Import perezoso: en eager/tests no se necesita redis ni rq instalados.
    import redis
    from rq import Queue

    conn = redis.Redis.from_url(config.REDIS_URL)
    Queue(config.JOBS_QUEUE_NAME, connection=conn).enqueue(
        "app.services.jobs.run_job", job_id, job_timeout=config.JOB_TIMEOUT,
    )
    return rec


def run_job(job_id: str) -> None:
    """Ejecuta el pipeline del job y persiste progreso/resultado. NUNCA levanta: cualquier
    fallo queda registrado en el job (status failed) para que GET /jobs/{id} lo muestre.

    Es la función que encola RQ (importable) y la que corre inline el modo eager.
    """
    rec = load(job_id)
    if rec is None:
        return
    update(job_id, {"status": "started", "started_at": _now()})

    def on_progress(stage: str, state: str) -> None:
        r = load(job_id)
        if r is None:
            return
        r["stages"][stage] = state
        if state in ("done", "skipped") and stage not in r["completed"]:
            r["completed"].append(stage)
        r["current_stage"] = stage if state == "running" else None
        _save(r)

    try:
        result = pipeline.process_upload(
            rec["upload_id"], on_progress=on_progress, **rec["params"]
        )
        update(job_id, {
            "status": "finished", "result": result, "current_stage": None,
            "finished_at": _now(),
        })
    except pipeline.PipelineError as e:
        update(job_id, {
            "status": "failed", "current_stage": None, "finished_at": _now(),
            "failed": {"stage": e.stage, "message": str(e), "upstream": e.upstream},
        })
    except Exception as e:  # noqa: BLE001 — un bug no debe tumbar el worker; queda en el job
        update(job_id, {
            "status": "failed", "current_stage": None, "finished_at": _now(),
            "failed": {"stage": rec.get("current_stage"), "message": str(e),
                       "upstream": False},
        })
