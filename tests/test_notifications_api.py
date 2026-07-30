import time

from fastapi.testclient import TestClient

from app.api import voice_inbox_runner
from app.api.main import app
from tests.voice_fakes import (
    FAKE_SPOKEN_MESSAGE,
    FAKE_TTS_AUDIO_BYTES,
    fake_create_voice_inbox_page,
    fake_synthesize_speech,
    fake_synthesize_speech_raising,
    fake_transcribe_audio,
    fake_transcribe_audio_low_confidence,
    fake_verify_not_worth_notifying,
    fake_verify_raising,
    fake_verify_worth_notifying,
)

FAKE_AUDIO_BYTES = b"RIFF....WAVEfake-audio-content-for-tests"


def _upload(client, request_id, audio_bytes=FAKE_AUDIO_BYTES, **extra_form):
    files = {"audio": ("test.wav", audio_bytes, "audio/wav")}
    data = {"request_id": request_id, "source": "esp32-box3", **extra_form}
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


def _patch_happy_low_confidence(monkeypatch):
    monkeypatch.setattr(voice_inbox_runner.transcription, "transcribe_audio", fake_transcribe_audio_low_confidence)
    monkeypatch.setattr(voice_inbox_runner, "verify_low_confidence_transcript", fake_verify_worth_notifying)
    monkeypatch.setattr(voice_inbox_runner.tts, "synthesize_speech", fake_synthesize_speech)
    monkeypatch.setattr(voice_inbox_runner.notion_client, "create_voice_inbox_page", fake_create_voice_inbox_page)


def test_low_confidence_worth_notifying_creates_pending_notification(monkeypatch):
    _patch_happy_low_confidence(monkeypatch)

    with TestClient(app) as client:
        resp = _upload(client, request_id="notif-happy-1")
        voice_inbox_id = resp.json()["voice_inbox_id"]
        assert _poll_until_terminal(client, voice_inbox_id) == "completed"

        pending = client.get("/api/v1/notifications/pending")
        assert pending.status_code == 200
        body = pending.json()
        assert body["pending"] is True
        assert body["count"] == 1
        notification_id = body["notification_id"]
        assert notification_id

        detail = client.get(f"/api/v1/notifications/{notification_id}")
        assert detail.status_code == 200
        detail_body = detail.json()
        assert detail_body["status"] == "pending"
        assert detail_body["source_voice_inbox_id"] == voice_inbox_id
        assert detail_body["spoken_message"] == FAKE_SPOKEN_MESSAGE
        assert detail_body["confidence"] is not None and detail_body["confidence"] < 0.05

        audio = client.get(f"/api/v1/notifications/{notification_id}/audio")
        assert audio.status_code == 200
        assert audio.content == FAKE_TTS_AUDIO_BYTES

        ack = client.post(f"/api/v1/notifications/{notification_id}/ack")
        assert ack.status_code == 200
        ack_body = ack.json()
        assert ack_body["status"] == "delivered"
        assert ack_body["pending_remaining"] == 0

        pending_after = client.get("/api/v1/notifications/pending")
        assert pending_after.json() == {
            "pending": False,
            "count": 0,
            "notification_id": None,
            "created_at": None,
        }


def test_high_confidence_transcript_never_creates_notification(monkeypatch):
    monkeypatch.setattr(voice_inbox_runner.transcription, "transcribe_audio", fake_transcribe_audio)
    monkeypatch.setattr(voice_inbox_runner.notion_client, "create_voice_inbox_page", fake_create_voice_inbox_page)

    with TestClient(app) as client:
        resp = _upload(client, request_id="notif-high-confidence-1")
        voice_inbox_id = resp.json()["voice_inbox_id"]
        assert _poll_until_terminal(client, voice_inbox_id) == "completed"

        pending = client.get("/api/v1/notifications/pending")
        assert pending.json()["pending"] is False


def test_low_confidence_but_verifier_declines_creates_no_notification(monkeypatch):
    monkeypatch.setattr(voice_inbox_runner.transcription, "transcribe_audio", fake_transcribe_audio_low_confidence)
    monkeypatch.setattr(voice_inbox_runner, "verify_low_confidence_transcript", fake_verify_not_worth_notifying)
    monkeypatch.setattr(voice_inbox_runner.notion_client, "create_voice_inbox_page", fake_create_voice_inbox_page)

    with TestClient(app) as client:
        resp = _upload(client, request_id="notif-declined-1")
        voice_inbox_id = resp.json()["voice_inbox_id"]
        assert _poll_until_terminal(client, voice_inbox_id) == "completed"

        pending = client.get("/api/v1/notifications/pending")
        assert pending.json()["pending"] is False


