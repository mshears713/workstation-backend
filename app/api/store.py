import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings

_index_lock = threading.Lock()


def _runs_dir() -> Path:
    path = get_settings().data_dir_path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_dir(run_id: str) -> Path:
    d = _runs_dir() / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _record_path(run_id: str) -> Path:
    return _run_dir(run_id) / "record.json"


def _events_path(run_id: str) -> Path:
    return _run_dir(run_id) / "events.jsonl"


def _index_path() -> Path:
    return _runs_dir() / "index.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _read_index() -> dict[str, str]:
    path = _index_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_index(index: dict[str, str]) -> None:
    _atomic_write(_index_path(), json.dumps(index, indent=2))


def find_run_id_by_request_id(request_id: str) -> Optional[str]:
    with _index_lock:
        return _read_index().get(request_id)


def _write_record(run_id: str, record: dict[str, Any]) -> None:
    _atomic_write(_record_path(run_id), json.dumps(record, indent=2))


def load_record(run_id: str) -> Optional[dict[str, Any]]:
    path = _record_path(run_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def create_run(run_id: str, request_id: str, notes: Optional[str]) -> dict[str, Any]:
    now = _now()
    record = {
        "run_id": run_id,
        "request_id": request_id,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "input": {"notes": notes},
        "model": None,
        "trace": None,
        "reports": {"embedded": None, "backend": None, "langgraph": None, "audit": None},
        "final": None,
        "events": [],
        "error": None,
    }
    _write_record(run_id, record)
    with _index_lock:
        index = _read_index()
        index[request_id] = run_id
        _write_index(index)
    return record


def mark_running(run_id: str) -> None:
    record = load_record(run_id)
    if record is None:
        return
    record["status"] = "running"
    record["started_at"] = _now()
    record["updated_at"] = _now()
    _write_record(run_id, record)


def _append_events_jsonl(run_id: str, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    with _events_path(run_id).open("a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def finalize_run(run_id: str, final_state: dict[str, Any], trace_meta: dict[str, Any]) -> None:
    record = load_record(run_id)
    if record is None:
        return
    events = final_state.get("events", [])
    record["events"].extend(events)
    _append_events_jsonl(run_id, events)
    record["reports"] = {
        "embedded": final_state.get("embedded_report"),
        "backend": final_state.get("backend_report"),
        "langgraph": final_state.get("langgraph_report"),
        "audit": final_state.get("audit_report"),
    }
    record["final"] = final_state.get("final")
    record["model"] = {
        "name": trace_meta.get("model"),
        "base_url": trace_meta.get("base_url"),
    }
    record["trace"] = {
        "langsmith_tracing_enabled": trace_meta.get("langsmith_tracing_enabled"),
        "langsmith_project": trace_meta.get("langsmith_project"),
        "run_name": trace_meta.get("run_name"),
    }
    record["status"] = "completed"
    record["completed_at"] = _now()
    record["updated_at"] = _now()
    _write_record(run_id, record)


def fail_run(run_id: str, error: str) -> None:
    record = load_record(run_id)
    if record is None:
        return
    record["status"] = "failed"
    record["error"] = error
    record["completed_at"] = _now()
    record["updated_at"] = _now()
    _write_record(run_id, record)
