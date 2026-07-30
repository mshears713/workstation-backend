import time

from fastapi.testclient import TestClient

from app.api import entries_runner, notification_delivery
from app.api.main import app
from tests.entries_fakes import (
    CLARIFICATION_MESSAGE,
    FAKE_SOURCE_PAGE,
    FAKE_VAN_BUILD_LOG_PAGE,
    SEMANTIC_SPOKEN_QUESTION,
    fake_create_source_page,
    fake_create_source_page_raising,
    fake_create_van_build_log_page,
    fake_run_entry_architect_confident,
    fake_run_entry_architect_must_not_be_called,
    fake_run_entry_architect_no_van_log,
    fake_run_entry_architect_none,
    fake_run_semantic_verifier_high,
    fake_run_semantic_verifier_low,
    fake_run_semantic_verifier_must_not_be_called,
)
from tests.voice_fakes import (
    FAKE_TTS_AUDIO_BYTES,
    fake_check_audio_reliability_reliable,
    fake_check_audio_reliability_unreliable,
    fake_synthesize_speech,
    fake_transcribe_audio,
    fake_transcribe_audio_low_confidence,
)

FAKE_AUDIO_BYTES = b"RIFF....WAVEfake-audio-content-for-tests"


def _upload(client, request_id, audio_bytes=FAKE_AUDIO_BYTES, **extra_form):
    files = {"audio": ("test.wav", audio_bytes, "audio/wav")}
    data = {"request_id": request_id, "source": "esp32-box3", **extra_form}
    return client.post("/api/v1/entries", data=data, files=files)


