from fastapi import APIRouter, HTTPException, Response, status

from app.api import notifications_store as store
from app.api.notifications_schemas import (
    NotificationAckResponse,
    NotificationPendingResponse,
    NotificationStatusResponse,
)

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

# Raw PCM, not a WAV/audio/* MIME type - the ESP32 already knows the fixed
# sample-rate/bit-depth/channel triple out of band (see app/voice/tts.py),
# same "firmware knows the format, HTTP is just a byte pipe" contract the
# multipart audio uploads use in the other direction.
AUDIO_CONTENT_TYPE = "application/octet-stream"


@router.get("/pending", response_model=NotificationPendingResponse)
async def get_pending_notification() -> NotificationPendingResponse:
    """Cheap poll target for the ESP32 - no transcript/message body, just
    enough to drive the ring color and know which id to fetch next."""
    pending = store.list_pending()
    if not pending:
        return NotificationPendingResponse(pending=False, count=0)

    oldest = pending[0]
    return NotificationPendingResponse(
        pending=True,
        count=len(pending),
        notification_id=oldest["notification_id"],
        created_at=oldest["created_at"],
    )


@router.get("/{notification_id}", response_model=NotificationStatusResponse)
async def get_notification(notification_id: str) -> NotificationStatusResponse:
    record = store.load_record(notification_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="notification not found")
    return NotificationStatusResponse(**record)


@router.get("/{notification_id}/audio")
async def get_notification_audio(notification_id: str) -> Response:
    record = store.load_record(notification_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="notification not found")

    path = store.audio_path(notification_id)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="notification audio not found")

    return Response(content=path.read_bytes(), media_type=AUDIO_CONTENT_TYPE)


@router.post("/{notification_id}/ack", response_model=NotificationAckResponse)
async def ack_notification(notification_id: str) -> NotificationAckResponse:
    record = store.load_record(notification_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="notification not found")

    if record["status"] == "pending":
        store.mark_delivered(notification_id)

    remaining = len(store.list_pending())
    return NotificationAckResponse(notification_id=notification_id, status="delivered", pending_remaining=remaining)
