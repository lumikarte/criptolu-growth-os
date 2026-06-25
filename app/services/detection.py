"""Detección de mejores momentos para clips verticales (PP-MVP-01).

Le pasa la transcripción a un LLM para que elija los momentos que funcionan como clip
vertical independiente (TikTok/Reels/Shorts), cada uno con timestamps, score (0-100),
título, motivo y frase citable. Guarda el resultado en data/clips/<id>/moments.json.

Motor enchufable (ENGINES):
  - 'groq'   → Llama 3.3 70B vía la API de Groq (default; reusa GROQ_API_KEY, corre hoy).
  - 'claude' → Claude (API de Anthropic, mejor criterio editorial; requiere
               ANTHROPIC_API_KEY). Usa el SDK oficial `anthropic` (import perezoso, así
               el motor Groq no necesita la dependencia).
Ambos devuelven la misma estructura de clips, así que el resto del pipeline no cambia.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .. import config
from . import storage, transcription

# Groq está detrás de Cloudflare y bloquea el User-Agent por defecto de Python
# (error 1010); con un UA de navegador las requests pasan (igual que en transcription).
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Rúbrica editorial: qué hace que un momento sea un buen clip vertical. Versionada acá
# para que el criterio sea reproducible y auditable.
CRITERIA_VERSION = "1.0"
DETECTION_RUBRIC = """\
Elegí los momentos que funcionan como CLIP VERTICAL independiente para TikTok/Reels/Shorts.
Un buen clip cumple:
- GANCHO en los primeros 1-2 segundos (algo que frene el scroll).
- Idea AUTOCONCLUSIVA: se entiende sin haber visto el resto del episodio.
- Tiene valor: insight, emoción, sorpresa, humor, o un consejo práctico concreto.
- Contiene una FRASE CITABLE (quotable) clara.
- Empieza y termina en límites naturales de oración (usá los timestamps de los segmentos).
- Duración objetivo entre {min_sec} y {max_sec} segundos.
Evitá: arranques a mitad de idea, relleno, partes que dependen de contexto previo."""

SYSTEM_PROMPT = "Sos editor de contenido de CriptoLú: cortás clips verticales de podcasts."

# Esquema del JSON que debe devolver el LLM. Sin restricciones numéricas (no soportadas por
# structured outputs de Claude); el rango se valida en código.
CLIPS_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "score": {"type": "integer"},
                    "title": {"type": "string"},
                    "reason": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["start", "end", "score", "title", "reason", "quote"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}


class DetectError(RuntimeError):
    """Falla de validación al detectar momentos (se traduce a HTTP 400 en el router)."""


class UpstreamError(DetectError):
    """Falla de la dependencia externa (LLM caído, red, key/SDK faltante) → HTTP 502."""


def _format_segments(transcript: dict) -> str:
    """Segmentos numerados con [start-end] para que el LLM cite timestamps exactos."""
    return "\n".join(
        f"[{s['start']:.2f}-{s['end']:.2f}] {s['text'].strip()}"
        for s in transcript.get("segments", [])
    )


def build_prompt(transcript: dict, *, n_clips: int, min_sec: int, max_sec: int) -> str:
    """Arma el prompt completo (rúbrica + transcripción) que el LLM debe analizar."""
    rubric = DETECTION_RUBRIC.format(min_sec=min_sec, max_sec=max_sec)
    return (
        f"Analizá esta transcripción de podcast y elegí hasta {n_clips} momentos para "
        f"clips verticales.\n\n{rubric}\n\n"
        f"Devolvé un objeto JSON {{\"clips\": [...]}} ordenado por score descendente. "
        f"Cada clip: start (seg), end (seg), score (0-100), title, reason, quote.\n\n"
        f"Transcripción (idioma {transcript.get('language')}):\n"
        f"{_format_segments(transcript)}\n"
    )


# --------------------------------------------------------------------------- #
# Motores
# --------------------------------------------------------------------------- #

def _engine_groq(prompt: str) -> list[dict]:
    """Detecta con Llama 3.3 70B vía Groq (chat completions, JSON mode). Sin deps extra."""
    if not config.GROQ_API_KEY:
        raise UpstreamError("Falta GROQ_API_KEY. Ponela en editorpro/.env (GROQ_API_KEY=gsk_...).")

    payload = json.dumps({
        "model": config.GROQ_LLM_MODEL,
        "temperature": 0.4,
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

    content = data["choices"][0]["message"]["content"]
    return json.loads(content).get("clips", [])


def _engine_claude(prompt: str) -> list[dict]:
    """Detecta con Claude (API de Anthropic) usando structured outputs. SDK perezoso."""
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
            output_config={"format": {"type": "json_schema", "schema": CLIPS_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as e:
        raise UpstreamError(f"Error de la API de Anthropic: {e}") from e
    if response.stop_reason == "refusal":
        raise DetectError("Claude rechazó la petición por seguridad.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text).get("clips", [])


ENGINES = {
    "groq": _engine_groq,      # Llama 3.3 70B vía Groq — motor activo (default)
    "claude": _engine_claude,  # Claude (Anthropic) — listo, requiere ANTHROPIC_API_KEY
}


def _validate_clips(raw: list[dict], duration: float) -> list[dict]:
    """Valida y normaliza los clips del LLM. Lanza DetectError si algún campo es inválido."""
    clean: list[dict] = []
    for i, c in enumerate(raw, 1):
        try:
            start, end, score = float(c["start"]), float(c["end"]), int(c["score"])
        except (KeyError, TypeError, ValueError) as e:
            raise DetectError(f"Clip #{i} con campos inválidos: {e}") from e
        if not (0 <= start < end <= duration + 0.5):
            raise DetectError(
                f"Clip #{i} con timestamps fuera de rango: {start}-{end} (duración {duration:.2f})"
            )
        if not (0 <= score <= 100):
            raise DetectError(f"Clip #{i} con score fuera de 0-100: {score}")
        title = (c.get("title") or "").strip()
        if not title:
            raise DetectError(f"Clip #{i} sin título.")
        clean.append({
            "id": i,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "score": score,
            "title": title,
            "reason": (c.get("reason") or "").strip(),
            "quote": (c.get("quote") or "").strip(),
        })
    clean.sort(key=lambda x: x["score"], reverse=True)
    # reasignar ids tras ordenar para que reflejen el ranking
    for i, c in enumerate(clean, 1):
        c["id"] = i
    return clean


def detect_moments(
    upload_id: str, *, engine: str | None = None, n_clips: int | None = None,
) -> dict:
    """Detecta los mejores momentos de una subida ya transcrita y guarda moments.json.

    Devuelve {upload_id, engine, model, criteria_version, n_clips, clips}.
    Lanza DetectError si no hay transcripción, el motor falla o el LLM devuelve basura.
    """
    transcript = transcription.load_transcript(upload_id)
    if transcript is None:
        raise DetectError(
            f"La subida '{upload_id}' no tiene transcripción. Transcribila primero."
        )
    segments = transcript.get("segments") or []
    if not segments:
        raise DetectError("La transcripción no tiene segmentos para analizar.")

    eng = engine or config.DETECT_DEFAULT_ENGINE
    if eng not in ENGINES:
        raise DetectError(f"Motor desconocido '{eng}'. Disponibles: {', '.join(ENGINES)}")
    n = n_clips or config.DETECT_N_CLIPS

    prompt = build_prompt(
        transcript, n_clips=n, min_sec=config.DETECT_MIN_SEC, max_sec=config.DETECT_MAX_SEC
    )
    raw = ENGINES[eng](prompt)
    duration = max((s["end"] for s in segments), default=0.0)
    clips = _validate_clips(raw, duration)[:n]  # acotar a lo pedido por si el LLM se pasa

    model = config.GROQ_LLM_MODEL if eng == "groq" else config.CLAUDE_MODEL
    payload = {
        "upload_id": upload_id,
        "engine": eng,
        "model": model,
        "criteria_version": CRITERIA_VERSION,
        "n_clips": len(clips),
        "clips": clips,
    }
    out_dir = config.CLIPS_DIR / upload_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "moments.json"
    storage.atomic_write_text(out_file, json.dumps(payload, ensure_ascii=False, indent=2))

    storage.update_metadata(upload_id, {
        "status": "moments",
        "moments": {"file": str(out_file), "n_clips": len(clips), "engine": eng},
    })
    return payload


def load_moments(upload_id: str) -> dict | None:
    """Devuelve moments.json de una subida, o None si no existe (o el id es inválido)."""
    if not storage.valid_upload_id(upload_id):
        return None
    out_file = config.CLIPS_DIR / upload_id / "moments.json"
    if not out_file.is_file():
        return None
    return json.loads(out_file.read_text(encoding="utf-8"))
