import asyncio
from typing import Any, Optional

from app.api import notes_store as store
from app.api.upload_validation import UploadValidationError as NoteValidationError
from app.api.upload_validation import validate_upload
from app.config import get_settings
from app.voice import transcription
from app.voice.graph import graph as graph  # re-exported; tests monkeypatch this name

_background_tasks: set[asyncio.Task] = set()


async def submit_note(
    request_id: str,
    source: str,
    filename: Optional[str],
    content_type: Optional[str],
    audio_bytes: bytes,
    duration_seconds: Optional[float],
    sample_rate_hz: Optional[int],
) -> tuple[dict[str, Any], bool]:
    """Validate, persist the audio, create the note record, and schedule
    background processing. Returns (record, duplicate).

    The ESP32's request_id becomes the note_id directly, so it doubles as
    both the idempotency key and the resource identifier used in
    /api/v1/notes/{note_id} URLs.
    """
    validate_upload(request_id, source, audio_bytes, duration_seconds, sample_rate_hz)

    note_id = request_id

    existing = store.load_record(note_id)
    if existing is not None:
        return existing, True

    audio_meta = store.save_audio(note_id, filename, audio_bytes)
    audio_meta.update(
        {
            "original_filename": filename,
            "content_type": content_type,
            "duration_seconds": duration_seconds,
            "sample_rate_hz": sample_rate_hz,
        }
    )

    record = store.create_note(note_id, request_id, source.strip(), audio_meta)

    task = asyncio.create_task(_execute(note_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return record, False


async def _execute(note_id: str) -> None:
    try:
        store.mark_status(note_id, "transcribing", started_at=store.now_iso())
        record = store.load_record(note_id)
        stored_filename = record["audio"]["stored_filename"]
        audio_path = store.note_dir(note_id) / stored_filename

        transcription_result = await asyncio.to_thread(transcription.transcribe_audio, audio_path)
        store.record_transcription(note_id, transcription_result.model_dump())

        store.mark_status(note_id, "processing")
        final_state, model_meta, trace_meta = await asyncio.to_thread(
            _invoke_graph, note_id, transcription_result.text
        )
        store.finalize_note(note_id, final_state, model_meta, trace_meta)
    except Exception as exc:  # noqa: BLE001 - persisted as the note's failure reason
        store.fail_note(note_id, repr(exc))


def _invoke_graph(note_id: str, transcript: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    settings = get_settings()
    run_name = f"voice-note-{note_id}"
    config = {
        "configurable": {"thread_id": note_id},
        "run_name": run_name,
        "tags": ["operation-homebound-stage2", "voice-note"],
        "metadata": {"note_id": note_id},
    }
    result = graph.invoke({"note_id": note_id, "transcript": transcript}, config=config)
    model_meta = {
        "transcription_model": settings.openai_transcription_model,
        "graph_model": settings.openrouter_model,
        "graph_base_url": settings.openrouter_base_url,
    }
    trace_meta = {
        "langsmith_tracing_enabled": settings.langsmith_tracing,
        "langsmith_project": settings.langsmith_project,
        "run_name": run_name,
    }
    return result, model_meta, trace_meta
