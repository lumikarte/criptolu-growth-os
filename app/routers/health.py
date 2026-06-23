"""Endpoints base: raíz / (CRI-255) y /health (CRI-254)."""

from __future__ import annotations

from fastapi import APIRouter

from .. import config

router = APIRouter(tags=["base"])


@router.get("/")
def root() -> dict:
    """Endpoint raíz: identifica el servicio."""
    return {"name": config.APP_NAME, "version": config.APP_VERSION, "status": "ok"}


@router.get("/health")
def health() -> dict:
    """Healthcheck simple para monitoreo/deploy."""
    return {"status": "healthy"}
