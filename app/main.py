"""Punto de entrada de la API FastAPI (CRI-252).

Correr en local:
    uvicorn app.main:app --reload
"""

from __future__ import annotations

import tempfile
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config
from .routers import approvals, brand, distribution, files, health, jobs, metrics
from .security import is_valid_bearer


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Al arrancar, asegurar la estructura de carpetas de datos (CRI-256).
    config.ensure_dirs()
    # Temporales en disco real: /tmp es un tmpfs con cuota que se llena y rompe
    # el spooling de UploadFile en subidas grandes.
    config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(config.TMP_DIR)
    yield


# Rutas sin auth: healthcheck de monitoreo/deploy. Todo lo demás queda protegido por
# default vía middleware (no por dependency de router) para que un router nuevo no
# pueda quedar afuera por descuido.
_OPEN_PATHS = {"/", "/health"}

app = FastAPI(
    title=config.APP_NAME,
    version=config.APP_VERSION,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def _require_api_key(request: Request, call_next):
    # Nota (auditoría Codex): esto corre ANTES del routing, así que si algún día se agrega
    # CORSMiddleware para un frontend en browser, tiene que envolver a este middleware por
    # afuera (o hay que exceptuar el verbo OPTIONS acá) — un preflight real no manda
    # Authorization y hoy recibiría 401 en vez del 200/204 vacío que CORS espera.
    if request.url.path not in _OPEN_PATHS:
        if not config.PODCASTPRO_API_KEY:
            return JSONResponse(
                {"detail": "PODCASTPRO_API_KEY no configurada (ver .env.example)."},
                status_code=500,
            )
        if not is_valid_bearer(request.headers.get("authorization")):
            return JSONResponse({"detail": "API key inválida o ausente."}, status_code=401)
    return await call_next(request)


app.include_router(health.router)
app.include_router(files.router)
app.include_router(brand.router)
app.include_router(approvals.router)
app.include_router(metrics.router)
app.include_router(jobs.router)
app.include_router(distribution.router)
