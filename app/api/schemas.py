from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field

RunStatusLiteral = Literal["queued", "running", "completed", "failed"]


class HandshakeRequest(BaseModel):
    device_id: str
    event_type: str
    mission: str
    device_uptime_ms: int
    sequence: Optional[int] = None
    sample_interval_ms: Optional[int] = None
    # Up to the device's last 10 accelerometer readings (magnitude, in g).
    # May be fewer than 10 (e.g. shortly after boot) or empty (no IMU) -
    # the device never pads this with fabricated values, so don't assume
    # exactly 10 here either.
    accel_samples_g: List[float] = Field(default_factory=list, max_length=10)


class HandshakeResponse(BaseModel):
    accepted: bool
    event_id: str
    server_time: str
    message: str
    accepted_sample_count: int


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
