import time

from fastapi.testclient import TestClient

from app.api import notes_runner
from app.api.main import app
from app.voice.graph import build_graph
from tests.fakes import FakeStructuredChatModel
from tests.voice_fakes import (
    VOICE_RESPONSES_BY_TITLE,
    fake_transcribe_audio,
    fake_transcribe_audio_failing,
)

FAKE_AUDIO_BYTES = b"RIFF....WAVEfake-audio-content-for-tests"


def _fake_voice_graph():
    return build_graph(llm_factory=lambda: FakeStructuredChatModel(VOICE_RESPONSES_BY_TITLE))


def _upload(client, request_id="note-req-1", source="esp32-box3", audio_bytes=FAKE_AUDIO_BYTES, **extra_form):
    files = {"audio": ("test.wav", audio_bytes, "audio/wav")}
    data = {"request_id": request_id, "source": source, **extra_form}
    return client.post("/api/v1/notes", data=data, files=files)


def _poll_until_terminal(client, note_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/notes/{note_id}")
        assert resp.status_code == 200
        status = resp.json()["status"]
        if status in ("completed", "failed"):
            return status
        time.sleep(0.02)
    return status


def test_upload_and_full_pipeline_with_mocked_transcription(monkeypatch):
    monkeypatch.setattr(notes_runner, "graph", _fake_voice_graph())
    monkeypatch.setattr(notes_runner.transcription, "transcribe_audio", fake_transcribe_audio)

    with TestClient(app) as client:
        resp = _upload(client, request_id="note-happy-1", duration_seconds=4.2, sample_rate_hz=16000)
        assert resp.status_code == 202
        body = resp.json()
        note_id = body["note_id"]
        assert body["request_id"] == "note-happy-1"
        assert body["duplicate"] is False
        assert body["status_url"] == f"/api/v1/notes/{note_id}"
        assert body["result_url"] == f"/api/v1/notes/{note_id}/result"

        status = _poll_until_terminal(client, note_id)
        assert status == "completed"

        result = client.get(f"/api/v1/notes/{note_id}/result")
        assert result.status_code == 200
        payload = result.json()
        assert payload["transcription"]["text"]
        assert payload["transcription"]["model"] == "fake-transcribe-model"
        assert payload["interpreted_note"]["category"] == "task"
        assert payload["possible_actions"]["needs_mike_review"] is False
        assert payload["grounding_report"]["grounded"] is True
        assert payload["final"]["status"] == "completed"
        assert payload["audio"]["duration_seconds"] == 4.2
        assert payload["audio"]["sample_rate_hz"] == 16000
        assert payload["model"]["transcription_model"] == "fake-transcribe-model" or payload["model"]["transcription_model"]
        assert len(payload["events"]) > 0


def test_note_id_equals_request_id():
    with TestClient(app) as client:
        resp = _upload(client, request_id="note-id-check-1")
        assert resp.status_code == 202
        assert resp.json()["note_id"] == "note-id-check-1"


def test_duplicate_request_id_does_not_launch_second_run(monkeypatch):
    monkeypatch.setattr(notes_runner, "graph", _fake_voice_graph())
    monkeypatch.setattr(notes_runner.transcription, "transcribe_audio", fake_transcribe_audio)

    with TestClient(app) as client:
        first = _upload(client, request_id="dup-note-1")
        assert first.status_code == 202
        note_id = first.json()["note_id"]
        _poll_until_terminal(client, note_id)

        second = _upload(client, request_id="dup-note-1")
        assert second.status_code == 200
        assert second.json()["duplicate"] is True
        assert second.json()["note_id"] == note_id


def test_audio_is_persisted_to_disk():
    from app.api import notes_store as store

    with TestClient(app) as client:
        resp = _upload(client, request_id="audio-persist-1")
        note_id = resp.json()["note_id"]

        audio_dir = store.note_dir(note_id)
        files = list(audio_dir.glob("audio.*"))
        assert len(files) == 1
        assert files[0].read_bytes() == FAKE_AUDIO_BYTES


def test_missing_request_id_is_rejected():
    with TestClient(app) as client:
        files = {"audio": ("test.wav", FAKE_AUDIO_BYTES, "audio/wav")}
        resp = client.post("/api/v1/notes", data={"source": "esp32-box3"}, files=files)
        assert resp.status_code == 422


def test_unsafe_request_id_is_rejected():
    with TestClient(app) as client:
        resp = _upload(client, request_id="not a safe id!!")
        assert resp.status_code == 422


def test_empty_audio_is_rejected():
    with TestClient(app) as client:
        resp = _upload(client, request_id="empty-audio-1", audio_bytes=b"")
        assert resp.status_code == 422


def test_transcription_failure_is_captured_truthfully(monkeypatch):
    monkeypatch.setattr(notes_runner, "graph", _fake_voice_graph())
    monkeypatch.setattr(notes_runner.transcription, "transcribe_audio", fake_transcribe_audio_failing)

    with TestClient(app) as client:
        resp = _upload(client, request_id="transcribe-fail-1")
        note_id = resp.json()["note_id"]

        status = _poll_until_terminal(client, note_id)
        assert status == "failed"

        status_resp = client.get(f"/api/v1/notes/{note_id}")
        assert status_resp.json()["error"]

        result_resp = client.get(f"/api/v1/notes/{note_id}/result")
        assert result_resp.status_code == 500
        assert result_resp.json()["detail"]["status"] == "failed"


def test_result_endpoint_truthfully_reports_not_yet_complete(monkeypatch):
    import threading

    from tests.fakes import FakeSlowChatModel

    gate = threading.Event()
    monkeypatch.setattr(notes_runner, "graph", build_graph(llm_factory=lambda: FakeSlowChatModel(gate)))
    monkeypatch.setattr(notes_runner.transcription, "transcribe_audio", fake_transcribe_audio)

    with TestClient(app) as client:
        resp = _upload(client, request_id="not-ready-1")
        note_id = resp.json()["note_id"]

        result_resp = client.get(f"/api/v1/notes/{note_id}/result")
        assert result_resp.status_code == 409
        assert result_resp.json()["detail"]["status"] in ("accepted", "transcribing", "processing")

        gate.set()
        _poll_until_terminal(client, note_id)


def test_unknown_note_returns_404():
    with TestClient(app) as client:
        resp = client.get("/api/v1/notes/does-not-exist")
        assert resp.status_code == 404
        resp2 = client.get("/api/v1/notes/does-not-exist/result")
        assert resp2.status_code == 404
