import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from app.api import notifications_store
from app.api import voice_inbox_store as store
from app.api.upload_validation import UploadValidationError as VoiceInboxValidationError
from app.api.upload_validation import validate_upload
from app.config import get_settings
from app.integrations import notion_client
from app.voice import transcription, tts
from app.voice.confidence import is_low_confidence, transcription_confidence
from app.voice.transcription import TranscriptionResult
from app.voice.verifier import verify_low_confidence_transcript

log = logging.getLogger("voice_inbox_runner")

_background_tasks: set[asyncio.Task] = set()


async def submit_voice_inbox_item(
    request_id: str,
    source: str,
    filename: Optional[str],
    content_type: Optional[str],
    audio_bytes: bytes,
    duration_seconds: Optional[float],
    sample_rate_hz: Optional[int],
) -> tuple[dict[str, Any], bool]:
    """Validate, persist the audio, create the item record, and schedule
    background processing (transcribe -> create a Notion Voice Inbox page).
    Returns (record, duplicate).

    The ESP32's request_id becomes the voice_inbox_id directly, same
    idempotency-key-doubles-as-resource-id pattern as notes_runner.submit_note.
    """
    validate_upload(request_id, source, audio_bytes, duration_seconds, sample_rate_hz)

    voice_inbox_id = request_id

    existing = store.load_record(voice_inbox_id)
    if existing is not None:
        return existing, True

    audio_meta = store.save_audio(voice_inbox_id, filename, audio_bytes)
    audio_meta.update(
        {
            "original_filename": filename,
            "content_type": content_type,
            "duration_seconds": duration_seconds,
            "sample_rate_hz": sample_rate_hz,
        }
    )

    record = store.create_item(voice_inbox_id, request_id, source.strip(), audio_meta)

    task = asyncio.create_task(_execute(voice_inbox_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return record, False


async def _maybe_create_notification(voice_inbox_id: str, transcription_result: TranscriptionResult) -> None:
    """Best-effort side channel off the main voice-inbox pipeline: low
    transcription confidence -> ask the verifier whether it's worth a spoken
    notification -> if so, generate TTS and create the notification record.

    Deliberately never raises - a failure anywhere in this path (verifier
    call, TTS call, disk write) is logged and swallowed, not surfaced as a
    voice-inbox failure. The Notion page is the pipeline's primary contract
    and must still get created regardless of what happens here (see
    _execute, which continues to "sending_to_notion" unconditionally after
    this returns).
    """
    settings = get_settings()
    confidence = transcription_confidence(transcription_result)
    if not is_low_confidence(transcription_result, settings.voice_confidence_threshold):
        return

    try:
        verification = await asyncio.to_thread(
            verify_low_confidence_transcript, transcription_result.text, confidence
        )
        if not verification.worth_notifying:
            return

        audio_bytes = await asyncio.to_thread(tts.synthesize_speech, verification.spoken_message)
        notification_id = str(uuid.uuid4())
        notifications_store.create_notification(
            notification_id=notification_id,
            source_voice_inbox_id=voice_inbox_id,
            transcript=transcription_result.text,
            confidence=confidence,
            spoken_message=verification.spoken_message,
            audio_bytes=audio_bytes,
        )
    except Exception as exc:  # noqa: BLE001 - see docstring: must never fail the voice-inbox item
        log.warning("notification creation failed for voice_inbox_id=%s: %r", voice_inbox_id, exc)


async def _execute(voice_inbox_id: str) -> None:
    try:
        store.mark_status(voice_inbox_id, "transcribing", started_at=store.now_iso())
        record = store.load_record(voice_inbox_id)
        stored_filename = record["audio"]["stored_filename"]
        audio_path = store.item_dir(voice_inbox_id) / stored_filename

        transcription_result = await asyncio.to_thread(transcription.transcribe_audio, audio_path)
        store.record_transcription(voice_inbox_id, transcription_result.model_dump())

        await _maybe_create_notification(voice_inbox_id, transcription_result)

        store.mark_status(voice_inbox_id, "sending_to_notion")

        received_at = datetime.fromisoformat(record["created_at"])
        duration_seconds = record["audio"].get("duration_seconds")
        # Best-effort proxy for when the note was actually captured, not
        # ground truth: this still accumulates LAN + upload-transfer latency
        # on top of the recording itself, since the ESP32 has no RTC and
        # never sends a capture timestamp of its own.
        captured_at = (
            received_at - timedelta(seconds=duration_seconds)
            if duration_seconds is not None
            else received_at
        )
        name = captured_at.strftime("%Y-%m-%d %H:%M:%S UTC")

        page = await asyncio.to_thread(
            notion_client.create_voice_inbox_page,
            name=name,
            captured_at=captured_at,
            transcript=transcription_result.text,
        )
        store.finalize_item(voice_inbox_id, page)
    except Exception as exc:  # noqa: BLE001 - persisted as the item's failure reason
        store.fail_item(voice_inbox_id, repr(exc))
