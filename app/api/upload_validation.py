import re

# Keeps the ESP32-supplied request_id safe to use directly as a directory
# name (it becomes the note_id/voice_inbox_id - see the runner modules).
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")


class UploadValidationError(Exception):
    pass


def validate_upload(
    request_id: str,
    source: str,
    audio_bytes: bytes,
    duration_seconds: float | None,
    sample_rate_hz: int | None,
) -> None:
    """Shared validation for the two ESP32 audio-upload endpoints
    (/api/v1/notes and /api/v1/voice-inbox) - same contract, same checks."""
    # "." and ".." satisfy SAFE_ID_RE (it allows dots) but are path
    # traversal once request_id is used as the per-item directory name in
    # the *_store modules, so they are excluded explicitly.
    if not request_id or not SAFE_ID_RE.match(request_id) or request_id in {".", ".."}:
        raise UploadValidationError(
            "request_id is required and must match ^[A-Za-z0-9_.-]{1,200}$"
        )
    if not source or not source.strip():
        raise UploadValidationError("source is required")
    if not audio_bytes:
        raise UploadValidationError("audio file is empty")
    if duration_seconds is not None and duration_seconds < 0:
        raise UploadValidationError("duration_seconds must be >= 0")
    if sample_rate_hz is not None and sample_rate_hz <= 0:
        raise UploadValidationError("sample_rate_hz must be > 0")
