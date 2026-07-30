from typing import Any, Literal, Optional

from pydantic import BaseModel

EntryStatusLiteral = Literal["accepted", "transcribing", "processing", "completed", "completed_with_warnings", "failed"]


class EntryAccepted(BaseModel):
    entry_id: str
    request_id: str
    status: EntryStatusLiteral
    created_at: str
    duplicate: bool = False
    status_url: str
    result_url: str


class EntryStatusResponse(BaseModel):
    entry_id: str
    request_id: str
    source: str
    status: EntryStatusLiteral
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None


class EntryResultResponse(BaseModel):
    entry_id: str
    request_id: str
    source: str
    status: EntryStatusLiteral
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    audio: dict[str, Any]
    transcription: Optional[dict[str, Any]] = None
    audio_reliability: Optional[dict[str, Any]] = None
    entry_architect: Optional[dict[str, Any]] = None
    source_page: Optional[dict[str, Any]] = None
    van_build_log_page: Optional[dict[str, Any]] = None
    semantic_verification: Optional[dict[str, Any]] = None
    error: Optional[str] = None
