from typing import Literal, Optional

from pydantic import BaseModel

NotificationStatusLiteral = Literal["pending", "delivered"]


class NotificationPendingResponse(BaseModel):
    """Deliberately minimal - the ESP32 polls this frequently, so it carries
    just enough to drive the ring color and know which id to fetch/ack, not
    the transcript or spoken_message (see /{notification_id}/audio for the
    actual payload)."""

    pending: bool
    count: int
    notification_id: Optional[str] = None
    created_at: Optional[str] = None


class NotificationStatusResponse(BaseModel):
    notification_id: str
    source_voice_inbox_id: str
    status: NotificationStatusLiteral
    created_at: str
    delivered_at: Optional[str] = None
    transcript: str
    spoken_message: str
    confidence: Optional[float] = None


class NotificationAckResponse(BaseModel):
    notification_id: str
    status: NotificationStatusLiteral
    pending_remaining: int
