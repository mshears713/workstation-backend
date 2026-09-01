"""Shared temp-file accumulation for chunked ESP32 audio uploads.

Long GO/NOTE/SEND recordings (up to main/audio_capture.c's
AUDIO_NOTE_MAX_DURATION_MS, currently 20 minutes) can't fit in the ESP32's
PSRAM as one buffer - 10 minutes of raw 16kHz/16-bit/mono audio alone is
~18MB, more than the board's entire 16MB PSRAM pool. The firmware streams
~15s chunks instead (main/audio_capture.c's streaming recording loop, via
main/stream_upload.c), POSTing each one to this kind's /{request_id}/chunk
endpoint, then a final .../finish call once recording ends (STOP or the
safety cap).

This module just accumulates those chunks into one temp .pcm file per
(kind, request_id) and wraps the result in a single real WAV header on
finalize - every downstream consumer (entries_runner.py etc.) still just
sees one ordinary audio file, exactly as before the streaming path existed.
The chunking is invisible past finalize_wav().
"""

import wave
from io import BytesIO
from pathlib import Path

from app.config import get_settings

# The three ESP32 upload kinds that share this module - see entries_router.py/
# notes_router.py/voice_inbox_router.py, each of which calls append_chunk()/
# finalize_wav() with its own kind so the three in-progress temp files never
# collide even if request_ids were ever reused across kinds.
_VALID_KINDS = {"entries", "notes", "voice_inbox"}


def _streaming_dir(kind: str) -> Path:
    assert kind in _VALID_KINDS, f"unknown streaming kind: {kind}"
    path = get_settings().streaming_tmp_dir_path / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def _chunk_path(kind: str, request_id: str) -> Path:
    return _streaming_dir(kind) / f"{request_id}.pcm"


def append_chunk(kind: str, request_id: str, data: bytes) -> int:
    """Appends raw PCM bytes to the in-progress capture for request_id,
    returns the new total byte count so far. Chunks must arrive in order -
    the firmware sends them synchronously, one blocking POST at a time from
    a single task (see audio_capture.c's recording loop), so there is no
    reordering or concurrent-write case to handle here."""
    path = _chunk_path(kind, request_id)
    with path.open("ab") as f:
        f.write(data)
    return path.stat().st_size


def finalize_wav(kind: str, request_id: str, sample_rate_hz: int, bits_per_sample: int, channels: int) -> bytes:
    """Wraps the accumulated raw PCM chunks in one real WAV header and
    deletes the temp file. Missing/empty input (a finish call with no prior
    chunks) still produces a valid, silent zero-length WAV rather than
    raising - validate_upload()'s "audio file is empty" check downstream is
    what should reject that case, not this function."""
    path = _chunk_path(kind, request_id)
    pcm = path.read_bytes() if path.exists() else b""

    buf = BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(bits_per_sample // 8)
        wav.setframerate(sample_rate_hz)
        wav.writeframes(pcm)

    if path.exists():
        path.unlink()

    return buf.getvalue()


def discard_chunks(kind: str, request_id: str) -> None:
    """Deletes the in-progress temp file without assembling it - the CANCEL
    button's counterpart to finalize_wav(). Best-effort: a request_id with
    no accumulated chunks (cancelled before the first one landed) is not an
    error, just a no-op."""
    path = _chunk_path(kind, request_id)
    if path.exists():
        path.unlink()
