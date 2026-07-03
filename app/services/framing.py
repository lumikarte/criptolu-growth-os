"""Auto-crop 9:16 face-tracked (CRI-128).

El corte de clips (``clipping._video_cmd``) escala el source a un lienzo que cubre 1080×1920
y toma la franja **central**. Cuando la cara del hablante no está centrada, se pierde en el
recorte. Este módulo calcula una x de crop **sobre la cara detectada** en vez de la central.

Detección: **YuNet** (``cv2.FaceDetectorYN``), modelo ONNX ~345 KB versionado en el repo —
sin PyTorch, sin GPU, CPU. La dependencia (``opencv-python-headless``) es **opcional** y se
importa de forma perezosa (patrón ``anthropic``/PIL del repo).

Regla de oro — **fallback**: ante cualquier problema (flag apagado, sin OpenCV, sin modelo,
sin cara, error de lectura) ``plan_crop`` devuelve ``None`` y el llamador cae al crop central
de siempre (``face_tracked=false``). El auto-crop **nunca** rompe el corte.

Seguridad del filtergraph: el modo ``smooth`` genera comandos ``sendcmd`` con valores
**numéricos** que calculamos y clampeamos acá (nunca texto del usuario). El archivo de
comandos lo escribe y limpia ``clipping`` (dueño de los temporales), con la ruta escapada y
los args de FFmpeg en lista (sin shell) — mismo patrón seguro que los subtítulos.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import config


@dataclass
class CropPlan:
    """Plan de recorte horizontal sobre el lienzo escalado (ancho ``scaled_w``, alto 1920).

    - ``mode="static"``: una sola ``x`` para todo el clip (cero jitter).
    - ``mode="smooth"``: paneo suavizado como lista de ``keyframes`` ``(t_rel, x)`` que
      ``clipping`` vuelca a un archivo ``sendcmd``. ``x_init`` es la x del primer frame.
    Todas las ``x`` están clampeadas a ``[0, scaled_w - 1080]`` (dentro del lienzo).
    """

    mode: str
    scaled_w: int
    x: int | None = None
    keyframes: list[tuple[float, int]] | None = None
    x_init: int | None = None
    n_samples: int = 0


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _scaled_width(sw: int, sh: int) -> int:
    """Ancho del lienzo tras ``scale=1080:1920:force_original_aspect_ratio=increase``.

    El factor de escala cubre ambos ejes (max). FFmpeg NO fuerza par acá (sin
    ``force_divisible_by``): usa ``av_rescale`` = round-half-up, así que replicamos con
    ``round`` crudo para que la x que calculamos caiga dentro del frame real (verificado
    contra FFmpeg: 1920×1080 → ancho 3413, no 3414).
    """
    if sw <= 0 or sh <= 0:
        return config.CLIP_WIDTH
    factor = max(config.CLIP_WIDTH / sw, config.CLIP_HEIGHT / sh)
    return int(round(sw * factor))


def _sample_faces(src: Path, start: float, dur: float) -> tuple[list[tuple[float, float]], int, int]:
    """Muestrea el clip y devuelve ``([(t_rel, center_frac)], sw, sh)``.

    ``center_frac`` es el centro horizontal de la cara de mayor confianza como fracción del
    ancho (0..1). Aislado en su propia función para poder mockearlo en tests sin OpenCV.
    Lanza ImportError si no está OpenCV y RuntimeError si el modelo/lectura falla.
    """
    import cv2  # perezoso: opcional, primera dep binaria del repo

    model = config.AUTOCROP_MODEL
    if not Path(model).is_file():
        raise RuntimeError(f"Falta el modelo YuNet: {model}")

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV no pudo abrir {src}")
    try:
        sw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 0
        sh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 0
        detector = cv2.FaceDetectorYN.create(
            model, "", (sw or 320, sh or 320), config.AUTOCROP_MIN_SCORE
        )
        if sw > 0 and sh > 0:
            detector.setInputSize((sw, sh))

        step = 1.0 / max(config.AUTOCROP_SAMPLE_FPS, 0.1)
        n = min(int(dur / step) + 1, 120) if dur > 0 else 1  # cota dura de muestras
        samples: list[tuple[float, float]] = []
        for i in range(n):
            t_rel = i * step
            cap.set(cv2.CAP_PROP_POS_MSEC, (start + t_rel) * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            fw = frame.shape[1] or sw
            _, faces = detector.detect(frame)
            if faces is None or len(faces) == 0:
                continue
            # cara de mayor score (col 14); centro x = x + w/2
            best = max(faces, key=lambda f: f[14])
            cx = float(best[0]) + float(best[2]) / 2.0
            if fw:
                samples.append((round(t_rel, 3), _clamp(cx / fw, 0.0, 1.0)))
        return samples, sw, sh
    finally:
        cap.release()


def plan_crop(src: Path, start: float, dur: float) -> CropPlan | None:
    """Calcula el plan de crop face-tracked para ``[start, start+dur]`` o ``None`` (fallback).

    Devuelve ``None`` si el flag está apagado, no hay OpenCV/modelo, no se detecta ninguna
    cara o cualquier error de lectura — el llamador usa entonces el crop central de siempre.
    """
    if not config.AUTOCROP_ENABLED:
        return None
    try:
        samples, sw, sh = _sample_faces(src, start, dur)
    except Exception:
        # ImportError (sin OpenCV), modelo ausente, lectura rota → fallback silencioso.
        return None
    if not samples:
        return None

    scaled_w = _scaled_width(sw, sh)
    max_x = max(0, scaled_w - config.CLIP_WIDTH)
    if max_x == 0:
        # El lienzo ya es 1080 de ancho (source vertical): no hay nada que paneary.
        return None
    win_frac = config.CLIP_WIDTH / scaled_w

    def x_of(center_frac: float) -> int:
        x_frac = _clamp(center_frac - win_frac / 2.0, 0.0, 1.0 - win_frac)
        return int(round(_clamp(x_frac * scaled_w, 0, max_x)))

    xs = [(t, x_of(cf)) for t, cf in samples]

    if config.AUTOCROP_MODE == "static" or len(xs) == 1:
        ordered = sorted(v for _, v in xs)
        median = ordered[len(ordered) // 2]
        return CropPlan(mode="static", scaled_w=scaled_w, x=median, n_samples=len(xs))

    # Paneo suavizado: EMA + deadzone (no mover ante micro-variaciones → sin jitter).
    alpha = config.AUTOCROP_SMOOTH_ALPHA
    dead = config.AUTOCROP_DEADZONE * scaled_w
    keyframes: list[tuple[float, int]] = []
    cur = float(xs[0][1])
    for t, target in xs:
        if abs(target - cur) > dead:
            cur = cur + alpha * (target - cur)
        xi = int(round(_clamp(cur, 0, max_x)))
        if not keyframes or keyframes[-1][1] != xi:
            keyframes.append((t, xi))
    x_init = keyframes[0][1] if keyframes else int(xs[0][1])
    return CropPlan(mode="smooth", scaled_w=scaled_w, keyframes=keyframes,
                    x_init=x_init, n_samples=len(xs))


def build_sendcmd(plan: CropPlan) -> str:
    """Serializa el paneo a un guion ``sendcmd`` (una línea por keyframe).

    ``sendcmd`` NO interpola entre comandos → emitimos denso (ya viene pre-suavizado). Los
    valores son enteros calculados/clampeados acá; nunca hay texto del usuario en el guion.
    """
    lines = [f"{t:.3f} crop@cam x {x};" for t, x in (plan.keyframes or [])]
    return "\n".join(lines) + "\n"
