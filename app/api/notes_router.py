from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status

from app.api import notes_runner as runner
from app.api import notes_store as store
from app.api.notes_schemas import NoteAccepted, NoteResultResponse, NoteStatusResponse

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])


def _status_url(note_id: str) -> str:
    return f"/api/v1/notes/{note_id}"


def _result_url(note_id: str) -> str:
    return f"/api/v1/notes/{note_id}/result"


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

    try:
        record, duplicate = await runner.submit_note(
            request_id=request_id,
            source=source,
            filename=audio.filename,
            content_type=audio.content_type,
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
