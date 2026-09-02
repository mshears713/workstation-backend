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
from app.api.upload_validation import SAFE_ID_RE, UploadValidationError

# The ESP32 upload kinds that share this module - see entries_router.py/
# notes_router.py/voice_inbox_router.py, each of which calls append_chunk()/
# finalize_wav() with its own kind so the three in-progress temp files never
# collide even if request_ids were ever reused across kinds.
_VALID_KINDS = {"entries", "notes", "voice_inbox", "issues"}


def _streaming_dir(kind: str) -> Path:
    assert kind in _VALID_KINDS, f"unknown streaming kind: {kind}"
    path = get_settings().streaming_tmp_dir_path / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


# A WAV with no frames is still 44 bytes of header, which is why
# validate_upload's "audio file is empty" check does not catch a capture that
# never received a chunk - 44 is not zero.
WAV_HEADER_BYTES = 44


def has_audio(wav_bytes: bytes) -> bool:
    """True if the assembled WAV actually contains samples."""
    return len(wav_bytes) > WAV_HEADER_BYTES


class ChunkOrderError(Exception):
    """A chunk arrived that does not continue the accumulated capture -
    a gap, or a reordering. Never silently tolerated: assembling chunks in
    the wrong order produces scrambled audio that still transcribes into
    plausible-sounding text, so it would corrupt a note invisibly."""


def _validate_request_id(request_id: str) -> None:
    """request_id becomes a filename here, so it is validated before it
    ever reaches the filesystem - the finish path validates via
    validate_upload(), but the chunk path had no equivalent check even
    though it is what actually creates the file."""
    if not request_id or not SAFE_ID_RE.match(request_id) or request_id in {".", ".."}:
        raise UploadValidationError(
            "request_id is required and must match ^[A-Za-z0-9_.-]{1,200}$"
        )


def _chunk_path(kind: str, request_id: str) -> Path:
    _validate_request_id(request_id)
    return _streaming_dir(kind) / f"{request_id}.pcm"


def append_chunk(
    kind: str, request_id: str, data: bytes, offset: int | None = None
) -> tuple[int, bool]:
    """Appends raw PCM bytes to the in-progress capture for request_id.
    Returns (total_bytes_so_far, was_duplicate).

    `offset` is the byte position the firmware believes this chunk starts
    at. It is the ordering guard, and it needs no stored state of its own:
    the accumulated file's size IS the expected next offset.

        offset == size                     -> the next chunk, append it
        offset < size and offset+len <= size -> a retry of a chunk already
                                               applied (the POST landed but
                                               its response was lost), so
                                               this is an idempotent no-op
        anything else                      -> a gap or a reordering, reject

    This matters because audio_capture.c uploads chunks from a task other
    than the one filling the buffer, so ordering is no longer guaranteed by
    the firmware being single-threaded and blocking. Passing offset=None
    keeps the old append-blindly behavior so firmware predating the guard
    still works.
    """
    path = _chunk_path(kind, request_id)
    size = path.stat().st_size if path.exists() else 0

    if offset is not None:
        if offset < 0:
            raise ChunkOrderError("offset must be >= 0")
        if offset != size:
            if offset < size and offset + len(data) <= size:
                # Already have these bytes - the firmware is retrying a
                # chunk whose response it never saw. Do nothing and report
                # success, so the retry does not duplicate the audio.
                return size, True
            raise ChunkOrderError(
                f"chunk offset {offset} does not continue the capture "
                f"(expected {size}) - gap or out-of-order chunk"
            )

    with path.open("ab") as f:
        f.write(data)
    return path.stat().st_size, False


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
