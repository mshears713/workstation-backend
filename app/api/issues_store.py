"""On-disk record of every issue this backend has filed.

Small on purpose - one JSON file per request_id, no index, matching the
other *_store modules. Its job is idempotency and an audit trail: a voice
capture that is retried must not file the same issue twice, and there should
be a local answer to "did that actually go through" without opening GitHub.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings


def _data_dir() -> Path:
    path = get_settings().issues_data_dir_path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _record_path(issue_request_id: str) -> Path:
    return _data_dir() / f"{issue_request_id}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _item_dir(issue_request_id: str) -> Path:
    path = _data_dir() / issue_request_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_audio(issue_request_id: str, content: bytes) -> Path:
    """Keeps the source audio next to the record.

    transcribe_audio() takes a path, and keeping the file also means a filed
    issue can be checked against what was actually said - the transcript is
    a model's opinion, the audio is the evidence.
    """
    audio_path = _item_dir(issue_request_id) / "audio.wav"
    audio_path.write_bytes(content)
    return audio_path


def load_record(issue_request_id: str) -> Optional[dict[str, Any]]:
    path = _record_path(issue_request_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write(issue_request_id: str, record: dict[str, Any]) -> None:
    """Atomic replace, same as the other stores - a half-written record would
    make a filed issue look unfiled and invite a duplicate."""
    path = _record_path(issue_request_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    tmp.replace(path)


def create_record(
    request_id: str,
    repo_id: str,
    repo: str,
    title: str,
    body: str,
    source: str,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    record = {
        "request_id": request_id,
        "repo_id": repo_id,
        "repo": repo,
        "title": title,
        "body": body,
        "source": source,
        "metadata": metadata or {},
        "status": "creating",
        "created_at": _now(),
        "updated_at": _now(),
        "issue": None,
        "error": None,
    }
    _write(request_id, record)
    return record


def mark_created(request_id: str, issue: dict[str, Any]) -> dict[str, Any]:
    record = load_record(request_id) or {}
    record.update({"status": "created", "issue": issue, "error": None, "updated_at": _now()})
    _write(request_id, record)
    return record


def mark_failed(request_id: str, error: str) -> dict[str, Any]:
    record = load_record(request_id) or {}
    record.update({"status": "failed", "error": error, "updated_at": _now()})
    _write(request_id, record)
    return record
