"""Repropósito multi-formato desde la transcripción (W-03, CRI-562).

El DIFERENCIAL del producto: de UNA sola fuente (la transcripción del episodio) el LLM
genera un paquete de contenido de texto listo para varias redes — no solo clips (eso ya
lo hace OpusClip). Produce, en ``data/clips/<id>/repurpose.json``:
  - carousel   : un carrusel de slides (título + 5-8 slides con encabezado y cuerpo).
  - feed_post  : un post de feed (texto largo + hashtags).
  - thread     : un hilo para X/Threads (lista de posts cortos).
  - captions   : un caption adaptado por red (tiktok, reels, shorts, instagram_feed, x).

Motor enchufable (ENGINES), idéntico a la detección de momentos:
  - 'groq'   → Llama 3.3 70B vía Groq (default; reusa GROQ_API_KEY).
  - 'claude' → Claude (Anthropic; requiere ANTHROPIC_API_KEY, SDK perezoso).
Ambos devuelven el mismo objeto, así que el resto del pipeline no cambia.

La voz de marca (W-04, CRI-563) se inyecta como texto opcional (`brand_voice`) en el
prompt: acá queda el hook; el perfil de marca de 1 página se conecta en ese ticket.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .. import config
from . import brand, storage, transcription

# Groq está detrás de Cloudflare y bloquea el User-Agent por defecto de Python (error
# 1010); con un UA de navegador las requests pasan (igual que en detection/transcription).
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Rúbrica versionada: qué hace bueno a cada formato. Reproducible y auditable.
CRITERIA_VERSION = "1.0"
REPURPOSE_RUBRIC = """\
De esta transcripción de podcast generá UN paquete de contenido en varios formatos, todos
sobre las MISMAS ideas centrales del episodio. Reglas por formato:

CARRUSEL (carousel): un título gancho + entre {min_slides} y {max_slides} slides. Cada
slide: un `heading` corto (1 línea, idea sola) y un `body` de 1-2 oraciones que la
desarrolla. Slide 1 = gancho, última slide = cierre con llamado a la acción suave.

