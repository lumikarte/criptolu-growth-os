"""Tests del orquestador /process (PP-MVP-05).

Los 4 servicios se mockean: se valida el orden, el passthrough de params, el resume/force,
el stop-on-failure y el mapeo de error por etapa (502 upstream vs 400 validación).
"""

from __future__ import annotations

import pytest

from app.services import clipping, detection, export, pipeline, transcription


@pytest.fixture
def all_stages(monkeypatch):
    """Mockea los 4 servicios: nada existe (no skip) y cada etapa registra su llamada."""
    order: list[str] = []
    captured: dict[str, dict] = {}

    # exists() → None: ninguna etapa tiene artefacto, así que todas corren.
    monkeypatch.setattr(transcription, "load_transcript", lambda uid: None)
    monkeypatch.setattr(detection, "load_moments", lambda uid: None)
    monkeypatch.setattr(clipping, "load_clips", lambda uid: None)
    monkeypatch.setattr(export, "load_export", lambda uid: None)

    def mk(name):
        def fn(uid, **kw):
            order.append(name)
            captured[name] = kw
            return {name: "ok"}
        return fn

    monkeypatch.setattr(transcription, "transcribe_upload", mk("transcribe"))
    monkeypatch.setattr(detection, "detect_moments", mk("detect"))
    monkeypatch.setattr(clipping, "cut_clips", mk("clips"))
    monkeypatch.setattr(export, "export_clips", mk("export"))
    return order, captured


def test_process_runs_all_stages_in_order(all_stages):
    order, _ = all_stages
    out = pipeline.process_upload("uid")
    assert order == ["transcribe", "detect", "clips", "export"]
    assert out["completed"] == ["transcribe", "detect", "clips", "export"]
    assert out["failed"] is None
    assert set(out["stages"]) == {"transcribe", "detect", "clips", "export"}


def test_process_passes_through_params(all_stages):
    _, captured = all_stages
    pipeline.process_upload(
        "uid", language="es", engine="claude", n_clips=3,
        subtitles=False, platforms=["tiktok"],
    )
    assert captured["transcribe"] == {"language": "es"}
    assert captured["detect"] == {"engine": "claude", "n_clips": 3}
    assert captured["clips"] == {"subtitles": False}
    assert captured["export"] == {"platforms": ["tiktok"]}


def test_process_resume_skips_existing_stages(monkeypatch, all_stages):
    order, _ = all_stages
    # transcript ya existe → la etapa transcribe se saltea (no se llama al servicio).
    monkeypatch.setattr(transcription, "load_transcript", lambda uid: {"done": True})
    out = pipeline.process_upload("uid")
    assert "transcribe" not in order               # no se ejecutó
    assert out["stages"]["transcribe"] == {"skipped": True}
    assert out["completed"][0] == "transcribe"      # pero cuenta como completada


def test_process_force_reruns_existing_stages(monkeypatch, all_stages):
    order, _ = all_stages
    monkeypatch.setattr(transcription, "load_transcript", lambda uid: {"done": True})
    pipeline.process_upload("uid", force=True)
    assert "transcribe" in order  # force ignora el artefacto existente


def test_process_stops_on_failure_and_maps_upstream(monkeypatch, all_stages):
    order, _ = all_stages

    def boom(uid, **kw):
        raise detection.UpstreamError("Groq caído")

    monkeypatch.setattr(detection, "detect_moments", boom)
    with pytest.raises(pipeline.PipelineError) as ei:
        pipeline.process_upload("uid")
    err = ei.value
    assert err.stage == "detect"
    assert err.upstream is True               # UpstreamError → 502
    assert err.completed == ["transcribe"]    # lo previo sí corrió
    assert "clips" not in order and "export" not in order  # no siguió


def test_process_validation_error_is_not_upstream(monkeypatch, all_stages):
    def boom(uid, **kw):
        raise clipping.ClipError("No hay momentos para cortar.")

    monkeypatch.setattr(clipping, "cut_clips", boom)
    with pytest.raises(pipeline.PipelineError) as ei:
        pipeline.process_upload("uid")
    assert ei.value.stage == "clips"
    assert ei.value.upstream is False         # ClipError → 400
    assert ei.value.completed == ["transcribe", "detect"]
