"""Tests de la diarización (W-02, CRI-561). El motor externo (AssemblyAI/pyannote) se
mockea; el foco es _assign_speakers (mapeo por solapamiento) y el wiring del endpoint."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import diarization

from .conftest import VALID_ID, make_transcript, make_upload


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _two_speaker_segments() -> list[dict]:
    # SPEAKER_A habla 0-5, SPEAKER_B habla 5-10.
    return [
        {"id": 0, "start": 0.0, "end": 5.0, "text": "hola soy A",
         "words": [{"start": 0.5, "end": 1.0, "word": "hola"},
                   {"start": 2.0, "end": 2.5, "word": "soy"},
                   {"start": 4.0, "end": 4.5, "word": "A"}]},
        {"id": 1, "start": 5.0, "end": 10.0, "text": "y yo B",
         "words": [{"start": 6.0, "end": 6.5, "word": "y"},
                   {"start": 7.0, "end": 7.5, "word": "yo"},
                   {"start": 9.0, "end": 9.5, "word": "B"}]},
    ]


_TURNS = [
    {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_A"},
    {"start": 5.0, "end": 10.0, "speaker": "SPEAKER_B"},
]


# --------------------------------------------------------------------------- #
# _assign_speakers / _speaker_at (lógica pura)
# --------------------------------------------------------------------------- #

def test_assign_speakers_by_overlap():
    segs = _two_speaker_segments()
    speakers = diarization._assign_speakers(segs, _TURNS)
    assert set(speakers) == {"SPEAKER_A", "SPEAKER_B"}
    assert segs[0]["speaker"] == "SPEAKER_A"
    assert segs[1]["speaker"] == "SPEAKER_B"
    assert all(w["speaker"] == "SPEAKER_A" for w in segs[0]["words"])
    assert all(w["speaker"] == "SPEAKER_B" for w in segs[1]["words"])


def test_segment_speaker_is_majority_vote():
    # 2 words de A, 1 de B → el segmento es de A
    seg = {"id": 0, "start": 0.0, "end": 10.0, "text": "x",
           "words": [{"start": 1.0, "end": 1.5, "word": "a"},
                     {"start": 2.0, "end": 2.5, "word": "a"},
                     {"start": 8.0, "end": 8.5, "word": "b"}]}
    diarization._assign_speakers([seg], _TURNS)
    assert seg["speaker"] == "SPEAKER_A"


def test_word_in_gap_gets_nearest_turn():
    turns = [{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_A"},
             {"start": 8.0, "end": 10.0, "speaker": "SPEAKER_B"}]
    # word a t≈3 (más cerca de A que termina en 2) vs word a t≈7.5 (más cerca de B)
    assert diarization._speaker_at(turns, 3.0) == "SPEAKER_A"
    assert diarization._speaker_at(turns, 7.5) == "SPEAKER_B"


def test_segment_without_words_uses_midpoint():
    seg = {"id": 0, "start": 6.0, "end": 8.0, "text": "x", "words": []}
    diarization._assign_speakers([seg], _TURNS)  # midpoint 7.0 → SPEAKER_B
    assert seg["speaker"] == "SPEAKER_B"


def test_turns_from_assemblyai_converts_ms_and_prefixes():
    data = {"utterances": [
        {"start": 0, "end": 5000, "speaker": "A"},
        {"start": 5000, "end": 10000, "speaker": "B"},
    ]}
    turns = diarization._turns_from_assemblyai(data)
    assert turns == [
        {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_A"},
        {"start": 5.0, "end": 10.0, "speaker": "SPEAKER_B"},
    ]


# --------------------------------------------------------------------------- #
# diarize_upload (motor mockeado)
# --------------------------------------------------------------------------- #

def test_diarize_enriches_transcript(iso, monkeypatch):
    make_upload()
    make_transcript(segments=_two_speaker_segments())
    monkeypatch.setitem(diarization.ENGINES, "assemblyai", lambda uid: _TURNS)
    out = diarization.diarize_upload(VALID_ID)

    assert out["n_speakers"] == 2
    saved = json.loads((config.TRANSCRIPTS_DIR / VALID_ID / "transcript.json").read_text())
    assert saved["diarization"]["engine"] == "assemblyai"
    assert saved["diarization"]["n_speakers"] == 2
    assert saved["segments"][0]["speaker"] == "SPEAKER_A"
    assert saved["segments"][0]["words"][0]["speaker"] == "SPEAKER_A"
    meta = json.loads((config.UPLOADS_DIR / VALID_ID / "meta.json").read_text())
    assert meta["status"] == "diarized"


def test_diarize_without_transcript_raises(iso):
    make_upload()
    with pytest.raises(diarization.DiarizeError):
        diarization.diarize_upload(VALID_ID)


def test_diarize_unknown_engine_raises(iso):
    make_upload()
    make_transcript(segments=_two_speaker_segments())
    with pytest.raises(diarization.DiarizeError):
        diarization.diarize_upload(VALID_ID, engine="whisperx")


def test_diarize_no_turns_maps_upstream(iso, monkeypatch):
    make_upload()
    make_transcript(segments=_two_speaker_segments())
    monkeypatch.setitem(diarization.ENGINES, "assemblyai", lambda uid: [])
    with pytest.raises(diarization.UpstreamError):
        diarization.diarize_upload(VALID_ID)


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

def test_post_diarize_unknown_upload_404(iso, client):
    r = client.post(f"/uploads/{VALID_ID}/diarize")
    assert r.status_code == 404


def test_post_diarize_happy_200(iso, client, monkeypatch):
    make_upload()
    make_transcript(segments=_two_speaker_segments())
    monkeypatch.setitem(diarization.ENGINES, "assemblyai", lambda uid: _TURNS)
    r = client.post(f"/uploads/{VALID_ID}/diarize")
    assert r.status_code == 200
    assert r.json()["n_speakers"] == 2


def test_post_diarize_no_transcript_400(iso, client):
    make_upload()
    r = client.post(f"/uploads/{VALID_ID}/diarize")
    assert r.status_code == 400


def test_post_diarize_upstream_502(iso, client, monkeypatch):
    make_upload()
    make_transcript(segments=_two_speaker_segments())

    def boom(uid):
        raise diarization.UpstreamError("AssemblyAI caído")

    monkeypatch.setitem(diarization.ENGINES, "assemblyai", boom)
    r = client.post(f"/uploads/{VALID_ID}/diarize")
    assert r.status_code == 502
