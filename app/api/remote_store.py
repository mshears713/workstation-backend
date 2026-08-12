import uuid
from datetime import datetime, timezone
from typing import Any, Optional

# In-memory only, unlike notifications_store's on-disk records: these are
# ephemeral keypresses, not something worth surviving a backend restart or
# auditing later. Safe as a plain list under a single-process asyncio
# event loop - every mutation below runs to completion without an `await`
# in the middle, so there's no interleaving to guard against.
_queue: list[dict[str, Any]] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue(key: str) -> dict[str, Any]:
    command = {
        "command_id": uuid.uuid4().hex,
        "key": key,
        "status": "pending",
        "created_at": _now(),
    }
    _queue.append(command)
    return command


def list_pending() -> list[dict[str, Any]]:
    """Oldest first (FIFO) - same ordering guarantee notifications_store
    gives, so /pending always points at the longest-waiting keypress."""
    return [c for c in _queue if c["status"] == "pending"]


def find(command_id: str) -> Optional[dict[str, Any]]:
    for command in _queue:
        if command["command_id"] == command_id:
            return command
    return None


def mark_delivered(command_id: str) -> None:
    """Removes the command outright rather than flagging it delivered and
    keeping it around - nothing ever reads a delivered command again, and
    dropping it keeps the queue from growing for the life of the process
    (see the module docstring on why this store skips disk persistence)."""
    global _queue
    _queue = [c for c in _queue if c["command_id"] != command_id]
