import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings


def _notes_dir() -> Path:
    path = get_settings().notes_data_dir_path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _note_dir(note_id: str) -> Path:
    d = _notes_dir() / note_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _record_path(note_id: str) -> Path:
    return _note_dir(note_id) / "record.json"


def _events_path(note_id: str) -> Path:
    return _note_dir(note_id) / "events.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_iso() -> str:
    return _now()


def note_dir(note_id: str) -> Path:
    return _note_dir(note_id)


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _write_record(note_id: str, record: dict[str, Any]) -> None:
    _atomic_write(_record_path(note_id), json.dumps(record, indent=2))


def load_record(note_id: str) -> Optional[dict[str, Any]]:
    path = _record_path(note_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_audio(note_id: str, filename: Optional[str], content: bytes) -> dict[str, Any]:
    """Writes the original uploaded audio into the note's directory and
    returns audio metadata (filename, size, content_type is added by caller)."""
    suffix = Path(filename).suffix if filename else ""
    if not suffix:
        suffix = ".wav"
    audio_path = _note_dir(note_id) / f"audio{suffix}"
    audio_path.write_bytes(content)
    return {"stored_filename": audio_path.name, "size_bytes": len(content)}


def create_note(
    note_id: str,
    request_id: str,
    source: str,
    audio_meta: dict[str, Any],
) -> dict[str, Any]:
    now = _now()
    record = {
        "note_id": note_id,
        "request_id": request_id,
        "source": source,
        "status": "accepted",
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "audio": audio_meta,
        "transcription": None,
        "interpreted_note": None,
        "possible_actions": None,
        "grounding_report": None,
        "final": None,
        "model": None,
        "trace": None,
        "events": [],
        "warnings": [],
        "error": None,
    }
    _write_record(note_id, record)
    return record


def mark_status(note_id: str, status: str, **timestamp_fields: Any) -> None:
    record = load_record(note_id)
    if record is None:
        return
    record["status"] = status
    record["updated_at"] = _now()
    record.update(timestamp_fields)
    _write_record(note_id, record)


def record_transcription(note_id: str, transcription: dict[str, Any]) -> None:
    record = load_record(note_id)
    if record is None:
        return
    record["transcription"] = transcription
    record["updated_at"] = _now()
    _write_record(note_id, record)


def _append_events_jsonl(note_id: str, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    with _events_path(note_id).open("a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def finalize_note(note_id: str, final_state: dict[str, Any], model_meta: dict[str, Any], trace_meta: dict[str, Any]) -> None:
    record = load_record(note_id)
    if record is None:
        return
    events = final_state.get("events", [])
    record["events"].extend(events)
    _append_events_jsonl(note_id, events)
    record["interpreted_note"] = final_state.get("interpreted_note")
    record["possible_actions"] = final_state.get("possible_actions")
    record["grounding_report"] = final_state.get("grounding_report")
    record["final"] = final_state.get("final")
    record["warnings"] = list(record.get("warnings", [])) + list(final_state.get("warnings", []))
    record["model"] = model_meta
    record["trace"] = trace_meta

    final = final_state.get("final") or {}
    record["status"] = "failed" if final.get("status") == "failed" else "completed"
    record["completed_at"] = _now()
    record["updated_at"] = _now()
    _write_record(note_id, record)


def fail_note(note_id: str, error: str) -> None:
    record = load_record(note_id)
    if record is None:
        return
    record["status"] = "failed"
    record["error"] = error
    record["completed_at"] = _now()
    record["updated_at"] = _now()
    _write_record(note_id, record)
