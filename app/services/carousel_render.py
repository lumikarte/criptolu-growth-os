"""Render del carrusel a imágenes PNG de marca (CRI-604).

Toma `content.carousel` de ``data/clips/<id>/repurpose.json`` (texto: título + slides con
heading/body) y lo materializa como slides PNG 1080×1350 con la identidad visual de CriptoLú
(fondo morado, acento magenta, tipografía DejaVu bundleada), listas para subir a IG/LinkedIn.

Render propio con **Pillow**: liviano, sin browser headless, determinista (mismo texto +
misma plantilla ⇒ mismos bytes), control total de marca. Igual espíritu que el `force_style`
de libass en ``clipping.py``. La dep (PIL) se importa de forma perezosa, como el SDK
``anthropic`` en ``detection.py``: el resto del pipeline no la necesita.

Salida en ``data/clips/<id>/carousel/``:
  - slide_00.png            portada (title)
  - slide_01.png … slide_NN.png   una por slide (heading + body)
  - carousel.json           índice
"""

from __future__ import annotations

import json

from .. import config
from . import repurpose, storage


class CarouselError(RuntimeError):
    """Falla de validación al renderizar (se traduce a HTTP 400 en el router)."""


class UpstreamError(CarouselError):
    """Dependencia faltante o rota (Pillow/fuente) → HTTP 502."""


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    """'#RRGGBB' → (r, g, b). Acepta con o sin '#'."""
    h = value.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _load_font(path: str, size: int):
    """Carga una TrueType con el tamaño dado; cae a la fuente por defecto de PIL si falta."""
    from PIL import ImageFont  # import perezoso
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        # Sin la fuente bundleada el texto no queda con el tamaño de marca, pero no reventamos.
        return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    """Parte `text` en líneas que caben en `max_w` px (word-wrap; PIL no lo hace solo)."""
    words = text.split()
    if not words:
        return [""]
    lines, cur = [], words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _fit(draw, text: str, font_path: str, size: int, max_w: int, max_h: int, line_gap: float):
    """Devuelve (font, lines) reduciendo el tamaño hasta que el texto entre en max_w×max_h.

    Nunca baja de CAROUSEL_MIN_FONT_SIZE; si aun así no entra, trunca la última línea con '…'.
    """
    s = size
    while s >= config.CAROUSEL_MIN_FONT_SIZE:
        font = _load_font(font_path, s)
        lines = _wrap(draw, text, font, max_w)
        line_h = int(s * line_gap)
        if len(lines) * line_h <= max_h:
            return font, lines
        s -= 4
    # Piso alcanzado: recortar a las líneas que entran y marcar con '…'.
    font = _load_font(font_path, config.CAROUSEL_MIN_FONT_SIZE)
    lines = _wrap(draw, text, font, max_w)
    line_h = int(config.CAROUSEL_MIN_FONT_SIZE * line_gap)
    keep = max(1, max_h // line_h)
    if len(lines) > keep:
        lines = lines[:keep]
        lines[-1] = lines[-1].rstrip(".") + "…"
    return font, lines


def _draw_lines(draw, lines, font, x: int, y: int, fill, line_gap: float) -> int:
    """Dibuja las líneas apiladas desde (x, y). Devuelve la y final."""
    line_h = int(font.size * line_gap)
    for ln in lines:
        draw.text((x, y), ln, font=font, fill=fill)
        y += line_h
    return y


def _render_slide(*, kind: str, index: int, total: int, heading: str, body: str) -> bytes:
    """Renderiza una slide (portada o contenido) a PNG y devuelve sus bytes."""
    from PIL import Image, ImageDraw

    W, H, M = config.CAROUSEL_WIDTH, config.CAROUSEL_HEIGHT, config.CAROUSEL_MARGIN
    bg = _hex_to_rgb(config.CAROUSEL_BG_COLOR)
    accent = _hex_to_rgb(config.CAROUSEL_ACCENT_COLOR)
    text_c = _hex_to_rgb(config.CAROUSEL_TEXT_COLOR)
    muted = _hex_to_rgb(config.CAROUSEL_MUTED_COLOR)

    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    max_w = W - 2 * M

    # Barra de acento arriba a la izquierda (firma visual de marca).
    draw.rectangle([M, M, M + 120, M + 12], fill=accent)

    if kind == "cover":
        font, lines = _fit(draw, heading, config.CAROUSEL_FONT_BOLD,
                           config.CAROUSEL_TITLE_SIZE, max_w, H - 2 * M - 200, 1.2)
        # Centrado vertical aproximado del título.
        line_h = int(font.size * 1.2)
        y = max(M + 120, (H - len(lines) * line_h) // 2)
        _draw_lines(draw, lines, font, M, y, text_c, 1.2)
    else:
        y = M + 80
        hfont, hlines = _fit(draw, heading, config.CAROUSEL_FONT_BOLD,
                             config.CAROUSEL_HEADING_SIZE, max_w, 400, 1.15)
        y = _draw_lines(draw, hlines, hfont, M, y, accent, 1.15)
        y += 40
        bfont, blines = _fit(draw, body, config.CAROUSEL_FONT_REG,
                             config.CAROUSEL_BODY_SIZE, max_w, H - y - M - 80, 1.35)
        _draw_lines(draw, blines, bfont, M, y, text_c, 1.35)

    # Pie: marca + numeración n/N.
    foot = _load_font(config.CAROUSEL_FONT_BOLD, 32)
    draw.text((M, H - M), "CriptoLú", font=foot, fill=muted)
    if total > 1:
        num = f"{index + 1}/{total}"
        nw = draw.textlength(num, font=foot)
        draw.text((W - M - nw, H - M), num, font=foot, fill=muted)

    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_carousel(upload_id: str) -> dict:
    """Renderiza las slides del carrusel de una subida y guarda las PNG + carousel.json.

    Devuelve {upload_id, template_version, width, height, n_slides, title, slides:[...]}.
    Lanza CarouselError si no hay repurpose (400); UpstreamError si falta Pillow (502).
    """
    if not storage.valid_upload_id(upload_id):
        raise CarouselError(f"upload_id inválido: '{upload_id}'.")
    pkg = repurpose.load_repurpose(upload_id)
    if pkg is None:
        raise CarouselError(
            f"La subida '{upload_id}' no tiene contenido repurposado. Generalo primero."
        )
    carousel = (pkg.get("content") or {}).get("carousel") or {}
    title = (carousel.get("title") or "").strip()
    slides = carousel.get("slides") or []
    if not title or not slides:
        raise CarouselError("El carrusel del repurpose no tiene título o slides.")

    try:
        import PIL  # noqa: F401  (verifica disponibilidad antes de trabajar)
    except ImportError as e:
        raise UpstreamError(
            "Falta Pillow para renderizar el carrusel: 'pip install Pillow' "
            "(o revisá requirements.txt)."
        ) from e

    out_dir = config.CLIPS_DIR / upload_id / "carousel"
    total = len(slides) + 1  # +1 por la portada

    # Renderizar TODO en memoria antes de tocar el disco: si una slide falla, no dejamos
    # PNGs borrados ni un carousel.json colgando (índice inconsistente). Recién con todas
    # las slides listas, limpiamos las viejas y escribimos las nuevas + el índice.
    rendered: list[tuple[str, bytes, dict]] = []
    cover_name = "slide_00.png"
    cover_png = _render_slide(kind="cover", index=0, total=total, heading=title, body="")
    rendered.append((cover_name, cover_png,
                     {"index": 0, "filename": cover_name, "file": str(out_dir / cover_name),
                      "kind": "cover", "heading": title, "body": ""}))
    for i, s in enumerate(slides, start=1):
        heading = (s.get("heading") or "").strip()
        body = (s.get("body") or "").strip()
        png = _render_slide(kind="slide", index=i, total=total, heading=heading, body=body)
        name = f"slide_{i:02d}.png"
        rendered.append((name, png,
                         {"index": i, "filename": name, "file": str(out_dir / name),
                          "kind": "slide", "heading": heading, "body": body}))

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("slide_*.png"):  # limpiar sobrantes si ahora hay menos slides
        old.unlink()
    for name, png, _ in rendered:
        storage.atomic_write_bytes(out_dir / name, png)
    index = [entry for _, _, entry in rendered]

    payload = {
        "upload_id": upload_id,
        "template_version": config.CAROUSEL_TEMPLATE_VERSION,
        "width": config.CAROUSEL_WIDTH,
        "height": config.CAROUSEL_HEIGHT,
        "n_slides": len(index),
        "title": title,
        "slides": index,
    }
    storage.atomic_write_text(
        out_dir / "carousel.json", json.dumps(payload, ensure_ascii=False, indent=2)
    )
    storage.update_metadata(upload_id, {
        "carousel_render": {"dir": str(out_dir), "n_slides": len(index)},
    })
    return payload


def load_carousel(upload_id: str) -> dict | None:
    """Devuelve carousel.json de una subida, o None si no existe (o el id es inválido)."""
    if not storage.valid_upload_id(upload_id):
        return None
    f = config.CLIPS_DIR / upload_id / "carousel" / "carousel.json"
    if not f.is_file():
        return None
    return json.loads(f.read_text(encoding="utf-8"))