POST DE FEED (feed_post): un `text` autoconclusivo (150-400 palabras) que cuente la idea
más fuerte del episodio con un gancho en la primera línea, y una lista `hashtags` de 3-6
etiquetas relevantes (sin el símbolo #, solo la palabra).

HILO (thread): una lista `posts` de hasta {max_thread} entradas. Post 1 con gancho; cada
post ≤ {thread_chars} caracteres, autoconclusivo pero encadenado. Sin numerar (el número
lo pone la app).

CAPTIONS por red: un caption adaptado al tono y largo de cada red pedida. TikTok/Reels/
Shorts: 1-2 líneas punchy + CTA. instagram_feed: 2-4 líneas. x: ≤ {thread_chars} chars.

Escribí en el idioma de la transcripción, en español LatAm natural (nada de traducción
robótica). No inventes datos que no estén en la transcripción."""

SYSTEM_PROMPT = (
    "Sos el editor de contenido multi-formato de CriptoLú: convertís un episodio en "
    "piezas de texto listas para publicar en varias redes, con voz de marca."
)

# Esquema del objeto que debe devolver el LLM (structured outputs de Claude). Los rangos
# y el recorte se validan en código; acá solo la forma.
REPURPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "carousel": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "slides": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string"},
                            "body": {"type": "string"},
                        },
                        "required": ["heading", "body"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["title", "slides"],
            "additionalProperties": False,
        },
        "feed_post": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "hashtags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["text", "hashtags"],
            "additionalProperties": False,
        },
        "thread": {
            "type": "object",
            "properties": {
                "posts": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["posts"],
            "additionalProperties": False,
        },
        "captions": {
            "type": "object",
            "properties": {n: {"type": "string"} for n in config.REPURPOSE_NETWORKS},
            "required": list(config.REPURPOSE_NETWORKS),
            "additionalProperties": False,
        },
    },
    "required": ["carousel", "feed_post", "thread", "captions"],
    "additionalProperties": False,
}


class RepurposeError(RuntimeError):
    """Falla de validación al repurposar (se traduce a HTTP 400 en el router)."""


class UpstreamError(RepurposeError):
    """Falla de la dependencia externa (LLM caído, red, key/SDK faltante) → HTTP 502."""


def _format_segments(transcript: dict) -> str:
    """Texto plano de la transcripción para que el LLM entienda el episodio."""
    segs = transcript.get("segments") or []
    return "\n".join((s.get("text") or "").strip() for s in segs)


def build_prompt(transcript: dict, *, brand_voice: str | None = None) -> str:
    """Arma el prompt (rúbrica + voz de marca opcional + transcripción)."""
    rubric = REPURPOSE_RUBRIC.format(
        min_slides=config.REPURPOSE_MIN_SLIDES,
        max_slides=config.REPURPOSE_MAX_SLIDES,
        max_thread=config.REPURPOSE_MAX_THREAD_POSTS,
        thread_chars=config.REPURPOSE_THREAD_CHAR_LIMIT,
    )
    voice = f"\n\nVOZ DE MARCA (respetala en cada texto):\n{brand_voice.strip()}\n" if brand_voice else ""
    networks = ", ".join(config.REPURPOSE_NETWORKS)
    return (
        f"{rubric}{voice}\n\n"
        f"Redes para las que necesito caption: {networks}.\n\n"
        f"Devolvé UN objeto JSON con las claves: carousel, feed_post, thread, captions.\n\n"
        f"Transcripción (idioma {transcript.get('language')}):\n"
        f"{_format_segments(transcript)}\n"
    )


# --------------------------------------------------------------------------- #
# Motores (devuelven el objeto JSON completo, ya parseado)
# --------------------------------------------------------------------------- #

def _engine_groq(prompt: str) -> dict:
    """Genera el paquete con Llama 3.3 70B vía Groq (chat completions, JSON mode)."""
    if not config.GROQ_API_KEY:
        raise UpstreamError("Falta GROQ_API_KEY. Ponela en editorpro/.env (GROQ_API_KEY=gsk_...).")

    payload = json.dumps({
        "model": config.GROQ_LLM_MODEL,
        "temperature": 0.6,
        "max_tokens": config.REPURPOSE_MAX_TOKENS,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        config.GROQ_CHAT_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {config.GROQ_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": _BROWSER_UA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise UpstreamError(f"Groq HTTP {e.code}: {e.read().decode()[:300]}") from e
    except urllib.error.URLError as e:
        raise UpstreamError(f"Red al contactar Groq: {e}") from e

    # El envelope y el contenido vienen del upstream: un JSON truncado o una forma
    # inesperada es una falla de la dependencia (→ 502), no del cliente.
    try:
        return json.loads(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        raise UpstreamError(f"Respuesta de Groq inesperada o no-JSON: {e}") from e


def _engine_claude(prompt: str) -> dict:
    """Genera el paquete con Claude (Anthropic) usando structured outputs. SDK perezoso."""
    if not config.ANTHROPIC_API_KEY:
        raise UpstreamError(
            "Falta ANTHROPIC_API_KEY. Ponela en editorpro/.env para usar el motor 'claude'."
        )
    try:
        import anthropic
    except ImportError as e:
        raise UpstreamError(
            "Motor 'claude' no disponible: falta el SDK. Instalá con 'pip install anthropic' "
            "(o usá el motor 'groq', que ya corre)."
        ) from e

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    try:
        response = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=8192,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            output_config={"format": {"type": "json_schema", "schema": REPURPOSE_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as e:
        raise UpstreamError(f"Error de la API de Anthropic: {e}") from e
    if response.stop_reason == "refusal":
        raise RepurposeError("Claude rechazó la petición por seguridad.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise UpstreamError(f"Respuesta de Claude no-JSON o truncada: {e}") from e


ENGINES = {
    "groq": _engine_groq,      # Llama 3.3 70B vía Groq — motor activo (default)
    "claude": _engine_claude,  # Claude (Anthropic) — requiere ANTHROPIC_API_KEY
}


# --------------------------------------------------------------------------- #
# Validación / normalización del texto que devuelve el LLM
# --------------------------------------------------------------------------- #

def _clean_str(value, field: str) -> str:
    """Devuelve `value` como string no vacío y trimmeado, o lanza RepurposeError."""
    if not isinstance(value, str) or not value.strip():
        raise RepurposeError(f"Campo '{field}' vacío o no textual.")
    return value.strip()


def _validate_carousel(raw) -> dict:
    if not isinstance(raw, dict):
        raise RepurposeError("'carousel' faltante o con forma inválida.")
    title = _clean_str(raw.get("title"), "carousel.title")
    slides_raw = raw.get("slides")
    if not isinstance(slides_raw, list) or not slides_raw:
        raise RepurposeError("'carousel.slides' debe ser una lista no vacía.")
    slides = []
    for i, s in enumerate(slides_raw, 1):
        if not isinstance(s, dict):
            raise RepurposeError(f"Slide #{i} con forma inválida.")
        slides.append({
            "heading": _clean_str(s.get("heading"), f"slide[{i}].heading"),
            "body": _clean_str(s.get("body"), f"slide[{i}].body"),
        })
    if len(slides) < config.REPURPOSE_MIN_SLIDES:
        raise RepurposeError(
            f"El carrusel trae {len(slides)} slides; el mínimo es {config.REPURPOSE_MIN_SLIDES}."
        )
    slides = slides[:config.REPURPOSE_MAX_SLIDES]  # acotar si el LLM se pasó
    return {"title": title, "slides": slides}


def _validate_feed_post(raw) -> dict:
    if not isinstance(raw, dict):
        raise RepurposeError("'feed_post' faltante o con forma inválida.")
    text = _clean_str(raw.get("text"), "feed_post.text")
    tags_raw = raw.get("hashtags") or []
    if not isinstance(tags_raw, list):
        raise RepurposeError("'feed_post.hashtags' debe ser una lista.")
    # normalizar: sacar el '#', trimmear, descartar vacíos, deduplicar preservando orden.
    seen, tags = set(), []
    for t in tags_raw:
        if not isinstance(t, str):
            continue
        tag = t.strip().lstrip("#").strip()
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    return {"text": text, "hashtags": tags}


def _validate_thread(raw) -> dict:
    if not isinstance(raw, dict):
        raise RepurposeError("'thread' faltante o con forma inválida.")
    posts_raw = raw.get("posts")
    if not isinstance(posts_raw, list) or not posts_raw:
        raise RepurposeError("'thread.posts' debe ser una lista no vacía.")
    posts = [_clean_str(p, f"thread.posts[{i}]") for i, p in enumerate(posts_raw, 1)]
    return {"posts": posts[:config.REPURPOSE_MAX_THREAD_POSTS]}


def _validate_captions(raw) -> dict:
    if not isinstance(raw, dict):
        raise RepurposeError("'captions' faltante o con forma inválida.")
    captions = {}
    for net in config.REPURPOSE_NETWORKS:
        captions[net] = _clean_str(raw.get(net), f"captions.{net}")
    return captions


def _validate_package(raw: dict) -> dict:
    """Valida y normaliza el objeto del LLM. Lanza RepurposeError si algo falta o está mal."""
    if not isinstance(raw, dict):
        raise RepurposeError("El LLM no devolvió un objeto JSON.")
    return {
        "carousel": _validate_carousel(raw.get("carousel")),
        "feed_post": _validate_feed_post(raw.get("feed_post")),
        "thread": _validate_thread(raw.get("thread")),
        "captions": _validate_captions(raw.get("captions")),
    }


def repurpose_upload(
    upload_id: str, *, engine: str | None = None, brand_voice: str | None = None,
) -> dict:
    """Genera el paquete multi-formato de una subida ya transcrita y guarda repurpose.json.

    Devuelve {upload_id, engine, model, criteria_version, networks, content}.
    Lanza RepurposeError si no hay transcripción, el motor falla o el LLM devuelve basura.
    """
    transcript = transcription.load_transcript(upload_id)
    if transcript is None:
        raise RepurposeError(
            f"La subida '{upload_id}' no tiene transcripción. Transcribila primero."
        )
    segments = transcript.get("segments") or []
    if not segments:
        raise RepurposeError("La transcripción no tiene segmentos para repurposar.")

    eng = engine or config.REPURPOSE_DEFAULT_ENGINE
    if eng not in ENGINES:
        raise RepurposeError(f"Motor desconocido '{eng}'. Disponibles: {', '.join(ENGINES)}")

    # Voz de marca (W-04): si no se pasa una explícita, se inyecta el perfil guardado
    # (o el default). Así cada caption suena como la marca sin pedirlo en cada llamada.
    voice = brand_voice if brand_voice is not None else brand.load_brand_voice()
    prompt = build_prompt(transcript, brand_voice=voice)
    raw = ENGINES[eng](prompt)
    content = _validate_package(raw)

    model = config.GROQ_LLM_MODEL if eng == "groq" else config.CLAUDE_MODEL
    payload = {
        "upload_id": upload_id,
        "engine": eng,
        "model": model,
        "criteria_version": CRITERIA_VERSION,
        "networks": list(config.REPURPOSE_NETWORKS),
        "brand_voice": {
            "applied": bool(voice),
            "customized": brand_voice is None and brand.is_customized(),
        },
        "content": content,
    }
    out_dir = config.CLIPS_DIR / upload_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "repurpose.json"
    storage.atomic_write_text(out_file, json.dumps(payload, ensure_ascii=False, indent=2))

    storage.update_metadata(upload_id, {
        "status": "repurposed",
        "repurpose": {"file": str(out_file), "engine": eng},
    })
    return payload


def load_repurpose(upload_id: str) -> dict | None:
    """Devuelve repurpose.json de una subida, o None si no existe (o el id es inválido)."""
    if not storage.valid_upload_id(upload_id):
        return None
    out_file = config.CLIPS_DIR / upload_id / "repurpose.json"
    if not out_file.is_file():
        return None
    return json.loads(out_file.read_text(encoding="utf-8"))
