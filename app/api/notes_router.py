from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status

from app.api import notes_runner as runner
from app.api import notes_store as store
from app.api import streaming_capture
from app.api.notes_schemas import NoteAccepted, NoteResultResponse, NoteStatusResponse

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])

_STREAMING_KIND = "notes"


def _status_url(note_id: str) -> str:
    return f"/api/v1/notes/{note_id}"


def _result_url(note_id: str) -> str:
    return f"/api/v1/notes/{note_id}/result"


async def _accept_note(
    response: Response,
    request_id: str,
    source: str,
    filename: Optional[str],
    content_type: Optional[str],
    audio_bytes: bytes,
    duration_seconds: Optional[float],
    sample_rate_hz: Optional[int],
) -> NoteAccepted:
    """Shared by create_note() (single-shot upload) and finish_note()
    (streaming upload's final call)."""
    try:
        record, duplicate = await runner.submit_note(
            request_id=request_id,
            source=source,
            filename=filename,
            content_type=content_type,
            audio_bytes=audio_bytes,
            duration_seconds=duration_seconds,
            sample_rate_hz=sample_rate_hz,
        )
    except runner.NoteValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    if duplicate:
        response.status_code = status.HTTP_200_OK

    return NoteAccepted(
        note_id=record["note_id"],
        request_id=record["request_id"],
        status=record["status"],
        created_at=record["created_at"],
        duplicate=duplicate,
        status_url=_status_url(record["note_id"]),
        result_url=_result_url(record["note_id"]),
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=NoteAccepted)
async def create_note(
    response: Response,
    request_id: str = Form(...),
    source: str = Form(...),
    audio: UploadFile = File(...),
    duration_seconds: Optional[float] = Form(None),
    sample_rate_hz: Optional[int] = Form(None),
) -> NoteAccepted:
    audio_bytes = await audio.read()
    return await _accept_note(
        response, request_id, source, audio.filename, audio.content_type,
        audio_bytes, duration_seconds, sample_rate_hz,
    )


@router.post("/{request_id}/chunk", status_code=status.HTTP_202_ACCEPTED)
async def upload_note_chunk(request_id: str, request: Request) -> dict:
    """See entries_router.py's upload_entry_chunk() - identical shape,
    different kind."""
    data = await request.body()
    total = streaming_capture.append_chunk(_STREAMING_KIND, request_id, data)
    return {"received_bytes": len(data), "total_bytes": total}


@router.post("/{request_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_note_chunk(request_id: str) -> dict:
    """See entries_router.py's cancel_entry_chunk() - identical shape,
    different kind."""
    streaming_capture.discard_chunks(_STREAMING_KIND, request_id)
    return {"request_id": request_id, "status": "cancelled"}


@router.post("/finish", status_code=status.HTTP_202_ACCEPTED, response_model=NoteAccepted)
async def finish_note(
    response: Response,
    request_id: str = Form(...),
    source: str = Form(...),
    sample_rate_hz: int = Form(...),
    bits_per_sample: int = Form(16),
    channels: int = Form(1),
) -> NoteAccepted:
    audio_bytes = streaming_capture.finalize_wav(_STREAMING_KIND, request_id, sample_rate_hz, bits_per_sample, channels)

    block_align = channels * (bits_per_sample // 8)
    duration_seconds = (
        (len(audio_bytes) - 44) / (sample_rate_hz * block_align)
        if block_align and sample_rate_hz else None
    )

    return await _accept_note(
        response, request_id, source, f"{request_id}.wav", "audio/wav",
        audio_bytes, duration_seconds, sample_rate_hz,
    )


@router.get("/{note_id}", response_model=NoteStatusResponse)
async def get_note_status(note_id: str) -> NoteStatusResponse:
    record = store.load_record(note_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="note not found")
    return NoteStatusResponse(**record)


@router.get("/{note_id}/result")
async def get_note_result(note_id: str):
    record = store.load_record(note_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="note not found")

    if record["status"] == "completed":
        return NoteResultResponse(**record)

    if record["status"] == "failed":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"note_id": note_id, "status": "failed", "error": record.get("error")},
        )

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"note_id": note_id, "status": record["status"], "detail": "note processing is not yet complete"},
    )
