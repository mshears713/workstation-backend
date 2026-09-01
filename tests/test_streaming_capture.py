"""Chunked-upload path tests.

This is the path the ESP32 actually uses for every NOTE/SEND/GO recording
(main/stream_upload.c), and it had no coverage at all. The offset guard is
what replaces the ordering guarantee the firmware used to provide by
uploading synchronously from a single task - see
app/api/streaming_capture.py's append_chunk() docstring.
"""

import pytest
from fastapi.testclient import TestClient

from app.api import streaming_capture
from app.api.main import app
from app.api.upload_validation import UploadValidationError

KINDS = [
    ("notes", "/api/v1/notes"),
    ("voice_inbox", "/api/v1/voice-inbox"),
    ("entries", "/api/v1/entries"),
]

CHUNK_A = b"\x01\x02" * 512   # 1024 bytes, stands in for one PCM chunk
CHUNK_B = b"\x03\x04" * 512
CHUNK_C = b"\x05\x06" * 512


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _chunk(client, base, request_id, data, offset=None):
    url = f"{base}/{request_id}/chunk"
    if offset is not None:
        url += f"?offset={offset}"
    return client.post(url, content=data)


# --- ordering guard -------------------------------------------------------

@pytest.mark.parametrize("kind,base", KINDS)
def test_sequential_chunks_accumulate(client, kind, base):
    rid = f"seq-{kind}"
    r1 = _chunk(client, base, rid, CHUNK_A, offset=0)
    assert r1.status_code == 202
    assert r1.json() == {"received_bytes": 1024, "total_bytes": 1024, "duplicate": False}

    r2 = _chunk(client, base, rid, CHUNK_B, offset=1024)
    assert r2.status_code == 202
    assert r2.json()["total_bytes"] == 2048
    assert r2.json()["duplicate"] is False


@pytest.mark.parametrize("kind,base", KINDS)
def test_out_of_order_chunk_is_rejected(client, kind, base):
    """The failure this guard exists for: a chunk that skips ahead would
    splice silence into the middle of a note, and the result would still
    transcribe into plausible text."""
    rid = f"gap-{kind}"
    assert _chunk(client, base, rid, CHUNK_A, offset=0).status_code == 202
    resp = _chunk(client, base, rid, CHUNK_C, offset=2048)  # skipped 1024..2047
    assert resp.status_code == 409
    assert "does not continue" in resp.json()["detail"]


@pytest.mark.parametrize("kind,base", KINDS)
def test_rejected_chunk_does_not_corrupt_the_capture(client, kind, base):
    rid = f"nocorrupt-{kind}"
    _chunk(client, base, rid, CHUNK_A, offset=0)
    _chunk(client, base, rid, CHUNK_C, offset=99999)
    # The good next chunk still applies at the offset the reject left alone.
    ok = _chunk(client, base, rid, CHUNK_B, offset=1024)
    assert ok.status_code == 202
    assert ok.json()["total_bytes"] == 2048


@pytest.mark.parametrize("kind,base", KINDS)
def test_duplicate_chunk_is_an_idempotent_noop(client, kind, base):
    """The POST landed but its response was lost, so the firmware retries
    the same bytes. That must not append them twice."""
    rid = f"dupe-{kind}"
    _chunk(client, base, rid, CHUNK_A, offset=0)
    _chunk(client, base, rid, CHUNK_B, offset=1024)

    replay = _chunk(client, base, rid, CHUNK_B, offset=1024)
    assert replay.status_code == 202
    assert replay.json()["duplicate"] is True
    assert replay.json()["total_bytes"] == 2048

    nxt = _chunk(client, base, rid, CHUNK_C, offset=2048)
    assert nxt.json()["total_bytes"] == 3072


@pytest.mark.parametrize("kind,base", KINDS)
def test_negative_offset_is_rejected(client, kind, base):
    assert _chunk(client, base, f"neg-{kind}", CHUNK_A, offset=-1).status_code == 409


@pytest.mark.parametrize("kind,base", KINDS)
def test_offset_is_optional_for_older_firmware(client, kind, base):
    """Omitting offset keeps the pre-guard append-blindly behavior, so
    firmware that predates the guard still uploads successfully."""
    rid = f"nooffset-{kind}"
    assert _chunk(client, base, rid, CHUNK_A).status_code == 202
    r = _chunk(client, base, rid, CHUNK_B)
    assert r.json()["total_bytes"] == 2048


# --- request_id validation ------------------------------------------------

@pytest.mark.parametrize("kind,base", KINDS)
@pytest.mark.parametrize("bad_id", ["has space", "semi;colon", "x" * 201, r"sla\sh", "quo\"te"])
def test_invalid_request_id_rejected_on_chunk(client, kind, base, bad_id):
    """request_id becomes a filename here, so it is validated before it
    reaches the filesystem."""
    assert _chunk(client, base, bad_id, CHUNK_A, offset=0).status_code == 422


@pytest.mark.parametrize("kind", [k for k, _ in KINDS])
@pytest.mark.parametrize("bad_id", [".", "..", "", "has space", "x" * 201])
def test_traversal_request_ids_rejected_at_the_function(kind, bad_id):
    """"." and ".." cannot reach the handler through a URL - the client and
    router normalize them away before routing - so the guard against them is
    asserted directly on the function that names the file. It matters
    because these satisfy SAFE_ID_RE (which permits dots) yet are path
    traversal once used as a path component."""
    with pytest.raises(UploadValidationError):
        streaming_capture.append_chunk(kind, bad_id, CHUNK_A, offset=0)
    with pytest.raises(UploadValidationError):
        streaming_capture.discard_chunks(kind, bad_id)


# --- assembly -------------------------------------------------------------

@pytest.mark.parametrize("kind,base", KINDS)
def test_chunks_assemble_in_order(client, kind, base):
    rid = f"assemble-{kind}"
    _chunk(client, base, rid, CHUNK_A, offset=0)
    _chunk(client, base, rid, CHUNK_B, offset=1024)
    _chunk(client, base, rid, CHUNK_C, offset=2048)

    wav = streaming_capture.finalize_wav(kind, rid, 16000, 16, 1)
    assert wav[:4] == b"RIFF"
    assert wav[44:] == CHUNK_A + CHUNK_B + CHUNK_C


@pytest.mark.parametrize("kind,base", KINDS)
def test_cancel_discards_accumulated_chunks(client, kind, base):
    rid = f"cancel-{kind}"
    _chunk(client, base, rid, CHUNK_A, offset=0)
    assert client.post(f"{base}/{rid}/cancel").status_code == 202
    # A fresh capture under the same id starts from zero again.
    assert _chunk(client, base, rid, CHUNK_B, offset=0).json()["total_bytes"] == 1024
