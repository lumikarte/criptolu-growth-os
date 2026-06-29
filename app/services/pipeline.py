"""Orquestación del pipeline de punta a punta (PP-MVP-05).

Encadena las 4 etapas existentes en una sola llamada, reutilizando los servicios tal cual
(sin duplicar lógica): transcribe → detect → clips → export.

Por defecto es **resume/idempotente**: salta una etapa si su artefacto ya existe
(transcript.json / moments.json / clips.json / export.json). Con ``force=True`` rehace todo
desde cero — útil porque re-transcribir es desperdicio y re-detectar (LLM no determinístico)
cambiaría la selección de clips, dejando inconsistentes los artefactos posteriores.

Si una etapa falla, se corta la cadena y se levanta ``PipelineError`` con la etapa, las
etapas completadas y si la falla es upstream (502) o de validación (400). El router lo
traduce al status HTTP real (no 200-con-error).
"""

from __future__ import annotations

from . import clipping, detection, export, storage, transcription

# Las tres UpstreamError son clases INDEPENDIENTES (no comparten base) → tupla para isinstance.
_UPSTREAM = (
    transcription.UpstreamError,
    detection.UpstreamError,
    clipping.UpstreamError,
)
# Errores "esperables" de cada servicio. Las UpstreamError son subclases de estos, así que
# capturar las bases alcanza; cualquier otra excepción (bug real) se propaga como 500.
_STAGE_ERRORS = (
    transcription.TranscriptionError,
    detection.DetectError,
    clipping.ClipError,
    export.ExportError,
)


class PipelineError(RuntimeError):
    """Falla de una etapa del pipeline. ``upstream`` decide 502 (True) vs 400 (False)."""

    def __init__(self, stage: str, message: str, completed: list[str], *, upstream: bool):
        super().__init__(message)
        self.stage = stage
        self.completed = completed
        self.upstream = upstream


def process_upload(
    upload_id: str,
    *,
    language: str | None = None,
    engine: str | None = None,
    n_clips: int | None = None,
    subtitles: bool | None = None,
    platforms: list[str] | None = None,
    force: bool = False,
) -> dict:
    """Corre transcribe → detect → clips → export. Devuelve el resumen por etapa.

    Resume por defecto (salta etapas con artefacto existente); ``force`` rehace todo.
    Lanza PipelineError en la primera etapa que falle (no continúa).
    """
    stages: dict[str, dict] = {}
    completed: list[str] = []

    def run(name: str, exists, fn) -> None:
        if not force and exists() is not None:
            stages[name] = {"skipped": True}
        else:
            try:
                stages[name] = fn()
            except _STAGE_ERRORS as e:
                raise PipelineError(
                    name, str(e), list(completed), upstream=isinstance(e, _UPSTREAM)
                ) from e
        completed.append(name)

    run(
        "transcribe",
        lambda: transcription.load_transcript(upload_id),
        lambda: transcription.transcribe_upload(upload_id, language=language),
    )
    run(
        "detect",
        lambda: detection.load_moments(upload_id),
        lambda: detection.detect_moments(upload_id, engine=engine, n_clips=n_clips),
    )
    run(
        "clips",
        lambda: clipping.load_clips(upload_id),
        lambda: clipping.cut_clips(upload_id, subtitles=subtitles),
    )
    run(
        "export",
        lambda: export.load_export(upload_id),
        lambda: export.export_clips(upload_id, platforms=platforms),
    )

    return {
        "upload_id": upload_id,
        "stages": stages,
        "completed": completed,
        "failed": None,
    }
