"""Autenticación de la API vía API key compartida (Authorization: Bearer <key>)."""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import config

_bearer = HTTPBearer(auto_error=False)


def require_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """Dependency de FastAPI: exige Authorization: Bearer <PODCASTPRO_API_KEY>."""
    if not config.PODCASTPRO_API_KEY:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "PODCASTPRO_API_KEY no configurada (ver .env.example).",
        )
    if credentials is None or not secrets.compare_digest(
        credentials.credentials, config.PODCASTPRO_API_KEY
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key inválida o ausente.")
