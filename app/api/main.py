from fastapi import FastAPI, HTTPException, Response, status

from app.api import runner, store
from app.api.notes_router import router as notes_router
from app.api.schemas import RunAccepted, RunCreateRequest, RunResultResponse, RunStatusResponse

app = FastAPI(title="Operation Homebound Stage 2", version="0.1.0")
app.include_router(notes_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


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
