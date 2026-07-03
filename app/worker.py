"""Worker de la cola de jobs del pipeline (CRI-603).

Corre en un proceso aparte del API y toma los jobs encolados por `services/jobs.enqueue`.
RQ forkea un proceso hijo por job → aísla crashes/leaks de FFmpeg y aplica el timeout duro.

Uso:
    # con el CLI de RQ (recomendado)
    rq worker podcast-pipeline --url redis://localhost:6379/0
    # o con este módulo
    python -m app.worker

Requiere Redis/Valkey accesible en `config.REDIS_URL` y las deps `rq`/`redis` instaladas
(no hacen falta en modo eager ni para los tests).
"""

from __future__ import annotations

from . import config


def main() -> None:
    import redis
    from rq import Queue, Worker

    config.ensure_dirs()
    conn = redis.Redis.from_url(config.REDIS_URL)
    worker = Worker([Queue(config.JOBS_QUEUE_NAME, connection=conn)], connection=conn)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
