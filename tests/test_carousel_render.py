"""Tests del render del carrusel a PNG (CRI-604): servicio, endpoints e integración con el gate."""

from __future__ import annotations

import io
import json
import sys

import pytest

pytest.importorskip("PIL")  # si Pillow no está en el entorno, se saltan (CI seguro)

from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.main import app  # noqa: E402
from app.services import approval, carousel_render, storage  # noqa: E402

from .conftest import AUTH_HEADERS, VALID_ID, make_upload  # noqa: E402
from .test_repurpose import _valid_package  # noqa: E402


def _make_repurpose(upload_id: str = VALID_ID, carousel: dict | None = None) -> None:
    pkg = _valid_package()
    if carousel is not None:
        pkg["carousel"] = carousel
    out_dir = config.CLIPS_DIR / upload_id
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"upload_id": upload_id, "content": pkg}
    storage.atomic_write_text(out_dir / "repurpose.json", json.dumps(payload))


@pytest.fixture
def client():
    with TestClient(app, headers=AUTH_HEADERS) as c:
        yield c


# --------------------------------------------------------------------------- #
# Servicio
# --------------------------------------------------------------------------- #

def test_render_happy_path(iso):
    make_upload()
    _make_repurpose()  # carrusel de 6 slides
    out = carousel_render.render_carousel(VALID_ID)

    assert out["n_slides"] == 7  # portada + 6
    assert out["width"] == 1080 and out["height"] == 1350
    car_dir = config.CLIPS_DIR / VALID_ID / "carousel"
    pngs = sorted(car_dir.glob("slide_*.png"))
    assert len(pngs) == 7
    for p in pngs:
        data = p.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"          # magic bytes PNG
        assert Image.open(io.BytesIO(data)).size == (1080, 1350)
    # índice persistido
    saved = json.loads((car_dir / "carousel.json").read_text())
    assert saved["title"] == out["title"]
    assert [s["index"] for s in saved["slides"]] == list(range(7))
    # metadata actualizada
    meta = json.loads((config.UPLOADS_DIR / VALID_ID / "meta.json").read_text())
    assert meta["carousel_render"]["n_slides"] == 7


def test_render_without_repurpose_raises(iso):
    make_upload()
    with pytest.raises(carousel_render.CarouselError):
        carousel_render.render_carousel(VALID_ID)


def test_render_empty_carousel_raises(iso):
    make_upload()
    _make_repurpose(carousel={"title": "", "slides": []})
    with pytest.raises(carousel_render.CarouselError):
        carousel_render.render_carousel(VALID_ID)


def test_rerender_with_fewer_slides_cleans_old(iso):
    make_upload()
    _make_repurpose()  # 6 slides → 7 PNGs
    carousel_render.render_carousel(VALID_ID)
    # re-render con menos slides
    _make_repurpose(carousel={
        "title": "Menos", "slides": [{"heading": "H1", "body": "B1"},
                                      {"heading": "H2", "body": "B2"}],
    })
    out = carousel_render.render_carousel(VALID_ID)
    assert out["n_slides"] == 3  # portada + 2
    pngs = list((config.CLIPS_DIR / VALID_ID / "carousel").glob("slide_*.png"))
    assert len(pngs) == 3  # no quedaron los viejos


def test_long_text_does_not_crash(iso):
    make_upload()
    _make_repurpose(carousel={
        "title": "Título " * 40,
        "slides": [{"heading": "Encabezado larguísimo " * 10,
                    "body": "Cuerpo interminable. " * 60}],
    })
    out = carousel_render.render_carousel(VALID_ID)
    assert out["n_slides"] == 2  # no reventó por overflow


def test_missing_pillow_maps_upstream(iso, monkeypatch):
    make_upload()
    _make_repurpose()
    # forzar que `import PIL` falle dentro del servicio
    monkeypatch.setitem(sys.modules, "PIL", None)
    with pytest.raises(carousel_render.UpstreamError):
        carousel_render.render_carousel(VALID_ID)


# --------------------------------------------------------------------------- #
# Integración con el gate: la aprobación del carrusel caduca si cambia la plantilla
# --------------------------------------------------------------------------- #

def test_carousel_approval_goes_stale_on_template_bump(iso, monkeypatch):
    make_upload()
    _make_repurpose()
    approval.decide(VALID_ID, "carousel", approval.APPROVED)
    assert approval.is_approved(VALID_ID, "carousel") is True
    # retocar la plantilla de marca → la aprobación previa ya no vale
    monkeypatch.setattr(config, "CAROUSEL_TEMPLATE_VERSION", "2.0")
    assert approval.is_approved(VALID_ID, "carousel") is False
    st = {p["key"]: p["status"] for p in approval.get_state(VALID_ID)["pieces"]}
    assert st["carousel"] == "stale"


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

def test_post_render_unknown_upload_404(iso, client):
    r = client.post(f"/uploads/{VALID_ID}/carousel/render")
    assert r.status_code == 404


def test_post_render_happy_and_get(iso, client):
    make_upload()
    _make_repurpose()
    r = client.post(f"/uploads/{VALID_ID}/carousel/render")
    assert r.status_code == 200
    assert r.json()["n_slides"] == 7
    g = client.get(f"/uploads/{VALID_ID}/carousel")
    assert g.status_code == 200
    assert g.json()["width"] == 1080


def test_get_carousel_before_render_404(iso, client):
    make_upload()
    r = client.get(f"/uploads/{VALID_ID}/carousel")
    assert r.status_code == 404


def test_render_without_repurpose_maps_400(iso, client):
    make_upload()
    r = client.post(f"/uploads/{VALID_ID}/carousel/render")
    assert r.status_code == 400


def test_serve_slide_png(iso, client):
    make_upload()
    _make_repurpose()
    client.post(f"/uploads/{VALID_ID}/carousel/render")
    r = client.get(f"/uploads/{VALID_ID}/carousel/slide_00.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_serve_missing_slide_404(iso, client):
    make_upload()
    _make_repurpose()
    client.post(f"/uploads/{VALID_ID}/carousel/render")
    r = client.get(f"/uploads/{VALID_ID}/carousel/slide_99.png")
    assert r.status_code == 404
