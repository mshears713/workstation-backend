"""Voice-captured GitHub issues (issue #8, backend half).

The device uploads PCM chunks exactly as it does for notes, then calls
/finish with a repo_id. The backend assembles, transcribes and files.

Both transcription and GitHub are faked here - the suite must never spend
money on OpenAI or file a real issue.
"""

import json
import struct
import wave
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from app.api import issues_router, issues_store, projects_catalog
from app.api.main import app
from app.integrations import github_client
from app.voice.transcription import TranscriptionResult

CATALOG = {
    "version": 1,
    "projects": [{"id": "van1", "label": "VAN1"}],
    "repos": [{"id": "wk", "label": "WKSTN", "repo": "owner/workstation"}],
}

# One second of quiet 16kHz mono PCM - enough to be a valid, non-empty WAV.
PCM = struct.pack("<16000h", *([120] * 16000))


@pytest.fixture(autouse=True)
def catalog(tmp_path, monkeypatch):
    path = tmp_path / "projects.json"
    path.write_text(json.dumps(CATALOG), encoding="utf-8")
    monkeypatch.setattr(projects_catalog, "CATALOG_PATH", path)


@pytest.fixture
def fake_github(monkeypatch):
    calls = []

    def _create(repo, title, body, labels=None):
        calls.append({"repo": repo, "title": title, "body": body})
        return {"number": 99, "url": f"https://github.com/{repo}/issues/99", "api_url": "a"}

    monkeypatch.setattr(issues_router.github_client, "create_issue", _create)
    return calls


@pytest.fixture
def fake_transcribe(monkeypatch):
    """Returns whatever `.text` is set to, and records the path it was given
    so the audio-was-saved assertion has something to check."""
    state = {"text": "The recording overlay crowds the counter. Worth moving it.", "paths": []}

    def _transcribe(path):
        state["paths"].append(path)
        return TranscriptionResult(text=state["text"], language="en", model="fake", raw={})

    monkeypatch.setattr(issues_router.transcription, "transcribe_audio", _transcribe)
    return state


def _chunk(client, request_id, data, offset=0):
    return client.post(f"/api/v1/issues/{request_id}/chunk?offset={offset}", content=data)


def _finish(client, request_id="GO-1", repo_id="wk", **over):
    data = {
        "request_id": request_id,
        "repo_id": repo_id,
        "source": "esp32-box3",
        "sample_rate_hz": 16000,
    }
    data.update(over)
    return client.post("/api/v1/issues/finish", data=data)


# --- the whole path -------------------------------------------------------

def test_chunked_capture_transcribes_and_files(fake_github, fake_transcribe):
    with TestClient(app) as client:
        assert _chunk(client, "GO-1", PCM).status_code == 202
        resp = _finish(client)

    assert resp.status_code == 201
    body = resp.json()
    assert body["issue"]["number"] == 99
    assert body["repo"] == "owner/workstation"
    assert fake_github[0]["title"] == "The recording overlay crowds the counter."
    assert "Worth moving it." in fake_github[0]["body"]


def test_audio_is_kept_alongside_the_record(fake_github, fake_transcribe):
    """The transcript is a model's opinion; the audio is the evidence."""
    with TestClient(app) as client:
        _chunk(client, "GO-audio", PCM)
        _finish(client, request_id="GO-audio")

    path = fake_transcribe["paths"][0]
    assert path.exists()
    with wave.open(str(path)) as w:
        assert w.getframerate() == 16000
        assert w.getnframes() == 16000


def test_duration_is_recorded_as_metadata(fake_github, fake_transcribe):
    with TestClient(app) as client:
        _chunk(client, "GO-dur", PCM)
        _finish(client, request_id="GO-dur")
    assert "duration_seconds: 1.0" in fake_github[0]["body"]


def test_chunks_assemble_in_order_before_transcription(fake_github, fake_transcribe):
    with TestClient(app) as client:
        _chunk(client, "GO-multi", PCM, offset=0)
        _chunk(client, "GO-multi", PCM, offset=len(PCM))
        _finish(client, request_id="GO-multi")

    with wave.open(str(fake_transcribe["paths"][0])) as w:
        assert w.getnframes() == 32000  # both chunks, not one


# --- the guards still apply on this path ---------------------------------

def test_unknown_repo_id_rejected_after_transcription(fake_github, fake_transcribe):
    with TestClient(app) as client:
        _chunk(client, "GO-badrepo", PCM)
        resp = _finish(client, request_id="GO-badrepo", repo_id="nope")
    assert resp.status_code == 422
    assert fake_github == []


def test_repeat_finish_does_not_refile_or_retranscribe(fake_github, fake_transcribe):
    """The firmware retries finish on a lost response. That must not file a
    second issue - nor pay OpenAI twice."""
    with TestClient(app) as client:
        _chunk(client, "GO-dupe", PCM)
        first = _finish(client, request_id="GO-dupe")
        second = _finish(client, request_id="GO-dupe")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert len(fake_github) == 1
    assert len(fake_transcribe["paths"]) == 1, "must not transcribe twice"


def test_empty_transcript_is_not_filed(fake_github, fake_transcribe):
    """Silence, or a capture that picked up nothing. An empty issue would be
    worse than saying so."""
    fake_transcribe["text"] = "   "
    with TestClient(app) as client:
        _chunk(client, "GO-silent", PCM)
        resp = _finish(client, request_id="GO-silent")

    assert resp.status_code == 422
    assert "empty" in resp.json()["detail"]
    assert fake_github == []
    assert issues_store.load_record("GO-silent")["status"] == "failed"


def test_transcription_failure_is_recorded_and_surfaced(fake_github, monkeypatch):
    def _boom(path):
        raise RuntimeError("openai exploded")

    monkeypatch.setattr(issues_router.transcription, "transcribe_audio", _boom)
    with TestClient(app) as client:
        _chunk(client, "GO-fail", PCM)
        resp = _finish(client, request_id="GO-fail")

    assert resp.status_code == 502
    assert "transcription failed" in resp.json()["detail"]
    assert issues_store.load_record("GO-fail")["status"] == "failed"
    assert fake_github == []


def test_finish_with_no_chunks_is_rejected(fake_github, fake_transcribe):
    """A header-only WAV is not empty by byte count, so validate_upload would
    let it through - the transcript check is what actually catches it."""
    with TestClient(app) as client:
        resp = _finish(client, request_id="GO-nochunks")
    assert resp.status_code in (422, 502)
    assert fake_github == []


def test_out_of_order_chunk_is_rejected(fake_github, fake_transcribe):
    with TestClient(app) as client:
        _chunk(client, "GO-gap", PCM, offset=0)
        resp = _chunk(client, "GO-gap", PCM, offset=999999)
    assert resp.status_code == 409


def test_cancel_discards_the_capture(fake_github, fake_transcribe):
    with TestClient(app) as client:
        _chunk(client, "GO-cancel", PCM)
        assert client.post("/api/v1/issues/GO-cancel/cancel").status_code == 202
        # A fresh capture under the same id starts from zero.
        assert _chunk(client, "GO-cancel", PCM, offset=0).json()["total_bytes"] == len(PCM)