def test_verifier_failure_does_not_fail_voice_inbox_item(monkeypatch):
    monkeypatch.setattr(voice_inbox_runner.transcription, "transcribe_audio", fake_transcribe_audio_low_confidence)
    monkeypatch.setattr(voice_inbox_runner, "verify_low_confidence_transcript", fake_verify_raising)
    monkeypatch.setattr(voice_inbox_runner.notion_client, "create_voice_inbox_page", fake_create_voice_inbox_page)

    with TestClient(app) as client:
        resp = _upload(client, request_id="notif-verifier-fails-1")
        voice_inbox_id = resp.json()["voice_inbox_id"]
        # The Notion pipeline is the primary contract - it must still
        # complete even though the notification side channel blew up.
        assert _poll_until_terminal(client, voice_inbox_id) == "completed"
        assert client.get("/api/v1/notifications/pending").json()["pending"] is False


def test_tts_failure_does_not_fail_voice_inbox_item(monkeypatch):
    monkeypatch.setattr(voice_inbox_runner.transcription, "transcribe_audio", fake_transcribe_audio_low_confidence)
    monkeypatch.setattr(voice_inbox_runner, "verify_low_confidence_transcript", fake_verify_worth_notifying)
    monkeypatch.setattr(voice_inbox_runner.tts, "synthesize_speech", fake_synthesize_speech_raising)
    monkeypatch.setattr(voice_inbox_runner.notion_client, "create_voice_inbox_page", fake_create_voice_inbox_page)

    with TestClient(app) as client:
        resp = _upload(client, request_id="notif-tts-fails-1")
        voice_inbox_id = resp.json()["voice_inbox_id"]
        assert _poll_until_terminal(client, voice_inbox_id) == "completed"
        assert client.get("/api/v1/notifications/pending").json()["pending"] is False


def test_multiple_pending_notifications_are_fifo_and_counted(monkeypatch):
    _patch_happy_low_confidence(monkeypatch)

    with TestClient(app) as client:
        first = _upload(client, request_id="notif-fifo-1")
        first_id = first.json()["voice_inbox_id"]
        _poll_until_terminal(client, first_id)

        second = _upload(client, request_id="notif-fifo-2")
        second_id = second.json()["voice_inbox_id"]
        _poll_until_terminal(client, second_id)

        pending = client.get("/api/v1/notifications/pending").json()
        assert pending["pending"] is True
        assert pending["count"] == 2

        first_notification_id = pending["notification_id"]
        first_detail = client.get(f"/api/v1/notifications/{first_notification_id}").json()
        assert first_detail["source_voice_inbox_id"] == first_id

        client.post(f"/api/v1/notifications/{first_notification_id}/ack")

        pending_after = client.get("/api/v1/notifications/pending").json()
        assert pending_after["count"] == 1
        assert pending_after["notification_id"] != first_notification_id


def test_unknown_notification_id_returns_404():
    with TestClient(app) as client:
        assert client.get("/api/v1/notifications/does-not-exist").status_code == 404
        assert client.get("/api/v1/notifications/does-not-exist/audio").status_code == 404
        assert client.post("/api/v1/notifications/does-not-exist/ack").status_code == 404


def test_acking_already_delivered_notification_is_idempotent(monkeypatch):
    _patch_happy_low_confidence(monkeypatch)

    with TestClient(app) as client:
        resp = _upload(client, request_id="notif-double-ack-1")
        voice_inbox_id = resp.json()["voice_inbox_id"]
        _poll_until_terminal(client, voice_inbox_id)

        notification_id = client.get("/api/v1/notifications/pending").json()["notification_id"]
        first_ack = client.post(f"/api/v1/notifications/{notification_id}/ack")
        assert first_ack.status_code == 200

        second_ack = client.post(f"/api/v1/notifications/{notification_id}/ack")
        assert second_ack.status_code == 200
        assert second_ack.json()["status"] == "delivered"
