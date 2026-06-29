"""Export de clips a carpetas por plataforma con naming consistente (PP-MVP-03).

Último paso del MVP: toma los clips cortados (``data/clips/<id>/clip_N.mp4`` vía
``clips.json``) y los publica en ``data/exports/{shorts,reels,tiktok}/`` con un nombre
descriptivo y seguro. Las tres plataformas comparten el mismo MP4 vertical 9:16, así que
exportar es organizar + nombrar (``shutil.copy2``), sin re-encodear.

Seguridad (el ``title`` viene del LLM, texto arbitrario):
  - las plataformas se validan por PERTENENCIA a ``config.EXPORT_SUBDIRS`` (nunca se arma
    una ruta con el string crudo del query);
  - el slug del título es WHITELIST ASCII (normalizar → ascii → ``[a-z0-9-]`` → cap →
    fallback), así un título con ``../`` o unicode no puede derivar el nombre;
  - defensa en profundidad: el destino se ``resolve()`` y se verifica que quede dentro de
    la carpeta de la plataforma antes de copiar.

Idempotencia: al re-exportar se borran SOLO los archivos de ESTE upload (prefijo
``<idcorto>_``) en cada carpeta —compartida entre uploads—, nunca la carpeta entera.
"""

from __future__ import annotations

import json
import re
import shutil
import unicodedata
from pathlib import Path

from .. import config
from . import clipping, storage

# Longitud máxima del slug del título en el nombre de archivo (legibilidad + límites de FS).
_SLUG_MAX = 50


class ExportError(RuntimeError):
    """Falla de validación al exportar (se traduce a HTTP 400 en el router)."""


def slugify(text: str) -> str:
    """Convierte un título arbitrario en un slug ASCII seguro para nombre de archivo.

    Whitelist (no blacklist): normaliza unicode, descarta lo no-ASCII, deja solo
    ``[a-z0-9]`` separado por guiones, recorta y cae en 'clip' si queda vacío.
    """
    norm = unicodedata.normalize("NFKD", text or "")
    ascii_text = norm.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    slug = slug[:_SLUG_MAX].strip("-")
    return slug or "clip"


def _resolve_platforms(platforms: list[str] | None) -> list[str]:
    """Valida las plataformas pedidas contra EXPORT_SUBDIRS (default: todas)."""
    valid = list(config.EXPORT_SUBDIRS)
    if not platforms:
        return valid
    allowed = set(valid)
    chosen: list[str] = []
    for p in platforms:
        name = (p or "").strip().lower()
        if name not in allowed:
            raise ExportError(
                f"Plataforma desconocida '{name}'. Válidas: {', '.join(valid)}"
            )
        if name not in chosen:
            chosen.append(name)
    return chosen


def export_clips(upload_id: str, *, platforms: list[str] | None = None) -> dict:
    """Copia los clips de una subida a las carpetas de plataforma y escribe export.json.

    Devuelve el índice {upload_id, platforms, n_clips, exports}. Lanza ExportError si la
    subida no existe, no tiene clips, una plataforma es inválida o falta un clip en disco.
    """
    meta = storage.load_metadata(upload_id)
    if meta is None:
        raise ExportError(f"Subida '{upload_id}' no encontrada.")

    clips_idx = clipping.load_clips(upload_id)
    if clips_idx is None:
        raise ExportError(
            f"La subida '{upload_id}' no tiene clips. Cortalos con /clips primero."
        )
    clips = clips_idx.get("clips") or []
    if not clips:
        raise ExportError("No hay clips para exportar (clips.json sin clips).")

    targets = _resolve_platforms(platforms)
    short_id = upload_id[:8]  # hex seguro (upload_id ya validado por load_metadata)

    # Pre-validar TODOS los clips ANTES de borrar/copiar nada: si falta un source, fallamos
    # sin haber tocado exports/ (no dejar la carpeta en estado parcial). El nombre es el
    # mismo para todas las plataformas, así que se calcula una sola vez por clip.
    prepared: list[tuple[int, Path, str, dict]] = []
    for c in clips:
        try:
            cid = int(c["id"])
        except (KeyError, TypeError, ValueError) as e:
            raise ExportError(f"clips.json con un clip inválido: {e}") from e
        src = Path(c.get("file") or "")
        if not src.is_file():
            raise ExportError(f"Falta el archivo del clip {cid}: {src}")
        name = f"{short_id}_{cid:02d}_{slugify(c.get('title', ''))}.mp4"
        prepared.append((cid, src, name, c))

    exported: dict[str, list[dict]] = {}
    for platform in targets:
        dest_dir = config.EXPORTS_DIR / platform
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_root = dest_dir.resolve()
        # Limpieza idempotente: borrar SOLO los exports previos de este upload (la carpeta
        # es compartida entre uploads), para no dejar slugs viejos al re-exportar.
        for old in dest_dir.glob(f"{short_id}_*"):
            if old.is_file():
                old.unlink()

        items: list[dict] = []
        for cid, src, name, c in prepared:
            out = (dest_dir / name).resolve()
            # Defensa en profundidad: el nombre ya está saneado, pero confirmamos que el
            # destino no escapa de la carpeta de la plataforma antes de escribir.
            if not out.is_relative_to(dest_root):
                raise ExportError(f"Ruta de export fuera de la carpeta de plataforma: {out}")
            shutil.copy2(src, out)
            items.append({
                "clip_id": cid,
                "filename": name,
                "file": str(out),
                "title": c.get("title", ""),
                "score": c.get("score"),
            })
        exported[platform] = items

    payload = {
        "upload_id": upload_id,
        "platforms": targets,
        "n_clips": len(clips),
        "exports": exported,
    }
    out_file = config.CLIPS_DIR / upload_id / "export.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    storage.atomic_write_text(out_file, json.dumps(payload, ensure_ascii=False, indent=2))

    storage.update_metadata(upload_id, {
        "status": "exported",
        "export": {"platforms": targets, "n_clips": len(clips), "index": str(out_file)},
    })
    return payload


def load_export(upload_id: str) -> dict | None:
    """Devuelve export.json de una subida, o None si no existe (o el id es inválido)."""
    if not storage.valid_upload_id(upload_id):
        return None
    out_file = config.CLIPS_DIR / upload_id / "export.json"
    if not out_file.is_file():
        return None
    return json.loads(out_file.read_text(encoding="utf-8"))
