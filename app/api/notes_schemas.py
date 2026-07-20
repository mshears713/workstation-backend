from typing import Any, Literal, Optional

from pydantic import BaseModel

NoteStatusLiteral = Literal["accepted", "transcribing", "processing", "completed", "failed"]


class NoteAccepted(BaseModel):
    note_id: str
    request_id: str
    status: NoteStatusLiteral
    created_at: str
    duplicate: bool = False
    status_url: str
    result_url: str


class NoteStatusResponse(BaseModel):
    note_id: str
    request_id: str
    source: str
    status: NoteStatusLiteral
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None


class NoteResultResponse(BaseModel):
    note_id: str
    request_id: str
    source: str
    status: NoteStatusLiteral
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    audio: dict[str, Any]
    transcription: Optional[dict[str, Any]] = None
    interpreted_note: Optional[dict[str, Any]] = None
    possible_actions: Optional[dict[str, Any]] = None
    grounding_report: Optional[dict[str, Any]] = None
    final: Optional[dict[str, Any]] = None
    model: Optional[dict[str, Any]] = None
    trace: Optional[dict[str, Any]] = None
    events: list[dict[str, Any]]
    warnings: list[str]
    error: Optional[str] = None
