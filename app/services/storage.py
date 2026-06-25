"""Almacenamiento de archivos subidos + metadata (FP-MVP-02).

Cubre CRI-259 (validación), CRI-260 (guardar local) y CRI-261 (registrar metadata).
Cada subida vive en data/uploads/<id>/ con el archivo `source.<ext>` y `meta.json`.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from .. import config

_CHUNK = 1024 * 1024  # 1 MB


class UploadError(ValueError):
    """Error de validación de una subida (se traduce a HTTP 400)."""


def _ext(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def validate_filename(filename: str) -> str:
    """Valida la extensión antes de escribir nada. Devuelve la extensión normalizada."""
    ext = _ext(filename)
    if ext not in config.ALLOWED_UPLOAD_EXT:
        permitidas = ", ".join(sorted(config.ALLOWED_UPLOAD_EXT))
        raise UploadError(
            f"Extensión no permitida: '{ext or '(sin extensión)'}'. Permitidas: {permitidas}"
        )
    return ext


async def save_upload(file: UploadFile) -> dict:
    """Valida, guarda en streaming y registra metadata. Devuelve el dict de metadata.

    Hace streaming por chunks para no cargar archivos grandes en memoria y para poder
    abortar si superan el máximo permitido.
    """
    if not file.filename:
        raise UploadError("No se recibió ningún archivo.")
    ext = validate_filename(file.filename)

    upload_id = uuid4().hex
    dest_dir = config.UPLOADS_DIR / upload_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    stored = dest_dir / f"source{ext}"

    size = 0
    try:
        with stored.open("wb") as out:
            while chunk := await file.read(_CHUNK):
                size += len(chunk)
                if size > config.MAX_UPLOAD_BYTES:
                    raise UploadError(
                        f"Archivo demasiado grande: supera el máximo de "
                        f"{config.MAX_UPLOAD_BYTES // (1024 ** 2)} MB."
                    )
                out.write(chunk)
        if size == 0:
            raise UploadError("El archivo está vacío.")
    except Exception:
        shutil.rmtree(dest_dir, ignore_errors=True)  # no dejar subidas a medias
        raise

    meta = {
        "id": upload_id,
        "original_filename": file.filename,
        "stored_path": str(stored),
        "ext": ext,
        "content_type": file.content_type,
        "size_bytes": size,
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "uploaded",
    }
    (dest_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


def load_metadata(upload_id: str) -> dict | None:
    """Devuelve la metadata de una subida, o None si no existe."""
    meta_file = config.UPLOADS_DIR / upload_id / "meta.json"
    if not meta_file.is_file():
        return None
    return json.loads(meta_file.read_text(encoding="utf-8"))


def list_uploads() -> list[dict]:
    """Lista la metadata de todas las subidas, más recientes primero."""
    if not config.UPLOADS_DIR.is_dir():
        return []
    metas = []
    for d in config.UPLOADS_DIR.iterdir():
        if d.is_dir() and (d / "meta.json").is_file():
            metas.append(json.loads((d / "meta.json").read_text(encoding="utf-8")))
    return sorted(metas, key=lambda m: m.get("uploaded_at", ""), reverse=True)
