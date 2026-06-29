"""Tests del servicio de export (PP-MVP-03): naming seguro, validación e idempotencia."""

from __future__ import annotations

import json

import pytest

from app import config
from app.services import export

from .conftest import VALID_ID, make_clips, make_upload


# --- slugify: títulos hostiles del LLM no pueden romper el nombre de archivo ----------

@pytest.mark.parametrize("title, expected", [
    ("Gancho brutal", "gancho-brutal"),
    ("../../etc/passwd", "etc-passwd"),
    ("¡Hólà, qué tal! 😀", "hola-que-tal"),
    ("  ///  ", "clip"),            # queda vacío → fallback
    ("", "clip"),
    ("a" * 80, "a" * 50),           # cap de longitud
])
def test_slugify(title, expected):
    assert export.slugify(title) == expected


def test_slugify_never_contains_path_separators():
    for hostile in ["a/b", "a\\b", "..", "a\x00b", "../../x"]:
        assert "/" not in export.slugify(hostile)
        assert "\\" not in export.slugify(hostile)


# --- export_clips ----------------------------------------------------------------------

def test_export_to_all_platforms_by_default(iso):
    make_upload()
    make_clips()
    out = export.export_clips(VALID_ID)

    assert out["platforms"] == list(config.EXPORT_SUBDIRS)
    assert out["n_clips"] == 2
    short = VALID_ID[:8]
    for platform in config.EXPORT_SUBDIRS:
        files = sorted((config.EXPORTS_DIR / platform).glob(f"{short}_*"))
        assert len(files) == 2
        assert files[0].name == f"{short}_01_gancho-brutal.mp4"
    # índice per-upload escrito y metadata avanzada.
    assert (config.CLIPS_DIR / VALID_ID / "export.json").is_file()
    meta = json.loads((config.UPLOADS_DIR / VALID_ID / "meta.json").read_text())
    assert meta["status"] == "exported"


def test_export_subset_of_platforms(iso):
    make_upload()
    make_clips()
    out = export.export_clips(VALID_ID, platforms=["tiktok"])
    assert out["platforms"] == ["tiktok"]
    short = VALID_ID[:8]
    assert list((config.EXPORTS_DIR / "tiktok").glob(f"{short}_*"))
    assert not list((config.EXPORTS_DIR / "reels").glob(f"{short}_*"))


def test_export_unknown_platform_raises(iso):
    make_upload()
    make_clips()
    with pytest.raises(export.ExportError, match="Plataforma desconocida"):
        export.export_clips(VALID_ID, platforms=["../../etc"])


def test_export_is_idempotent_and_scoped(iso):
    make_upload()
    make_clips(clips=[
        {"id": 1, "score": 90, "title": "Primero"},
        {"id": 2, "score": 80, "title": "Segundo"},
    ])
    export.export_clips(VALID_ID)
    # Un archivo de OTRO upload en la misma carpeta no debe tocarse.
    foreign = config.EXPORTS_DIR / "shorts" / "deadbeef_01_otro.mp4"
    foreign.write_bytes(b"x")

    # Re-exportar con menos clips y títulos nuevos: los viejos de ESTE upload se limpian.
    make_clips(clips=[{"id": 1, "score": 95, "title": "Nuevo unico"}])
    export.export_clips(VALID_ID)

    short = VALID_ID[:8]
    mine = sorted((config.EXPORTS_DIR / "shorts").glob(f"{short}_*"))
    assert [f.name for f in mine] == [f"{short}_01_nuevo-unico.mp4"]  # sin slugs viejos
    assert foreign.is_file()  # el de otro upload sobrevive


def test_export_missing_clip_file_raises(iso):
    make_upload()
    make_clips()
    # Borrar el MP4 de un clip listado en clips.json.
    (config.CLIPS_DIR / VALID_ID / "clip_1.mp4").unlink()
    with pytest.raises(export.ExportError, match="Falta el archivo del clip"):
        export.export_clips(VALID_ID)


def test_export_missing_clip_does_not_touch_previous_export(iso):
    # Un export previo bueno no debe destruirse si una re-corrida tiene un source faltante:
    # la validación corre ANTES de la limpieza idempotente.
    make_upload()
    make_clips()
    export.export_clips(VALID_ID)
    short = VALID_ID[:8]
    before = sorted(p.name for p in (config.EXPORTS_DIR / "shorts").glob(f"{short}_*"))
    assert len(before) == 2

    (config.CLIPS_DIR / VALID_ID / "clip_2.mp4").unlink()  # falta un clip
    with pytest.raises(export.ExportError, match="Falta el archivo del clip"):
        export.export_clips(VALID_ID)
    after = sorted(p.name for p in (config.EXPORTS_DIR / "shorts").glob(f"{short}_*"))
    assert after == before  # el export previo quedó intacto


def test_export_without_clips_raises(iso):
    make_upload()  # sin clips.json
    with pytest.raises(export.ExportError, match="no tiene clips"):
        export.export_clips(VALID_ID)


def test_export_unknown_upload_raises(iso):
    with pytest.raises(export.ExportError, match="no encontrada"):
        export.export_clips(VALID_ID)


def test_load_export_invalid_id_returns_none(iso):
    assert export.load_export("../etc/passwd") is None
    assert export.load_export(VALID_ID) is None
