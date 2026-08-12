from typing import Literal, Optional

from pydantic import BaseModel

RemoteStatusLiteral = Literal["pending", "delivered"]

# Must match main/ir_roku.h's roku_key_t names (lowercased) on the firmware
# side - remote_client.c looks up this exact string to pick the NEC command
# byte to send.
ALLOWED_REMOTE_KEYS = (
    "power", "power_alt", "home", "back",
    "up", "down", "left", "right", "ok",
    "replay", "star", "rewind", "play_pause", "forward",
    "vol_up", "vol_down", "mute", "sleep",
)


class RemoteKeyResponse(BaseModel):
    command_id: str
    key: str
    status: Literal["queued"]


class RemotePendingResponse(BaseModel):
    """Deliberately minimal, same shape as NotificationPendingResponse - this
    is the fast-poll target remote_client.c hits every ~200ms, so it carries
    just enough to fire the IR command and know what to ack."""

    pending: bool
    count: int
    command_id: Optional[str] = None
    key: Optional[str] = None
    created_at: Optional[str] = None


class RemoteAckResponse(BaseModel):
    command_id: str
    status: RemoteStatusLiteral
    pending_remaining: int
