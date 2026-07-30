from typing import Any, Literal, Optional

from pydantic import BaseModel

VoiceInboxStatusLiteral = Literal["accepted", "transcribing", "sending_to_notion", "completed", "failed"]


class VoiceInboxAccepted(BaseModel):
    voice_inbox_id: str
    request_id: str
    status: VoiceInboxStatusLiteral
    created_at: str
    duplicate: bool = False
    status_url: str
    result_url: str


class VoiceInboxStatusResponse(BaseModel):
    voice_inbox_id: str
    request_id: str
    source: str
    status: VoiceInboxStatusLiteral
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None


class VoiceInboxResultResponse(BaseModel):
    voice_inbox_id: str
    request_id: str
    source: str
    status: VoiceInboxStatusLiteral
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    audio: dict[str, Any]
    transcription: Optional[dict[str, Any]] = None
    notion: Optional[dict[str, Any]] = None
    error: Optional[str] = None
