from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status

from app.api import voice_inbox_runner as runner
from app.api import voice_inbox_store as store
from app.api.voice_inbox_schemas import VoiceInboxAccepted, VoiceInboxResultResponse, VoiceInboxStatusResponse

router = APIRouter(prefix="/api/v1/voice-inbox", tags=["voice-inbox"])


def _status_url(voice_inbox_id: str) -> str:
    return f"/api/v1/voice-inbox/{voice_inbox_id}"


def _result_url(voice_inbox_id: str) -> str:
    return f"/api/v1/voice-inbox/{voice_inbox_id}/result"


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=VoiceInboxAccepted)
async def create_voice_inbox_item(
    response: Response,
    request_id: str = Form(...),
    source: str = Form(...),
    audio: UploadFile = File(...),
    duration_seconds: Optional[float] = Form(None),
    sample_rate_hz: Optional[int] = Form(None),
) -> VoiceInboxAccepted:
    audio_bytes = await audio.read()

    try:
        record, duplicate = await runner.submit_voice_inbox_item(
            request_id=request_id,
            source=source,
            filename=audio.filename,
            content_type=audio.content_type,
            audio_bytes=audio_bytes,
            duration_seconds=duration_seconds,
            sample_rate_hz=sample_rate_hz,
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
