"""Tests del auto-crop face-tracked (CRI-128). El detector YuNet (OpenCV) se mockea vía
`framing._sample_faces`: se valida la geometría del crop, el suavizado, el fallback y el
guion sendcmd — sin ninguna dependencia binaria."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import config
from app.services import framing


@pytest.fixture
def autocrop_on(monkeypatch):
    monkeypatch.setattr(config, "AUTOCROP_ENABLED", True)


def _fake_samples(centers, sw=1920, sh=1080):
    """Devuelve un _sample_faces que emite centros (fracción de ancho) dados, en 1080p landscape."""
    def _f(src, start, dur):
        return [(round(i * 0.33, 3), c) for i, c in enumerate(centers)], sw, sh
    return _f


def test_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(config, "AUTOCROP_ENABLED", False)
    assert framing.plan_crop(Path("x.mp4"), 0.0, 10.0) is None


def test_no_faces_returns_none(autocrop_on, monkeypatch):
    monkeypatch.setattr(framing, "_sample_faces", lambda s, a, b: ([], 1920, 1080))
    assert framing.plan_crop(Path("x.mp4"), 0.0, 10.0) is None


def test_opencv_missing_returns_none(autocrop_on, monkeypatch):
    def _boom(s, a, b):
        raise ImportError("no cv2")
    monkeypatch.setattr(framing, "_sample_faces", _boom)
    assert framing.plan_crop(Path("x.mp4"), 0.0, 10.0) is None


def test_vertical_source_returns_none(autocrop_on, monkeypatch):
    # source ya 9:16 (720x1280) → lienzo escalado 1080 de ancho, max_x=0, nada que panear.
    monkeypatch.setattr(framing, "_sample_faces", _fake_samples([0.5, 0.5], sw=720, sh=1280))
    assert framing.plan_crop(Path("x.mp4"), 0.0, 10.0) is None


def test_static_face_right_pushes_x_to_max(autocrop_on, monkeypatch):
    monkeypatch.setattr(config, "AUTOCROP_MODE", "static")
    monkeypatch.setattr(framing, "_sample_faces", _fake_samples([0.9, 0.9, 0.9]))
    plan = framing.plan_crop(Path("x.mp4"), 0.0, 10.0)
    assert plan is not None and plan.mode == "static"
    max_x = plan.scaled_w - config.CLIP_WIDTH
    assert plan.x == max_x  # cara a la derecha → crop pegado al borde derecho


def test_static_face_left_pushes_x_to_zero(autocrop_on, monkeypatch):
    monkeypatch.setattr(config, "AUTOCROP_MODE", "static")
    monkeypatch.setattr(framing, "_sample_faces", _fake_samples([0.05, 0.05]))
    plan = framing.plan_crop(Path("x.mp4"), 0.0, 10.0)
    assert plan is not None and plan.x == 0


def test_static_x_within_bounds(autocrop_on, monkeypatch):
    monkeypatch.setattr(config, "AUTOCROP_MODE", "static")
    monkeypatch.setattr(framing, "_sample_faces", _fake_samples([0.5]))
    plan = framing.plan_crop(Path("x.mp4"), 0.0, 10.0)
    assert 0 <= plan.x <= plan.scaled_w - config.CLIP_WIDTH


def test_smooth_builds_keyframes_and_sendcmd(autocrop_on, monkeypatch):
    monkeypatch.setattr(config, "AUTOCROP_MODE", "smooth")
    # cara que salta de izquierda a derecha: el paneo debe moverse gradualmente
    monkeypatch.setattr(framing, "_sample_faces", _fake_samples([0.1, 0.1, 0.9, 0.9, 0.9]))
    plan = framing.plan_crop(Path("x.mp4"), 0.0, 10.0)
    assert plan is not None and plan.mode == "smooth"
    assert plan.keyframes and plan.x_init is not None
    xs = [x for _, x in plan.keyframes]
    assert all(0 <= x <= plan.scaled_w - config.CLIP_WIDTH for x in xs)
    # EMA: no salta de una a la x final en un solo paso
    assert xs[0] < xs[-1]
    script = framing.build_sendcmd(plan)
    assert "crop@cam x" in script
    assert script.strip().endswith(";")
    # una línea por keyframe, valores enteros (sin texto de usuario)
    assert len(script.strip().splitlines()) == len(plan.keyframes)
