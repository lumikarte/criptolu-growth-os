"""Punto de entrada de la API FastAPI (CRI-252).

Correr en local:
    uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import config
from .routers import health


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Al arrancar, asegurar la estructura de carpetas de datos (CRI-256).
    config.ensure_dirs()
    yield


app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION, lifespan=lifespan)
app.include_router(health.router)
