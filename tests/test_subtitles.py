"""Tests de los subtítulos quemados en clips (PP-MVP-04).

FFmpeg se mockea: se valida el re-chunking de cues (tiempos 0-based, líneas cortas), que
el comando incluya el filtro subtitles= con la ruta del .srt, la política de transcript
ausente (skip si default / 400 si explícito), el escapado de ruta, y la limpieza del temp.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import config
from app.services import clipping

from .conftest import VALID_ID, make_clips, make_moments, make_transcript, make_upload


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    calls: list[list[str]] = []

    def _fake(cmd, *, timeout=None):
        calls.append(cmd)
        if cmd[0] == "ffmpeg":
            Path(cmd[-1]).write_bytes(b"fake-clip")
        return ""

    monkeypatch.setattr(clipping, "_run", _fake)
    return calls


def _probe(monkeypatch, *, video=True, audio=True, duration=100.0):
    monkeypatch.setattr(
        clipping, "probe_streams",
        lambda src: {"has_video": video, "has_audio": audio, "duration": duration},
    )


# --- _caption_cues: re-chunking desde words --------------------------------------------

def test_caption_cues_are_clip_relative_and_clamped():
    transcript = {"segments": [{
        "start": 10.0, "end": 14.0, "text": "uno dos tres",
        "words": [
            {"start": 10.0, "end": 10.5, "word": "uno"},
            {"start": 10.5, "end": 11.0, "word": " dos"},
            {"start": 11.0, "end": 11.5, "word": " tres"},
        ],
    }]}
    cues = clipping._caption_cues(transcript, 10.0, 20.0)  # clip arranca en 10
    assert cues
    assert cues[0]["start"] == 0.0          # 10.0 - 10.0
    assert cues[-1]["end"] <= 10.0          # dentro de dur=10
    assert "uno" in cues[0]["text"]


def test_caption_cues_chunks_long_run_into_short_lines():
    words = [{"start": float(i) * 0.4, "end": float(i) * 0.4 + 0.4, "word": f" w{i}"}
             for i in range(20)]
    transcript = {"segments": [{"start": 0.0, "end": 8.0, "text": "x", "words": words}]}
    cues = clipping._caption_cues(transcript, 0.0, 8.0)
    assert len(cues) >= 2  # 20 palabras no caben en una sola línea
    for c in cues:
        assert len(c["text"].split()) <= config.SUB_MAX_WORDS


def test_caption_cues_special_chars_kept_in_text():
    transcript = {"segments": [{
        "start": 0.0, "end": 2.0, "text": "raro",
        "words": [{"start": 0.0, "end": 1.0, "word": "a:b'c,d"}],
    }]}
    cues = clipping._caption_cues(transcript, 0.0, 5.0)
    assert cues[0]["text"] == "a:b'c,d"  # el texto se preserva (va al archivo SRT)


def test_caption_cues_segment_fallback_without_words():
    transcript = {"segments": [{"start": 1.0, "end": 3.0, "text": "sin words", "words": []}]}
    cues = clipping._caption_cues(transcript, 0.0, 5.0)
    assert cues == [{"start": 1.0, "end": 3.0, "text": "sin words"}]


# --- _subtitles_filter: escapado de ruta -----------------------------------------------

def test_subtitles_filter_escapes_path():
    f = clipping._subtitles_filter(Path("/tmp/we:ird/su'b.srt"))
    assert "\\:" in f and "\\'" in f
    assert "force_style=" in f and "subtitles=" in f


# --- cut_clips con subtítulos ----------------------------------------------------------

def test_cut_clips_burns_subtitles_when_transcript_exists(iso, monkeypatch, fake_ffmpeg):
    make_upload()
    make_moments()  # clips [5,25] y [40,70]
    # Transcript con texto en AMBAS ventanas para que los dos clips lleven captions.
    make_transcript(segments=[
        {"start": 6.0, "end": 9.0, "text": "hola mundo",
         "words": [{"start": 6.0, "end": 6.5, "word": "hola"},
                   {"start": 6.5, "end": 7.0, "word": " mundo"}]},
        {"start": 42.0, "end": 45.0, "text": "segundo clip",
         "words": [{"start": 42.0, "end": 42.5, "word": "segundo"},
                   {"start": 42.5, "end": 43.0, "word": " clip"}]},
    ])
    _probe(monkeypatch, video=True, audio=True)

    out = clipping.cut_clips(VALID_ID)  # default: quema si hay transcript

    assert out["subtitles"] is True
    assert all(c["subtitles"] for c in out["clips"])
    assert any("subtitles=" in " ".join(c) for c in fake_ffmpeg)
    # El temp .srt se limpió.
    assert not list(config.TMP_DIR.glob("sub_*.srt"))


def test_cut_clips_subtitles_per_clip_depends_on_window(iso, monkeypatch, fake_ffmpeg):
    # Transcript solo en la ventana del clip 1 → clip 1 con subs, clip 2 sin subs.
    make_upload()
    make_moments()
    make_transcript()  # words ~6-8.4s, caen solo en el clip [5,25]
    _probe(monkeypatch, video=True, audio=True)

    out = clipping.cut_clips(VALID_ID)
    by_id = {c["id"]: c for c in out["clips"]}
    assert by_id[1]["subtitles"] is True
    assert by_id[2]["subtitles"] is False


def test_cut_clips_skips_subtitles_when_no_transcript_default(iso, monkeypatch, fake_ffmpeg):
    make_upload()
    make_moments()  # sin transcript
    _probe(monkeypatch, video=True, audio=True)

    out = clipping.cut_clips(VALID_ID)  # default → omitir captions, no romper

    assert out["subtitles"] is False
    assert not any("subtitles=" in " ".join(c) for c in fake_ffmpeg)


def test_cut_clips_explicit_subtitles_without_transcript_raises(iso, monkeypatch, fake_ffmpeg):
    make_upload()
    make_moments()
    _probe(monkeypatch, video=True, audio=True)
    with pytest.raises(clipping.ClipError, match="no tiene transcripción"):
        clipping.cut_clips(VALID_ID, subtitles=True)


def test_cut_clips_subtitles_false_disables(iso, monkeypatch, fake_ffmpeg):
    make_upload()
    make_moments()
    make_transcript()
    _probe(monkeypatch, video=True, audio=True)

    out = clipping.cut_clips(VALID_ID, subtitles=False)
    assert out["subtitles"] is False
    assert not any("subtitles=" in " ".join(c) for c in fake_ffmpeg)


def test_cut_clips_subtitles_in_waveform_mode(iso, monkeypatch, fake_ffmpeg):
    make_upload(ext=".mp3")
    make_moments()
    make_transcript()
    _probe(monkeypatch, video=False, audio=True)

    out = clipping.cut_clips(VALID_ID)
    assert out["subtitles"] is True
    # El filtro de subtítulos también aparece en el filter_complex del waveform.
    assert any("subtitles=" in " ".join(c) for c in fake_ffmpeg)
