from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status

from app.api import streaming_capture
from app.api import voice_inbox_runner as runner
from app.api import voice_inbox_store as store
from app.api.voice_inbox_schemas import VoiceInboxAccepted, VoiceInboxResultResponse, VoiceInboxStatusResponse

router = APIRouter(prefix="/api/v1/voice-inbox", tags=["voice-inbox"])

_STREAMING_KIND = "voice_inbox"


def _status_url(voice_inbox_id: str) -> str:
    return f"/api/v1/voice-inbox/{voice_inbox_id}"


def _result_url(voice_inbox_id: str) -> str:
    return f"/api/v1/voice-inbox/{voice_inbox_id}/result"


async def _accept_voice_inbox_item(
    response: Response,
    request_id: str,
    source: str,
    filename: Optional[str],
    content_type: Optional[str],
    audio_bytes: bytes,
    duration_seconds: Optional[float],
    sample_rate_hz: Optional[int],
    project_hint: Optional[str],
) -> VoiceInboxAccepted:
    """Shared by create_voice_inbox_item() (single-shot upload) and
    finish_voice_inbox_item() (streaming upload's final call)."""
    try:
        record, duplicate = await runner.submit_voice_inbox_item(
            request_id=request_id,
            source=source,
            filename=filename,
            content_type=content_type,
            audio_bytes=audio_bytes,
            duration_seconds=duration_seconds,
            sample_rate_hz=sample_rate_hz,
            project_hint=project_hint,
        )
    except runner.VoiceInboxValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    if duplicate:
        response.status_code = status.HTTP_200_OK

    return VoiceInboxAccepted(
        voice_inbox_id=record["voice_inbox_id"],
        request_id=record["request_id"],
        status=record["status"],
        created_at=record["created_at"],
        duplicate=duplicate,
        status_url=_status_url(record["voice_inbox_id"]),
        result_url=_result_url(record["voice_inbox_id"]),
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=VoiceInboxAccepted)
async def create_voice_inbox_item(
    response: Response,
    request_id: str = Form(...),
    source: str = Form(...),
    audio: UploadFile = File(...),
    duration_seconds: Optional[float] = Form(None),
    sample_rate_hz: Optional[int] = Form(None),
    project_hint: Optional[str] = Form(None),
) -> VoiceInboxAccepted:
    audio_bytes = await audio.read()
    return await _accept_voice_inbox_item(
        response, request_id, source, audio.filename, audio.content_type,
        audio_bytes, duration_seconds, sample_rate_hz, project_hint,
    )


@router.post("/{request_id}/chunk", status_code=status.HTTP_202_ACCEPTED)
async def upload_voice_inbox_chunk(request_id: str, request: Request, offset: int | None = None) -> dict:
    """See entries_router.py's upload_entry_chunk() - identical shape,
    different kind.

    `offset` is the byte position this chunk starts at, used by
    streaming_capture.append_chunk() as the ordering guard - see its
    docstring. Optional so firmware predating the guard still works."""
    data = await request.body()
    try:
        total, duplicate = streaming_capture.append_chunk(
            _STREAMING_KIND, request_id, data, offset=offset
        )
    except runner.VoiceInboxValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except streaming_capture.ChunkOrderError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"received_bytes": len(data), "total_bytes": total, "duplicate": duplicate}


@router.post("/{request_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_voice_inbox_chunk(request_id: str) -> dict:
    """See entries_router.py's cancel_entry_chunk() - identical shape,
    different kind."""
    try:
        streaming_capture.discard_chunks(_STREAMING_KIND, request_id)
    except runner.VoiceInboxValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"request_id": request_id, "status": "cancelled"}


@router.post("/finish", status_code=status.HTTP_202_ACCEPTED, response_model=VoiceInboxAccepted)
async def finish_voice_inbox_item(
    response: Response,
    request_id: str = Form(...),
    source: str = Form(...),
    sample_rate_hz: int = Form(...),
    bits_per_sample: int = Form(16),
    channels: int = Form(1),
    project_hint: Optional[str] = Form(None),
) -> VoiceInboxAccepted:
    try:
        audio_bytes = streaming_capture.finalize_wav(_STREAMING_KIND, request_id, sample_rate_hz, bits_per_sample, channels)
    except runner.VoiceInboxValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    block_align = channels * (bits_per_sample // 8)
    duration_seconds = (
        (len(audio_bytes) - 44) / (sample_rate_hz * block_align)
        if block_align and sample_rate_hz else None
    )

    return await _accept_voice_inbox_item(
        response, request_id, source, f"{request_id}.wav", "audio/wav",
        audio_bytes, duration_seconds, sample_rate_hz, project_hint,
    )


@router.get("/{voice_inbox_id}", response_model=VoiceInboxStatusResponse)
async def get_voice_inbox_status(voice_inbox_id: str) -> VoiceInboxStatusResponse:
    record = store.load_record(voice_inbox_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="voice inbox item not found")
    return VoiceInboxStatusResponse(**record)


@router.get("/{voice_inbox_id}/result")
async def get_voice_inbox_result(voice_inbox_id: str):
    record = store.load_record(voice_inbox_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="voice inbox item not found")

    if record["status"] == "completed":
        return VoiceInboxResultResponse(**record)

    if record["status"] == "failed":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"voice_inbox_id": voice_inbox_id, "status": "failed", "error": record.get("error")},
        )

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "voice_inbox_id": voice_inbox_id,
            "status": record["status"],
            "detail": "voice inbox item processing is not yet complete",
        },
    )
