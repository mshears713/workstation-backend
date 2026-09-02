import time

from fastapi.testclient import TestClient

from app.api import voice_inbox_runner
from app.api import voice_inbox_store
from app.api.main import app
from tests.voice_fakes import (
    fake_create_voice_inbox_page,
    fake_create_voice_inbox_page_failing,
    fake_transcribe_audio,
    fake_transcribe_audio_failing,
)

FAKE_AUDIO_BYTES = b"RIFF....WAVEfake-audio-content-for-tests"


def _upload(client, request_id="voice-inbox-req-1", source="esp32-box3", audio_bytes=FAKE_AUDIO_BYTES, **extra_form):
    files = {"audio": ("test.wav", audio_bytes, "audio/wav")}
    data = {"request_id": request_id, "source": source, **extra_form}
    return client.post("/api/v1/voice-inbox", data=data, files=files)


def _poll_until_terminal(client, voice_inbox_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/voice-inbox/{voice_inbox_id}")
        assert resp.status_code == 200
        status = resp.json()["status"]
        if status in ("completed", "failed"):
            return status
        time.sleep(0.02)
    return status


def test_upload_and_full_pipeline_with_mocked_transcription_and_notion(monkeypatch):
    monkeypatch.setattr(voice_inbox_runner.transcription, "transcribe_audio", fake_transcribe_audio)
    monkeypatch.setattr(voice_inbox_runner.notion_client, "create_voice_inbox_page", fake_create_voice_inbox_page)

    with TestClient(app) as client:
        resp = _upload(client, request_id="voice-inbox-happy-1", duration_seconds=4.2, sample_rate_hz=16000)
        assert resp.status_code == 202
        body = resp.json()
        voice_inbox_id = body["voice_inbox_id"]
        assert body["request_id"] == "voice-inbox-happy-1"
        assert body["duplicate"] is False
        assert body["status_url"] == f"/api/v1/voice-inbox/{voice_inbox_id}"
        assert body["result_url"] == f"/api/v1/voice-inbox/{voice_inbox_id}/result"

        status = _poll_until_terminal(client, voice_inbox_id)
        assert status == "completed"

        result = client.get(f"/api/v1/voice-inbox/{voice_inbox_id}/result")
        assert result.status_code == 200
        payload = result.json()
        assert payload["transcription"]["text"]
        assert payload["notion"]["url"] == "https://notion.so/fake-notion-page-id"
        assert payload["audio"]["duration_seconds"] == 4.2
        assert payload["audio"]["sample_rate_hz"] == 16000


def test_voice_inbox_id_equals_request_id():
    with TestClient(app) as client:
        resp = _upload(client, request_id="voice-inbox-id-check-1")
        assert resp.status_code == 202
        assert resp.json()["voice_inbox_id"] == "voice-inbox-id-check-1"


def test_duplicate_request_id_does_not_launch_second_run(monkeypatch):
    monkeypatch.setattr(voice_inbox_runner.transcription, "transcribe_audio", fake_transcribe_audio)
    monkeypatch.setattr(voice_inbox_runner.notion_client, "create_voice_inbox_page", fake_create_voice_inbox_page)

    with TestClient(app) as client:
        first = _upload(client, request_id="dup-voice-inbox-1")
        assert first.status_code == 202
        voice_inbox_id = first.json()["voice_inbox_id"]
        _poll_until_terminal(client, voice_inbox_id)

        second = _upload(client, request_id="dup-voice-inbox-1")
        assert second.status_code == 200
        assert second.json()["duplicate"] is True
        assert second.json()["voice_inbox_id"] == voice_inbox_id


def test_audio_is_persisted_to_disk():
    from app.api import voice_inbox_store as store

    with TestClient(app) as client:
        resp = _upload(client, request_id="voice-inbox-audio-persist-1")
        voice_inbox_id = resp.json()["voice_inbox_id"]

        audio_dir = store.item_dir(voice_inbox_id)
        files = list(audio_dir.glob("audio.*"))
        assert len(files) == 1
        assert files[0].read_bytes() == FAKE_AUDIO_BYTES


def test_missing_request_id_is_rejected():
    with TestClient(app) as client:
        files = {"audio": ("test.wav", FAKE_AUDIO_BYTES, "audio/wav")}
        resp = client.post("/api/v1/voice-inbox", data={"source": "esp32-box3"}, files=files)
        assert resp.status_code == 422


def test_unsafe_request_id_is_rejected():
    with TestClient(app) as client:
        resp = _upload(client, request_id="not a safe id!!")
        assert resp.status_code == 422


def test_empty_audio_is_rejected():
    with TestClient(app) as client:
        resp = _upload(client, request_id="voice-inbox-empty-audio-1", audio_bytes=b"")
        assert resp.status_code == 422


def test_transcription_failure_is_captured_truthfully(monkeypatch):
    monkeypatch.setattr(voice_inbox_runner.transcription, "transcribe_audio", fake_transcribe_audio_failing)

    with TestClient(app) as client:
        resp = _upload(client, request_id="voice-inbox-transcribe-fail-1")
        voice_inbox_id = resp.json()["voice_inbox_id"]

        status = _poll_until_terminal(client, voice_inbox_id)
        assert status == "failed"

        status_resp = client.get(f"/api/v1/voice-inbox/{voice_inbox_id}")
        assert status_resp.json()["error"]

        result_resp = client.get(f"/api/v1/voice-inbox/{voice_inbox_id}/result")
        assert result_resp.status_code == 500
        assert result_resp.json()["detail"]["status"] == "failed"


def test_notion_failure_is_captured_truthfully(monkeypatch):
    monkeypatch.setattr(voice_inbox_runner.transcription, "transcribe_audio", fake_transcribe_audio)
    monkeypatch.setattr(
        voice_inbox_runner.notion_client, "create_voice_inbox_page", fake_create_voice_inbox_page_failing
    )

    with TestClient(app) as client:
        resp = _upload(client, request_id="voice-inbox-notion-fail-1")
        voice_inbox_id = resp.json()["voice_inbox_id"]

        status = _poll_until_terminal(client, voice_inbox_id)
        assert status == "failed"


def test_result_endpoint_truthfully_reports_not_yet_complete(monkeypatch):
    import threading

    gate = threading.Event()

    def slow_transcribe(audio_path):
        gate.wait(timeout=5.0)
        return fake_transcribe_audio(audio_path)

    monkeypatch.setattr(voice_inbox_runner.transcription, "transcribe_audio", slow_transcribe)
    monkeypatch.setattr(voice_inbox_runner.notion_client, "create_voice_inbox_page", fake_create_voice_inbox_page)

    with TestClient(app) as client:
        resp = _upload(client, request_id="voice-inbox-not-ready-1")
        voice_inbox_id = resp.json()["voice_inbox_id"]

        result_resp = client.get(f"/api/v1/voice-inbox/{voice_inbox_id}/result")
        assert result_resp.status_code == 409
        assert result_resp.json()["detail"]["status"] in ("accepted", "transcribing", "sending_to_notion")

        gate.set()
        _poll_until_terminal(client, voice_inbox_id)


def test_unknown_voice_inbox_item_returns_404():
    with TestClient(app) as client:
        resp = client.get("/api/v1/voice-inbox/does-not-exist")
        assert resp.status_code == 404
        resp2 = client.get("/api/v1/voice-inbox/does-not-exist/result")
        assert resp2.status_code == 404


# --- project_hint (issue #5) ------------------------------------------------
#
# NOTE carries the operator's project selection (main/project_selector.h) as
# routing/context metadata. The Voice Inbox stays authoritative downstream;
# the backend's job is only to record what was selected, unchanged.

def test_project_hint_is_persisted_on_single_shot_upload():
    with TestClient(app) as client:
        resp = _upload(client, request_id="vi-hint-1", project_hint="van2")
        assert resp.status_code == 202
        record = voice_inbox_store.load_record("vi-hint-1")
        assert record["project_hint"] == "van2"


def test_project_hint_is_optional():
    """NOTE must still work with no project chosen - the selector defaults to
    NONE and resets on reboot, so most notes will not carry one."""
    with TestClient(app) as client:
        resp = _upload(client, request_id="vi-hint-absent-1")
        assert resp.status_code == 202
        assert voice_inbox_store.load_record("vi-hint-absent-1")["project_hint"] is None


def test_project_hint_is_persisted_through_the_chunked_path():
    """The path the device actually uses - the hint rides on the finish call,
    not on any chunk."""
    with TestClient(app) as client:
        assert client.post(
            "/api/v1/voice-inbox/vi-hint-chunked-1/chunk?offset=0", content=b"\x01\x02" * 512
        ).status_code == 202
        resp = client.post(
            "/api/v1/voice-inbox/finish",
            data={
                "request_id": "vi-hint-chunked-1",
                "source": "esp32-box3",
                "sample_rate_hz": 16000,
                "project_hint": "van1",
            },
        )
        assert resp.status_code == 202
        assert voice_inbox_store.load_record("vi-hint-chunked-1")["project_hint"] == "van1"


def test_unknown_project_hint_is_stored_verbatim_not_rejected():
    """Deliberately not validated against an enum here. The device owns the
    list today and #6 moves it to a backend-managed catalog; rejecting
    unknown values now would break the device the moment that list changes."""
    with TestClient(app) as client:
        resp = _upload(client, request_id="vi-hint-unknown-1", project_hint="some-future-project")
        assert resp.status_code == 202
        assert voice_inbox_store.load_record("vi-hint-unknown-1")["project_hint"] == "some-future-project"
