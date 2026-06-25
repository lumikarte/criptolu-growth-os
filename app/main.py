"""Punto de entrada de la API FastAPI (CRI-252).

Correr en local:
    uvicorn app.main:app --reload
"""

from __future__ import annotations

import tempfile
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import config
from .routers import files, health


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Al arrancar, asegurar la estructura de carpetas de datos (CRI-256).
    config.ensure_dirs()
    # Temporales en disco real: /tmp es un tmpfs con cuota que se llena y rompe
    # el spooling de UploadFile en subidas grandes.
    config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(config.TMP_DIR)
    yield


app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION, lifespan=lifespan)
app.include_router(health.router)
app.include_router(files.router)
