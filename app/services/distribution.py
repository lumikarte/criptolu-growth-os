"""Distribución multi-red vía Postiz self-host (W-05, CRI-564).

Toma las piezas de una subida (clips, feed_post, hilo) y crea posts/borradores en Postiz.
**Requisito no negociable del PRD:** NADA sale sin pasar el gate de aprobación (W-06). Por
cada pieza×red, `distribution.py` consulta `approval.is_approved` ANTES de armar el payload;
una pieza no aprobada (pending/rejected/stale) se **salta** y nunca se envía. Falla cerrado.

**Modo borrador primero:** por defecto `type="draft"` (el humano da el último click en
Postiz). El auto-post directo (schedule/now) es fase 2 y está detrás de
`DISTRIBUTION_ALLOW_AUTOPOST` (apagado): aunque se pida otro `type`, se fuerza a draft.

Postiz es un proceso separado; editorpro habla con él solo por HTTP (urllib) → no es obra
derivada (sin contaminación AGPL). El cliente Postiz es lo único que tiene la API key.

NOTA: el carrusel se publica como IMAGEN; ver `carousel_render` (CRI-604). El wiring del
carrusel a Postiz queda para una iteración siguiente (acá se lista en `skipped` con motivo).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from .. import config
from . import approval, clipping, repurpose, storage


class DistributionError(RuntimeError):
    """Falla de validación al distribuir (se traduce a HTTP 400 en el router)."""


class UpstreamError(DistributionError):
    """Falla de Postiz (caído, red, key faltante) → HTTP 502."""


# --------------------------------------------------------------------------- #
# Cliente Postiz (urllib) — lo único que toca la API key
# --------------------------------------------------------------------------- #

def _require_key() -> str:
    if not config.POSTIZ_API_KEY:
        raise UpstreamError(
            "Falta POSTIZ_API_KEY. Ponela en editorpro/.env (y levantá un Postiz self-host)."
        )
    return config.POSTIZ_API_KEY


def _upload_media(path: Path) -> str:
    """Sube un archivo a Postiz y devuelve la referencia (path/url) para usar en el post."""
    key = _require_key()
    data = path.read_bytes()
    req = urllib.request.Request(
        f"{config.POSTIZ_BASE_URL}/upload", data=data,
        headers={"Authorization": key, "Content-Type": "application/octet-stream"},
    )
    try:
        with urllib.request.urlopen(req, timeout=config.POSTIZ_TIMEOUT) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise UpstreamError(f"Postiz upload HTTP {e.code}: {e.read().decode()[:300]}") from e
    except urllib.error.URLError as e:
        raise UpstreamError(f"Red al contactar Postiz (upload): {e}") from e
    return out.get("path") or out.get("url") or ""


def _create_post(payload: dict) -> str:
    """Crea un post/borrador en Postiz y devuelve su id."""
    key = _require_key()
    req = urllib.request.Request(
        f"{config.POSTIZ_BASE_URL}/posts", data=json.dumps(payload).encode(),
        headers={"Authorization": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=config.POSTIZ_TIMEOUT) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise UpstreamError(f"Postiz posts HTTP {e.code}: {e.read().decode()[:300]}") from e
    except urllib.error.URLError as e:
        raise UpstreamError(f"Red al contactar Postiz (posts): {e}") from e
    return out.get("id") or out.get("postId") or ""


# --------------------------------------------------------------------------- #
# Orquestación
# --------------------------------------------------------------------------- #

def _targets(upload_id: str, networks: list[str] | None) -> list[dict]:
    """Deriva la lista de (pieza × red) publicables de una subida, con el contenido y las
    piezas del gate que cada una requiere. NO consulta aprobación acá (eso lo hace distribute).
    """
    pkg = repurpose.load_repurpose(upload_id)
    if pkg is None:
        raise DistributionError(
            f"La subida '{upload_id}' no tiene contenido repurposado. Generalo primero."
        )
    content = pkg.get("content") or {}
    captions = content.get("captions") or {}
    wanted = set(networks) if networks else None

    def net_ok(n: str) -> bool:
        return wanted is None or n in wanted

    targets: list[dict] = []

    # Clips: cada clip a tiktok/reels/shorts, apareado con su caption por red.
    clips = clipping.load_clips(upload_id)
    for c in (clips or {}).get("clips", []) if clips else []:
        for net in config.DISTRIBUTION_TARGETS["clip"]:
            if not net_ok(net):
                continue
            targets.append({
                "piece_key": f"clip:{c['id']}",
                "network": net,
                # el clip solo sale si el clip Y el caption de esa red están aprobados
                "requires": [f"clip:{c['id']}", f"caption:{net}"],
                "content": (captions.get(net) or "").strip(),
                "media": c.get("file"),
                "kind": "video",
            })

    # feed_post: texto + hashtags a IG feed / FB / LinkedIn.
    if content.get("feed_post"):
        fp = content["feed_post"]
        text = fp.get("text", "")
        tags = " ".join(f"#{t}" for t in (fp.get("hashtags") or []))
        body = f"{text}\n\n{tags}".strip()
        for net in config.DISTRIBUTION_TARGETS["feed_post"]:
            if net_ok(net):
                targets.append({"piece_key": "feed_post", "network": net,
                                "requires": ["feed_post"], "content": body,
                                "media": None, "kind": "text"})

    # thread: hilo a x/threads (posts como array).
    if content.get("thread"):
        posts = content["thread"].get("posts") or []
        for net in config.DISTRIBUTION_TARGETS["thread"]:
            if net_ok(net):
                targets.append({"piece_key": "thread", "network": net,
                                "requires": ["thread"], "content": posts,
                                "media": None, "kind": "thread"})

    return targets


def distribute_upload(
    upload_id: str, *, post_type: str | None = None, networks: list[str] | None = None,
) -> dict:
    """Publica en Postiz las piezas aprobadas de una subida (modo borrador por defecto).

    Devuelve {upload_id, provider, type, results, skipped, note}. `results` son las piezas
    enviadas; `skipped` las que no pasaron el gate (con motivo). Lanza DistributionError (400)
    si no hay contenido; UpstreamError (502) si Postiz falla.
    """
    # Modo borrador salvo que el auto-post esté explícitamente habilitado (fase 2).
    ptype = post_type or config.DISTRIBUTION_DEFAULT_TYPE
    if ptype != "draft" and not config.DISTRIBUTION_ALLOW_AUTOPOST:
        ptype = "draft"

    targets = _targets(upload_id, networks)
    results: list[dict] = []
    skipped: list[dict] = []

    for t in targets:
        # GATE: la pieza (y para clips, su caption) deben estar aprobadas sobre su contenido
        # vigente. is_approved falla cerrado ante pending/rejected/stale.
        not_approved = [k for k in t["requires"] if not approval.is_approved(upload_id, k)]
        if not_approved:
            skipped.append({"piece_key": t["piece_key"], "network": t["network"],
                            "reason": f"no aprobada: {', '.join(not_approved)}"})
            continue
        if not t["content"]:
            skipped.append({"piece_key": t["piece_key"], "network": t["network"],
                            "reason": "sin caption/contenido para esta red"})
            continue

        # Recién con el gate pasado tocamos Postiz (subir media si es video).
        image = []
        if t["kind"] == "video" and t["media"]:
            image = [{"path": _upload_media(Path(t["media"]))}]
        value = ([{"content": p} for p in t["content"]] if t["kind"] == "thread"
                 else [{"content": t["content"], "image": image}])
        payload = {"type": ptype, "posts": [{"integration": {"id": t["network"]},
                                             "value": value}]}
        post_id = _create_post(payload)
        results.append({"piece_key": t["piece_key"], "network": t["network"],
                        "postiz_post_id": post_id, "status": ptype})

    note = None
    if repurpose.load_repurpose(upload_id) and \
            (repurpose.load_repurpose(upload_id).get("content") or {}).get("carousel"):
        note = ("El carrusel se distribuye como imagen (usar carousel_render, CRI-604); "
                "su wiring a Postiz queda para una iteración siguiente.")

    payload = {"upload_id": upload_id, "provider": "postiz", "type": ptype,
               "results": results, "skipped": skipped, "note": note}
    out_dir = config.CLIPS_DIR / upload_id
    out_dir.mkdir(parents=True, exist_ok=True)
    storage.atomic_write_text(
        out_dir / "distribution.json", json.dumps(payload, ensure_ascii=False, indent=2)
    )
    storage.update_metadata(upload_id, {
        "distribution": {"type": ptype, "n_results": len(results), "n_skipped": len(skipped)},
    })
    return payload


def load_distribution(upload_id: str) -> dict | None:
    """Devuelve distribution.json de una subida, o None si no existe (o el id es inválido)."""
    if not storage.valid_upload_id(upload_id):
        return None
    f = config.CLIPS_DIR / upload_id / "distribution.json"
    if not f.is_file():
        return None
    return json.loads(f.read_text(encoding="utf-8"))
