from fastapi import APIRouter, HTTPException, status

from app.api import remote_store as store
from app.api.remote_schemas import (
    ALLOWED_REMOTE_KEYS,
    RemoteAckResponse,
    RemoteKeyResponse,
    RemotePendingResponse,
)

router = APIRouter(prefix="/api/v1/remote", tags=["remote"])


@router.post("/keys/{key}", status_code=status.HTTP_202_ACCEPTED, response_model=RemoteKeyResponse)
async def press_key(key: str) -> RemoteKeyResponse:
    if key not in ALLOWED_REMOTE_KEYS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown key: {key}")

    command = store.enqueue(key)
    return RemoteKeyResponse(command_id=command["command_id"], key=key, status="queued")


@router.get("/pending", response_model=RemotePendingResponse)
async def get_pending_command() -> RemotePendingResponse:
    """Fast-poll target for the ESP32 - remote_client.c hits this every
    ~200ms so a keypress round-trips to an IR blast without the 5s lag the
    notifications poll tolerates."""
    pending = store.list_pending()
    if not pending:
        return RemotePendingResponse(pending=False, count=0)

    oldest = pending[0]
    return RemotePendingResponse(
        pending=True,
        count=len(pending),
        command_id=oldest["command_id"],
        key=oldest["key"],
        created_at=oldest["created_at"],
    )


@router.post("/{command_id}/ack", response_model=RemoteAckResponse)
async def ack_command(command_id: str) -> RemoteAckResponse:
    command = store.find(command_id)
    if command is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="command not found")

    store.mark_delivered(command_id)
    remaining = len(store.list_pending())
    return RemoteAckResponse(command_id=command_id, status="delivered", pending_remaining=remaining)
