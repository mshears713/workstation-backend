import asyncio
import uuid
from typing import Any, Optional

from app.api import store
from app.config import get_settings
from app.graph.graph import graph as graph  # re-exported; tests monkeypatch this name

_active_run_id: Optional[str] = None
_background_tasks: set[asyncio.Task] = set()


class RunInProgressError(Exception):
    def __init__(self, run_id: str):
        super().__init__(f"Another run ({run_id}) is already in progress")
        self.run_id = run_id


def _new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


async def submit_run(request_id: str, notes: Optional[str]) -> tuple[dict[str, Any], bool]:
    """Create (or find) a run for `request_id` and schedule its execution.

    Returns (record, duplicate). Raises RunInProgressError if a different run
    is currently active - this system only supports one active run at a time.
    """
    existing_run_id = store.find_run_id_by_request_id(request_id)
    if existing_run_id:
        existing = store.load_record(existing_run_id)
        if existing is not None:
            return existing, True

    global _active_run_id
    if _active_run_id is not None:
        active = store.load_record(_active_run_id)
        if active is not None and active["status"] in ("queued", "running"):
            raise RunInProgressError(_active_run_id)
        _active_run_id = None

    run_id = _new_run_id()
    record = store.create_run(run_id, request_id, notes)
    _active_run_id = run_id

    task = asyncio.create_task(_execute(run_id, notes))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return record, False


async def _execute(run_id: str, notes: Optional[str]) -> None:
    global _active_run_id
    store.mark_running(run_id)
    try:
        final_state, trace_meta = await asyncio.to_thread(_invoke_graph, run_id, notes)
        store.finalize_run(run_id, final_state, trace_meta)
    except Exception as exc:  # noqa: BLE001 - persisted as the run's failure reason
        store.fail_run(run_id, repr(exc))
    finally:
        if _active_run_id == run_id:
            _active_run_id = None


def _invoke_graph(run_id: str, notes: Optional[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    settings = get_settings()
    run_name = f"design-review-{run_id}"
    config = {
        "configurable": {"thread_id": run_id},
        "run_name": run_name,
        "tags": ["operation-homebound-stage2", "design-review"],
        "metadata": {"run_id": run_id},
    }
    result = graph.invoke({"run_id": run_id, "notes": notes}, config=config)
    trace_meta = {
        "model": settings.openrouter_model,
        "base_url": settings.openrouter_base_url,
        "langsmith_tracing_enabled": settings.langsmith_tracing,
        "langsmith_project": settings.langsmith_project,
        "run_name": run_name,
    }
    return result, trace_meta
