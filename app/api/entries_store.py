import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings


def _entries_dir() -> Path:
    path = get_settings().entries_data_dir_path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _item_dir(entry_id: str) -> Path:
    d = _entries_dir() / entry_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def item_dir(entry_id: str) -> Path:
    return _item_dir(entry_id)


def _record_path(entry_id: str) -> Path:
    return _item_dir(entry_id) / "record.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_iso() -> str:
    return _now()


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _write_record(entry_id: str, record: dict[str, Any]) -> None:
    _atomic_write(_record_path(entry_id), json.dumps(record, indent=2))


def load_record(entry_id: str) -> Optional[dict[str, Any]]:
    path = _record_path(entry_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_audio(entry_id: str, filename: Optional[str], content: bytes) -> dict[str, Any]:
    suffix = Path(filename).suffix if filename else ""
    if not suffix:
        suffix = ".wav"
    audio_path = _item_dir(entry_id) / f"audio{suffix}"
    audio_path.write_bytes(content)
    return {"stored_filename": audio_path.name, "size_bytes": len(content)}


def create_item(entry_id: str, request_id: str, source: str, audio_meta: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    record = {
        "entry_id": entry_id,
        "request_id": request_id,
        "source": source,
        "status": "accepted",
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "audio": audio_meta,
        "transcription": None,
        "audio_reliability": None,
        "entry_architect": None,
        "source_page": None,
        "van_build_log_page": None,
        "semantic_verification": None,
        "error": None,
    }
    _write_record(entry_id, record)
    return record


def mark_status(entry_id: str, status: str, **timestamp_fields: Any) -> None:
    record = load_record(entry_id)
    if record is None:
        return
    record["status"] = status
    record["updated_at"] = _now()
    record.update(timestamp_fields)
    _write_record(entry_id, record)


def record_transcription(entry_id: str, transcription: dict[str, Any]) -> None:
    record = load_record(entry_id)
    if record is None:
        return
    record["transcription"] = transcription
    record["updated_at"] = _now()
    _write_record(entry_id, record)


def record_audio_reliability(entry_id: str, reliability: dict[str, Any]) -> None:
    record = load_record(entry_id)
    if record is None:
        return
    record["audio_reliability"] = reliability
    record["updated_at"] = _now()
    _write_record(entry_id, record)


def record_entry_architect(entry_id: str, entry_architect: Optional[dict[str, Any]]) -> None:
    record = load_record(entry_id)
    if record is None:
        return
    record["entry_architect"] = entry_architect
    record["updated_at"] = _now()
    _write_record(entry_id, record)


def record_source_page(entry_id: str, source_page: dict[str, Any]) -> None:
    record = load_record(entry_id)
    if record is None:
        return
    record["source_page"] = source_page
    record["updated_at"] = _now()
    _write_record(entry_id, record)


def record_van_build_log_page(entry_id: str, van_build_log_page: dict[str, Any]) -> None:
    record = load_record(entry_id)
    if record is None:
        return
    record["van_build_log_page"] = van_build_log_page
    record["updated_at"] = _now()
    _write_record(entry_id, record)


def record_semantic_verification(entry_id: str, verification: dict[str, Any]) -> None:
    record = load_record(entry_id)
    if record is None:
        return
    record["semantic_verification"] = verification
    record["updated_at"] = _now()
    _write_record(entry_id, record)


def finalize_item(entry_id: str, status: str) -> None:
    """status is "completed" (fully silent) or "completed_with_warnings"
    (some notification was raised along the way) - same two-tier success
    status the notes pipeline already uses for FinalNoteResult."""
    record = load_record(entry_id)
    if record is None:
        return
    record["status"] = status
    record["completed_at"] = _now()
    record["updated_at"] = _now()
    _write_record(entry_id, record)


def fail_item(entry_id: str, error: str) -> None:
    record = load_record(entry_id)
    if record is None:
        return
    record["status"] = "failed"
    record["error"] = error
    record["completed_at"] = _now()
    record["updated_at"] = _now()
    _write_record(entry_id, record)
