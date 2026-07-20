from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

RunStatusLiteral = Literal["queued", "running", "completed", "failed"]


class RunCreateRequest(BaseModel):
    request_id: str = Field(..., min_length=1, max_length=200)
    notes: Optional[str] = None


class RunAccepted(BaseModel):
    run_id: str
    request_id: str
    status: RunStatusLiteral
    created_at: str
    duplicate: bool = False


class RunStatusResponse(BaseModel):
    run_id: str
    request_id: str
    status: RunStatusLiteral
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None


class RunResultResponse(BaseModel):
    run_id: str
    request_id: str
    status: RunStatusLiteral
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    input: dict[str, Any]
    model: Optional[dict[str, Any]] = None
    trace: Optional[dict[str, Any]] = None
    reports: dict[str, Any]
    final: Optional[dict[str, Any]] = None
    events: list[dict[str, Any]]
    error: Optional[str] = None
