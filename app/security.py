"""Autenticación de la API vía API key compartida (Authorization: Bearer <key>).

Enforced por middleware en app.main (protege TODO por default; ver _OPEN_PATHS ahí), no
por dependency de router — así un router nuevo agregado a futuro queda protegido sin que
haga falta acordarse de listarlo. Hardening tras auditoría Codex 2026-07-14 sobre el
commit f652e59: el diseño anterior (opt-in por router) dejaba /docs y /openapi.json
afuera sin que nadie lo notara.
"""

from __future__ import annotations

import secrets

from . import config


def is_valid_bearer(authorization: str | None) -> bool:
    """True si el header Authorization trae "Bearer <PODCASTPRO_API_KEY>" correcto."""
    if not config.PODCASTPRO_API_KEY or not authorization:
        return False
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return secrets.compare_digest(token, config.PODCASTPRO_API_KEY)
