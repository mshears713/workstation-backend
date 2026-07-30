import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.api import entries_store as store
from app.api.notification_delivery import create_spoken_notification, notify_audio_unreliable
from app.api.upload_validation import UploadValidationError as EntryValidationError
from app.api.upload_validation import validate_upload
from app.config import get_settings
from app.entries.entry_architect import run_entry_architect
from app.entries.schemas import EntryArchitectResult, SourceFields
from app.entries.verifier import run_semantic_verifier
from app.integrations import notion_client
from app.voice import transcription
from app.voice.audio_reliability import check_audio_reliability
from app.voice.transcription import TranscriptionResult

log = logging.getLogger("entries_runner")

_background_tasks: set[asyncio.Task] = set()

_ARCHITECT_FAILURE_MESSAGE = (
    "I have a new voice capture but couldn't process it automatically - "
    "you may want to review it directly."
)


async def submit_entry(
    request_id: str,
    source: str,
    filename: Optional[str],
    content_type: Optional[str],
    audio_bytes: bytes,
    duration_seconds: Optional[float],
    sample_rate_hz: Optional[int],
) -> tuple[dict[str, Any], bool]:
    """Validate, persist the audio, create the item record, and schedule
    background processing. Returns (record, duplicate) - same
    idempotency-key-doubles-as-resource-id pattern as notes/voice-inbox.
    """
    validate_upload(request_id, source, audio_bytes, duration_seconds, sample_rate_hz)

    entry_id = request_id

    existing = store.load_record(entry_id)
    if existing is not None:
        return existing, True

    audio_meta = store.save_audio(entry_id, filename, audio_bytes)
    audio_meta.update(
        {
            "original_filename": filename,
            "content_type": content_type,
            "duration_seconds": duration_seconds,
            "sample_rate_hz": sample_rate_hz,
        }
    )

    record = store.create_item(entry_id, request_id, source.strip(), audio_meta)

    task = asyncio.create_task(_execute(entry_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return record, False


def _captured_at(record: dict[str, Any]) -> datetime:
    """Same best-effort proxy voice_inbox_runner.py uses: created_at minus
    the recording's own duration, since the ESP32 has no RTC and never
    sends a capture timestamp of its own."""
    received_at = datetime.fromisoformat(record["created_at"])
    duration_seconds = record["audio"].get("duration_seconds")
    if duration_seconds is None:
        return received_at
    return received_at - timedelta(seconds=duration_seconds)


def _confidence_tier(score: Optional[float]) -> str:
    """Maps the audio-reliability score to the Sources database's Capture
    Confidence select ("Low"/"Medium"/"High"). "Low" is not reachable via
    this call site today - check_audio_reliability() already routes
    anything below threshold to the audio-unreliable notification before
    entries ever get here - but the mapping stays correct/total for when
    the threshold changes."""
    if score is None:
        return "Medium"
    if score >= 0.9:
        return "High"
    if score < get_settings().audio_confidence_threshold:
        return "Low"
    return "Medium"


def _fallback_source_fields(transcript: str) -> SourceFields:
    """Used only when the entry architect fails to produce any valid
    structured output at all (not the same as a valid response with
    van_build_log=null) - preserves the raw transcript as evidence rather
    than losing it, per the directive that a Source should always survive
    even when a build-log entry can't be made."""
    return SourceFields(
        name=f"Voice capture {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        cue="",
        summary="",
        observation=transcript,
        open_questions="Entry architect could not process this transcript automatically.",
    )


async def _execute(entry_id: str) -> None:
    try:
        store.mark_status(entry_id, "transcribing", started_at=store.now_iso())
        record = store.load_record(entry_id)
        stored_filename = record["audio"]["stored_filename"]
        audio_path = store.item_dir(entry_id) / stored_filename

        transcription_result = await asyncio.to_thread(transcription.transcribe_audio, audio_path)
        store.record_transcription(entry_id, transcription_result.model_dump())

        # Step 1: audio reliability - shared with NOTE, see
        # app/voice/audio_reliability.py. Unlike NOTE, GO stops here on an
        # unreliable recording rather than continuing.
        settings = get_settings()
        reliability = await asyncio.to_thread(
            check_audio_reliability, transcription_result, settings.audio_confidence_threshold
        )
        store.record_audio_reliability(entry_id, reliability.model_dump())

        if not reliability.reliable:
            await notify_audio_unreliable(entry_id, transcription_result.text, reliability)
            store.finalize_item(entry_id, status="completed_with_warnings")
            return

        store.mark_status(entry_id, "processing")

        # Step 2: entry architect
        architect_result: Optional[EntryArchitectResult] = await asyncio.to_thread(
            run_entry_architect, transcription_result.text
        )
        store.record_entry_architect(entry_id, architect_result.model_dump() if architect_result else None)

        if architect_result is not None:
            source_fields = architect_result.source
            van_build_log_fields = architect_result.van_build_log
            clarification_message = architect_result.clarification_message or _ARCHITECT_FAILURE_MESSAGE
        else:
            source_fields = _fallback_source_fields(transcription_result.text)
            van_build_log_fields = None
            clarification_message = _ARCHITECT_FAILURE_MESSAGE

        # Step 3: create the Source record - always, regardless of whether
        # a build-log entry can also be made.
        captured_at = _captured_at(record)
        source_page = await asyncio.to_thread(
            notion_client.create_source_page,
            name=source_fields.name,
            cue=source_fields.cue,
            summary=source_fields.summary,
            observation=source_fields.observation,
            open_questions=source_fields.open_questions,
            capture_confidence=_confidence_tier(reliability.confidence),
            captured_at=captured_at,
        )
        store.record_source_page(entry_id, source_page)

        if van_build_log_fields is None:
            # Directive: preserve the Source, allow the build-log page id to
            # be absent, and notify - never pass an incomplete record into
            # the semantic verifier, which requires both page ids.
            await create_spoken_notification(entry_id, transcription_result.text, clarification_message)
            store.finalize_item(entry_id, status="completed_with_warnings")
            return

        # Step 4: create the Van Build Log record, linked back to the Source.
        van_build_log_page = await asyncio.to_thread(
            notion_client.create_van_build_log_page,
            name=van_build_log_fields.name,
            van_or_scope=van_build_log_fields.van_or_scope,
            entry_type=van_build_log_fields.entry_type,
            workstream=van_build_log_fields.workstream,
            allocation=van_build_log_fields.allocation,
            summary=van_build_log_fields.summary,
            source_page_id=source_page["id"],
            module_or_component=van_build_log_fields.module_or_component,
            amount=van_build_log_fields.amount,
            labor_hours=van_build_log_fields.labor_hours,
            documentation_value=van_build_log_fields.documentation_value,
            date=captured_at,
        )
        store.record_van_build_log_page(entry_id, van_build_log_page)

        # Step 5: independent semantic verification - only ever reached
        # once both records exist.
        verification = await asyncio.to_thread(
            run_semantic_verifier,
            transcription_result.text,
            source_fields.model_dump(),
            van_build_log_fields.model_dump(),
        )
        store.record_semantic_verification(entry_id, verification.model_dump())

        if verification.notification_required:
            await create_spoken_notification(entry_id, transcription_result.text, verification.spoken_question)
            store.finalize_item(entry_id, status="completed_with_warnings")
        else:
            store.finalize_item(entry_id, status="completed")
    except Exception as exc:  # noqa: BLE001 - persisted as the item's failure reason
        store.fail_item(entry_id, repr(exc))