def _poll_until_terminal(client, entry_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/entries/{entry_id}")
        assert resp.status_code == 200
        status = resp.json()["status"]
        if status in ("completed", "completed_with_warnings", "failed"):
            return status
        time.sleep(0.02)
    return status


def _patch_common(monkeypatch, *, transcribe=fake_transcribe_audio, audio_reliability=fake_check_audio_reliability_reliable):
    monkeypatch.setattr(entries_runner.transcription, "transcribe_audio", transcribe)
    monkeypatch.setattr(entries_runner, "check_audio_reliability", audio_reliability)
    monkeypatch.setattr(notification_delivery.tts, "synthesize_speech", fake_synthesize_speech)


def test_reliable_entry_creates_source_and_van_build_log_silently(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(entries_runner, "run_entry_architect", fake_run_entry_architect_confident)
    monkeypatch.setattr(entries_runner.notion_client, "create_source_page", fake_create_source_page)
    monkeypatch.setattr(entries_runner.notion_client, "create_van_build_log_page", fake_create_van_build_log_page)
    monkeypatch.setattr(entries_runner, "run_semantic_verifier", fake_run_semantic_verifier_high)

    with TestClient(app) as client:
        resp = _upload(client, request_id="entry-happy-1")
        assert resp.status_code == 202
        entry_id = resp.json()["entry_id"]

        assert _poll_until_terminal(client, entry_id) == "completed"

        result = client.get(f"/api/v1/entries/{entry_id}/result").json()
        assert result["source_page"] == FAKE_SOURCE_PAGE
        assert result["van_build_log_page"] == FAKE_VAN_BUILD_LOG_PAGE
        assert result["semantic_verification"]["confidence"] == "high"

        # Silent - no notification for a clean, confident entry.
        assert client.get("/api/v1/notifications/pending").json()["pending"] is False


def test_unreliable_audio_creates_one_notification_and_never_reaches_architect_or_verifier(monkeypatch):
    monkeypatch.setattr(entries_runner.transcription, "transcribe_audio", fake_transcribe_audio_low_confidence)
    monkeypatch.setattr(entries_runner, "check_audio_reliability", fake_check_audio_reliability_unreliable)
    monkeypatch.setattr(notification_delivery.tts, "synthesize_speech", fake_synthesize_speech)
    # Tripwires: neither should ever be invoked on the unreliable-audio path.
    monkeypatch.setattr(entries_runner, "run_entry_architect", fake_run_entry_architect_must_not_be_called)
    monkeypatch.setattr(entries_runner, "run_semantic_verifier", fake_run_semantic_verifier_must_not_be_called)

    with TestClient(app) as client:
        resp = _upload(client, request_id="entry-bad-audio-1")
        entry_id = resp.json()["entry_id"]

        assert _poll_until_terminal(client, entry_id) == "completed_with_warnings"

        result = client.get(f"/api/v1/entries/{entry_id}/result").json()
        assert result["source_page"] is None
        assert result["van_build_log_page"] is None

        pending = client.get("/api/v1/notifications/pending").json()
        assert pending["pending"] is True
        assert pending["count"] == 1


def test_van_build_log_uncertain_preserves_source_and_notifies_without_verifier(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(entries_runner, "run_entry_architect", fake_run_entry_architect_no_van_log)
    monkeypatch.setattr(entries_runner.notion_client, "create_source_page", fake_create_source_page)
    # Tripwire: must not be called since van_build_log is None.
    monkeypatch.setattr(entries_runner, "run_semantic_verifier", fake_run_semantic_verifier_must_not_be_called)

    with TestClient(app) as client:
        resp = _upload(client, request_id="entry-no-vbl-1")
        entry_id = resp.json()["entry_id"]

        assert _poll_until_terminal(client, entry_id) == "completed_with_warnings"

        result = client.get(f"/api/v1/entries/{entry_id}/result").json()
        assert result["source_page"] == FAKE_SOURCE_PAGE
        assert result["van_build_log_page"] is None

        pending = client.get("/api/v1/notifications/pending").json()
        assert pending["pending"] is True
        assert pending["count"] == 1
        notification = client.get(f"/api/v1/notifications/{pending['notification_id']}").json()
        assert notification["spoken_message"] == CLARIFICATION_MESSAGE


def test_semantically_ambiguous_entry_creates_one_clarification_notification(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(entries_runner, "run_entry_architect", fake_run_entry_architect_confident)
    monkeypatch.setattr(entries_runner.notion_client, "create_source_page", fake_create_source_page)
    monkeypatch.setattr(entries_runner.notion_client, "create_van_build_log_page", fake_create_van_build_log_page)
    monkeypatch.setattr(entries_runner, "run_semantic_verifier", fake_run_semantic_verifier_low)

    with TestClient(app) as client:
        resp = _upload(client, request_id="entry-ambiguous-1")
        entry_id = resp.json()["entry_id"]

        assert _poll_until_terminal(client, entry_id) == "completed_with_warnings"

        result = client.get(f"/api/v1/entries/{entry_id}/result").json()
        assert result["source_page"] == FAKE_SOURCE_PAGE
        assert result["van_build_log_page"] == FAKE_VAN_BUILD_LOG_PAGE

        pending = client.get("/api/v1/notifications/pending").json()
        assert pending["pending"] is True
        assert pending["count"] == 1
        notification = client.get(f"/api/v1/notifications/{pending['notification_id']}").json()
        assert notification["spoken_message"] == SEMANTIC_SPOKEN_QUESTION


def test_entry_cannot_generate_both_audio_and_semantic_notifications(monkeypatch):
    """Mutual exclusivity, explicitly: the unreliable-audio path stops before
    the semantic verifier can ever run (see the tripwire test above), so at
    most one notification is ever created per entry. This test asserts the
    count directly as its own regression guard."""
    monkeypatch.setattr(entries_runner.transcription, "transcribe_audio", fake_transcribe_audio_low_confidence)
    monkeypatch.setattr(entries_runner, "check_audio_reliability", fake_check_audio_reliability_unreliable)
    monkeypatch.setattr(notification_delivery.tts, "synthesize_speech", fake_synthesize_speech)

    with TestClient(app) as client:
        resp = _upload(client, request_id="entry-mutex-1")
        entry_id = resp.json()["entry_id"]
        _poll_until_terminal(client, entry_id)

        assert client.get("/api/v1/notifications/pending").json()["count"] == 1


def test_entry_architect_total_failure_falls_back_to_minimal_source(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(entries_runner, "run_entry_architect", fake_run_entry_architect_none)
    monkeypatch.setattr(entries_runner.notion_client, "create_source_page", fake_create_source_page)
    monkeypatch.setattr(entries_runner, "run_semantic_verifier", fake_run_semantic_verifier_must_not_be_called)

    with TestClient(app) as client:
        resp = _upload(client, request_id="entry-architect-fails-1")
        entry_id = resp.json()["entry_id"]

        assert _poll_until_terminal(client, entry_id) == "completed_with_warnings"

        result = client.get(f"/api/v1/entries/{entry_id}/result").json()
        assert result["source_page"] == FAKE_SOURCE_PAGE
        assert result["van_build_log_page"] is None
        assert client.get("/api/v1/notifications/pending").json()["count"] == 1


def test_source_creation_failure_fails_the_entry(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(entries_runner, "run_entry_architect", fake_run_entry_architect_confident)
    monkeypatch.setattr(entries_runner.notion_client, "create_source_page", fake_create_source_page_raising)

    with TestClient(app) as client:
        resp = _upload(client, request_id="entry-source-fails-1")
        entry_id = resp.json()["entry_id"]

        assert _poll_until_terminal(client, entry_id) == "failed"
        status_resp = client.get(f"/api/v1/entries/{entry_id}").json()
        assert status_resp["error"]


def test_duplicate_request_id_does_not_launch_second_run(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(entries_runner, "run_entry_architect", fake_run_entry_architect_confident)
    monkeypatch.setattr(entries_runner.notion_client, "create_source_page", fake_create_source_page)
    monkeypatch.setattr(entries_runner.notion_client, "create_van_build_log_page", fake_create_van_build_log_page)
    monkeypatch.setattr(entries_runner, "run_semantic_verifier", fake_run_semantic_verifier_high)

    with TestClient(app) as client:
        first = _upload(client, request_id="entry-dup-1")
        assert first.status_code == 202
        entry_id = first.json()["entry_id"]
        _poll_until_terminal(client, entry_id)

        second = _upload(client, request_id="entry-dup-1")
        assert second.status_code == 200
        assert second.json()["duplicate"] is True
        assert second.json()["entry_id"] == entry_id


def test_unknown_entry_id_returns_404():
    with TestClient(app) as client:
        assert client.get("/api/v1/entries/does-not-exist").status_code == 404
        assert client.get("/api/v1/entries/does-not-exist/result").status_code == 404
