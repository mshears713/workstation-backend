import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings


def _voice_inbox_dir() -> Path:
    path = get_settings().voice_inbox_data_dir_path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _item_dir(voice_inbox_id: str) -> Path:
    d = _voice_inbox_dir() / voice_inbox_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _record_path(voice_inbox_id: str) -> Path:
    return _item_dir(voice_inbox_id) / "record.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_iso() -> str:
    return _now()


def item_dir(voice_inbox_id: str) -> Path:
    return _item_dir(voice_inbox_id)


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _write_record(voice_inbox_id: str, record: dict[str, Any]) -> None:
    _atomic_write(_record_path(voice_inbox_id), json.dumps(record, indent=2))


def load_record(voice_inbox_id: str) -> Optional[dict[str, Any]]:
    path = _record_path(voice_inbox_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_audio(voice_inbox_id: str, filename: Optional[str], content: bytes) -> dict[str, Any]:
    """Writes the original uploaded audio into the item's directory and
    returns audio metadata (filename, size, content_type is added by caller)."""
    suffix = Path(filename).suffix if filename else ""
    if not suffix:
        suffix = ".wav"
    audio_path = _item_dir(voice_inbox_id) / f"audio{suffix}"
    audio_path.write_bytes(content)
    return {"stored_filename": audio_path.name, "size_bytes": len(content)}


def create_item(
    voice_inbox_id: str,
    request_id: str,
    source: str,
    audio_meta: dict[str, Any],
    project_hint: Optional[str] = None,
) -> dict[str, Any]:
    now = _now()
    record = {
        "voice_inbox_id": voice_inbox_id,
        "request_id": request_id,
        "source": source,
        # Routing/context metadata chosen on the device (main/project_selector.h)
        # and carried through unchanged. The Voice Inbox stays authoritative
        # downstream - this only records what the operator had selected.
        "project_hint": project_hint,
        "status": "accepted",
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "audio": audio_meta,
        "transcription": None,
        "notion": None,
        "error": None,
    }
    _write_record(voice_inbox_id, record)
    return record


def mark_status(voice_inbox_id: str, status: str, **timestamp_fields: Any) -> None:
    record = load_record(voice_inbox_id)
    if record is None:
        return
    record["status"] = status
    record["updated_at"] = _now()
    record.update(timestamp_fields)
    _write_record(voice_inbox_id, record)


def record_transcription(voice_inbox_id: str, transcription: dict[str, Any]) -> None:
    record = load_record(voice_inbox_id)
    if record is None:
        return
    record["transcription"] = transcription
    record["updated_at"] = _now()
    _write_record(voice_inbox_id, record)


def finalize_item(voice_inbox_id: str, notion: dict[str, Any]) -> None:
    record = load_record(voice_inbox_id)
    if record is None:
        return
    record["notion"] = notion
    record["status"] = "completed"
    record["completed_at"] = _now()
    record["updated_at"] = _now()
    _write_record(voice_inbox_id, record)


def fail_item(voice_inbox_id: str, error: str) -> None:
    record = load_record(voice_inbox_id)
    if record is None:
        return
    record["status"] = "failed"
    record["error"] = error
    record["completed_at"] = _now()
    record["updated_at"] = _now()
    _write_record(voice_inbox_id, record)
