"""Tests de la capa de auth (commit f652e59 + hardening tras auditoría Codex).

Estos tests ejercen el RECHAZO, no solo el camino feliz: es lo que faltaba según la
revisión de Codex (todos los tests existentes ya usaban AUTH_HEADERS correcto, así que
"186/186 pasan" probaba que el auth no rompió nada, no que efectivamente bloquea a
alguien).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import config
from app.main import app

from .conftest import AUTH_HEADERS


def test_health_y_root_abiertos_sin_auth():
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/").status_code == 200


def test_rechaza_sin_header():
    with TestClient(app) as c:
        assert c.get("/uploads").status_code == 401


def test_rechaza_key_incorrecta():
    with TestClient(app) as c:
        r = c.get("/uploads", headers={"Authorization": "Bearer key-incorrecta"})
        assert r.status_code == 401


def test_rechaza_scheme_no_bearer():
    with TestClient(app) as c:
        r = c.get("/uploads", headers={"Authorization": f"Basic {config.PODCASTPRO_API_KEY}"})
        assert r.status_code == 401


def test_acepta_key_correcta():
    with TestClient(app) as c:
        assert c.get("/uploads", headers=AUTH_HEADERS).status_code == 200


def test_falla_cerrado_sin_key_configurada(monkeypatch):
    monkeypatch.setattr(config, "PODCASTPRO_API_KEY", None)
    with TestClient(app) as c:
        r = c.get("/uploads", headers=AUTH_HEADERS)
        assert r.status_code == 500


def test_docs_deshabilitados():
    with TestClient(app) as c:
        assert c.get("/openapi.json", headers=AUTH_HEADERS).status_code == 404
        assert c.get("/docs", headers=AUTH_HEADERS).status_code == 404
