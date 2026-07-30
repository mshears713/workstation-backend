import logging
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Response, status

from app.api import runner, store
from app.api.notes_router import router as notes_router
from app.api.notifications_router import router as notifications_router
from app.api.voice_inbox_router import router as voice_inbox_router
from app.api.schemas import (
    HandshakeRequest,
    HandshakeResponse,
    RunAccepted,
    RunCreateRequest,
    RunResultResponse,
    RunStatusResponse,
)

log = logging.getLogger("earthside_handshake")

app = FastAPI(title="Operation Homebound Stage 2", version="0.1.0")
app.include_router(notes_router)
app.include_router(voice_inbox_router)
app.include_router(notifications_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/handshake", response_model=HandshakeResponse)
async def handshake(req: HandshakeRequest) -> HandshakeResponse:
    event_id = str(uuid.uuid4())
    server_time = datetime.now(timezone.utc).isoformat()
    n = len(req.accel_samples_g)

    if n:
        lo, hi = min(req.accel_samples_g), max(req.accel_samples_g)
        avg = sum(req.accel_samples_g) / n
        sample_summary = f"{n} samples {lo:.2f}g-{hi:.2f}g avg={avg:.2f}g"
    else:
        sample_summary = "0 samples"

    log.info(
        "handshake accepted: device_id=%s mission=%s seq=%s uptime_ms=%s %s -> event_id=%s",
        req.device_id, req.mission, req.sequence, req.device_uptime_ms, sample_summary, event_id,
    )

    return HandshakeResponse(
        accepted=True,
        event_id=event_id,
        server_time=server_time,
        message="Earthside link confirmed",
        accepted_sample_count=n,
    )


@app.post("/runs", status_code=status.HTTP_202_ACCEPTED, response_model=RunAccepted)
async def create_run(payload: RunCreateRequest, response: Response) -> RunAccepted:
    try:
        record, duplicate = await runner.submit_run(payload.request_id, payload.notes)
    except runner.RunInProgressError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if duplicate:
        response.status_code = status.HTTP_200_OK

    return RunAccepted(
        run_id=record["run_id"],
        request_id=record["request_id"],
        status=record["status"],
        created_at=record["created_at"],
        duplicate=duplicate,
    )


@app.get("/runs/{run_id}", response_model=RunStatusResponse)
async def get_run(run_id: str) -> RunStatusResponse:
    record = store.load_record(run_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return RunStatusResponse(**record)


@app.get("/runs/{run_id}/result", response_model=RunResultResponse)
async def get_run_result(run_id: str) -> RunResultResponse:
    record = store.load_record(run_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return RunResultResponse(**record)
