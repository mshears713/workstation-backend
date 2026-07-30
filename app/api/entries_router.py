from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status

from app.api import entries_runner as runner
from app.api import entries_store as store
from app.api.entries_schemas import EntryAccepted, EntryResultResponse, EntryStatusResponse

router = APIRouter(prefix="/api/v1/entries", tags=["entries"])


def _status_url(entry_id: str) -> str:
    return f"/api/v1/entries/{entry_id}"


def _result_url(entry_id: str) -> str:
    return f"/api/v1/entries/{entry_id}/result"


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=EntryAccepted)
async def create_entry(
    response: Response,
    request_id: str = Form(...),
    source: str = Form(...),
    audio: UploadFile = File(...),
    duration_seconds: Optional[float] = Form(None),
    sample_rate_hz: Optional[int] = Form(None),
) -> EntryAccepted:
    audio_bytes = await audio.read()

    try:
        record, duplicate = await runner.submit_entry(
            request_id=request_id,
            source=source,
            filename=audio.filename,
            content_type=audio.content_type,
            audio_bytes=audio_bytes,
            duration_seconds=duration_seconds,
            sample_rate_hz=sample_rate_hz,
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
