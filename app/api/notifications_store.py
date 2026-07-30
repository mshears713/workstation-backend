import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings

AUDIO_FILENAME = "audio.pcm"


def _notifications_dir() -> Path:
    path = get_settings().notifications_data_dir_path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _item_dir(notification_id: str) -> Path:
    d = _notifications_dir() / notification_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _record_path(notification_id: str) -> Path:
    return _item_dir(notification_id) / "record.json"


def audio_path(notification_id: str) -> Path:
    return _item_dir(notification_id) / AUDIO_FILENAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _write_record(notification_id: str, record: dict[str, Any]) -> None:
    _atomic_write(_record_path(notification_id), json.dumps(record, indent=2))


def load_record(notification_id: str) -> Optional[dict[str, Any]]:
    path = _record_path(notification_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def create_notification(
    notification_id: str,
    source_voice_inbox_id: str,
    transcript: str,
    confidence: Optional[float],
    spoken_message: str,
    audio_bytes: bytes,
) -> dict[str, Any]:
    """Writes the pre-generated TTS audio alongside a pending record. Audio
    is always present at creation time - unlike voice_inbox/notes, there is
    no separate "processing" state, since by the time a notification exists
    the verifier has already run and tts.synthesize_speech() has already
    produced the clip (see voice_inbox_runner.py)."""
    audio_path(notification_id).write_bytes(audio_bytes)

    now = _now()
    record = {
        "notification_id": notification_id,
        "source_voice_inbox_id": source_voice_inbox_id,
        "transcript": transcript,
        "confidence": confidence,
        "spoken_message": spoken_message,
        "status": "pending",
        "created_at": now,
        "delivered_at": None,
        "audio": {"size_bytes": len(audio_bytes)},
    }
    _write_record(notification_id, record)
    return record


def list_pending() -> list[dict[str, Any]]:
    """Oldest first (FIFO) - so /pending always points at the notification
    that's been waiting longest, not an arbitrary one."""
    records = []
    for item_dir in _notifications_dir().iterdir():
        if not item_dir.is_dir():
            continue
        record = load_record(item_dir.name)
        if record is not None and record["status"] == "pending":
            records.append(record)
    records.sort(key=lambda r: r["created_at"])
    return records


def mark_delivered(notification_id: str) -> None:
    record = load_record(notification_id)
    if record is None:
        return
    record["status"] = "delivered"
    record["delivered_at"] = _now()
    _write_record(notification_id, record)
