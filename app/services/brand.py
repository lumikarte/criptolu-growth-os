"""Voz de marca (W-04, CRI-563).

Un perfil de marca de 1 página (tono, hacé/no hagás, ejemplos) que se inyecta en cada
caption del repropósito para que el contenido suene como la marca. Es "lite" a propósito:
el moat vive como PROMPT, no como módulo. Single-tenant → un solo perfil, en un archivo
editable (``data/brand_voice.md``, gitignored). Si el archivo no existe, se usa el default
de ``config.DEFAULT_BRAND_VOICE``.
"""

from __future__ import annotations

from .. import config
from . import storage


class BrandError(ValueError):
    """Perfil de marca inválido (se traduce a HTTP 400 en el router)."""


def load_brand_voice() -> str:
    """Devuelve el perfil de marca guardado, o el default si no hay archivo.

    Lee ``config.BRAND_VOICE_FILE`` en runtime (no al importar) para respetar los overrides
    de test. Un archivo vacío o solo-espacios cuenta como "no configurado" → default.
    """
    path = config.BRAND_VOICE_FILE
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return config.DEFAULT_BRAND_VOICE.strip()


def is_customized() -> bool:
    """True si hay un perfil propio guardado (no el default)."""
    path = config.BRAND_VOICE_FILE
    return path.is_file() and bool(path.read_text(encoding="utf-8").strip())


def save_brand_voice(text: str) -> str:
    """Valida y persiste el perfil de marca. Devuelve el texto guardado (trimmeado).

    Lanza BrandError si viene vacío o supera el tope de tamaño.
    """
    if not isinstance(text, str) or not text.strip():
        raise BrandError("El perfil de marca no puede estar vacío.")
    clean = text.strip()
    if len(clean) > config.BRAND_VOICE_MAX_CHARS:
        raise BrandError(
            f"El perfil de marca supera el máximo de {config.BRAND_VOICE_MAX_CHARS} caracteres."
        )
    config.BRAND_VOICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    storage.atomic_write_text(config.BRAND_VOICE_FILE, clean)
    return clean
