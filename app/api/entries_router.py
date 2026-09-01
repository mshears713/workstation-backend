from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status

from app.api import entries_runner as runner
from app.api import entries_store as store
from app.api import streaming_capture
from app.api.entries_schemas import EntryAccepted, EntryResultResponse, EntryStatusResponse

router = APIRouter(prefix="/api/v1/entries", tags=["entries"])

_STREAMING_KIND = "entries"


def _status_url(entry_id: str) -> str:
    return f"/api/v1/entries/{entry_id}"


def _result_url(entry_id: str) -> str:
    return f"/api/v1/entries/{entry_id}/result"


async def _accept_entry(
    response: Response,
    request_id: str,
    source: str,
    filename: Optional[str],
    content_type: Optional[str],
    audio_bytes: bytes,
    duration_seconds: Optional[float],
    sample_rate_hz: Optional[int],
    project_hint: Optional[str],
) -> EntryAccepted:
    """Shared by create_entry() (single-shot upload) and finish_entry()
    (streaming upload's final call) - identical from here on regardless of
    how the audio bytes were assembled."""
    try:
        record, duplicate = await runner.submit_entry(
            request_id=request_id,
            source=source,
            filename=filename,
            content_type=content_type,
            audio_bytes=audio_bytes,
            duration_seconds=duration_seconds,
            sample_rate_hz=sample_rate_hz,
            project_hint=project_hint,
        )
    except runner.EntryValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    if duplicate:
        response.status_code = status.HTTP_200_OK

    return EntryAccepted(
        entry_id=record["entry_id"],
        request_id=record["request_id"],
        status=record["status"],
        created_at=record["created_at"],
        duplicate=duplicate,
        status_url=_status_url(record["entry_id"]),
        result_url=_result_url(record["entry_id"]),
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=EntryAccepted)
async def create_entry(
    response: Response,
    request_id: str = Form(...),
    source: str = Form(...),
    audio: UploadFile = File(...),
    duration_seconds: Optional[float] = Form(None),
    sample_rate_hz: Optional[int] = Form(None),
    project_hint: Optional[str] = Form(None),
) -> EntryAccepted:
    audio_bytes = await audio.read()
    return await _accept_entry(
        response, request_id, source, audio.filename, audio.content_type,
        audio_bytes, duration_seconds, sample_rate_hz, project_hint,
    )


@router.post("/{request_id}/chunk", status_code=status.HTTP_202_ACCEPTED)
async def upload_entry_chunk(request_id: str, request: Request) -> dict:
    """Raw PCM bytes as the whole request body - see
    main/stream_upload.c's chunk POST, one call per ~15s of recording
    (main/audio_capture.c). No JSON wrapper: the body IS the audio, same
    "don't re-encode bytes that are already binary" reasoning the old
    Mission 10 /api/v1/audio endpoint used."""
    data = await request.body()
    total = streaming_capture.append_chunk(_STREAMING_KIND, request_id, data)
    return {"received_bytes": len(data), "total_bytes": total}


@router.post("/{request_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_entry_chunk(request_id: str) -> dict:
    """CANCEL button's counterpart to /finish - discards whatever chunks
    already arrived for request_id instead of assembling and processing
    them. See main/audio_capture.c's cancel path (audio_capture_cancel())."""
    streaming_capture.discard_chunks(_STREAMING_KIND, request_id)
    return {"request_id": request_id, "status": "cancelled"}


@router.post("/finish", status_code=status.HTTP_202_ACCEPTED, response_model=EntryAccepted)
async def finish_entry(
    response: Response,
    request_id: str = Form(...),
    source: str = Form(...),
    sample_rate_hz: int = Form(...),
    bits_per_sample: int = Form(16),
    channels: int = Form(1),
    project_hint: Optional[str] = Form(None),
) -> EntryAccepted:
    """Tells the backend no more chunks are coming for request_id - wraps
    the accumulated PCM in one real WAV (streaming_capture.finalize_wav())
    and hands off to the exact same runner.submit_entry() path create_entry()
    uses, so everything past this point (transcription, entry architect,
    Notion, semantic verification) is identical to the single-shot flow."""
    audio_bytes = streaming_capture.finalize_wav(_STREAMING_KIND, request_id, sample_rate_hz, bits_per_sample, channels)

    block_align = channels * (bits_per_sample // 8)
    duration_seconds = (
        (len(audio_bytes) - 44) / (sample_rate_hz * block_align)
        if block_align and sample_rate_hz else None
    )

    return await _accept_entry(
        response, request_id, source, f"{request_id}.wav", "audio/wav",
        audio_bytes, duration_seconds, sample_rate_hz, project_hint,
    )


@router.get("/{entry_id}", response_model=EntryStatusResponse)
async def get_entry_status(entry_id: str) -> EntryStatusResponse:
    record = store.load_record(entry_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entry not found")
    return EntryStatusResponse(**record)


@router.get("/{entry_id}/result")
async def get_entry_result(entry_id: str):
    record = store.load_record(entry_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entry not found")

    if record["status"] in ("completed", "completed_with_warnings"):
        return EntryResultResponse(**record)

    if record["status"] == "failed":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"entry_id": entry_id, "status": "failed", "error": record.get("error")},
        )

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"entry_id": entry_id, "status": record["status"], "detail": "entry processing is not yet complete"},
    )
