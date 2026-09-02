"""GitHub issue capture - text in, or audio in.

Two ways in, one way through:

  POST /api/v1/issues                     text (curl, tests, anything)
  POST /api/v1/issues/{id}/chunk          raw PCM, same as the other kinds
  POST /api/v1/issues/finish              assemble -> transcribe -> file
  POST /api/v1/issues/{id}/cancel         discard

Both funnel into _file_issue(), so the allowlist, the idempotency rule and
the title derivation cannot drift apart between them. The text endpoint stays
curl-testable, which is what made this verifiable against real GitHub before
any voice capture existed.

The ESP32 never holds a GitHub token. It names an approved repo *id*, this
resolves it through projects_catalog, and app/integrations/github_client.py
does the writing.
"""

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api import issues_store as store
from app.api import projects_catalog, streaming_capture
from app.api.upload_validation import SAFE_ID_RE, UploadValidationError, validate_upload
from app.integrations import github_client
from app.voice import transcription

router = APIRouter(prefix="/api/v1/issues", tags=["issues"])

_STREAMING_KIND = "issues"

# A voice transcript has no title, so one is derived from the opening words.
# GitHub allows far more, but a title that does not fit in a listing is not
# doing its job.
TITLE_MAX_LEN = 72


class IssueCreateRequest(BaseModel):
    request_id: str = Field(..., description="Idempotency key; doubles as the record id")
    repo_id: str = Field(..., description="An id from GET /api/v1/projects, not an owner/name slug")
    body: str = Field(..., description="Transcript or description")
    title: Optional[str] = Field(None, description="Derived from body when absent")
    source: str = Field("unknown", description="What captured this, e.g. esp32-box3")
    metadata: Optional[dict[str, Any]] = None


def _derive_title(body: str) -> str:
    """First sentence, or the opening words if there is no sentence break.

    Voice transcripts arrive as one run of prose, so something has to pick a
    title. Doing it here rather than on the device keeps the firmware free of
    a rule that will want tuning.
    """
    text = " ".join(body.split())
    for end in (". ", "? ", "! "):
        idx = text.find(end)
        if 0 < idx <= TITLE_MAX_LEN:
            return text[:idx + 1].strip()
    if len(text) <= TITLE_MAX_LEN:
        return text
    cut = text[:TITLE_MAX_LEN].rsplit(" ", 1)[0]
    return (cut or text[:TITLE_MAX_LEN]).rstrip(",;:") + "..."


def _compose_body(body: str, source: str, request_id: str, metadata: Optional[dict[str, Any]]) -> str:
    """The transcript, plus a short provenance footer.

    Worth carrying: months later, "was this dictated at the workbench or
    typed?" changes how much the wording is trusted.
    """
    lines = [body.strip(), "", "---", f"Captured by `{source}` (request `{request_id}`)."]
    if metadata:
        for key in sorted(metadata):
            lines.append(f"- {key}: {metadata[key]}")
    return "\n".join(lines)


def _public(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": record.get("request_id"),
        "repo_id": record.get("repo_id"),
        "repo": record.get("repo"),
        "title": record.get("title"),
        "status": record.get("status"),
        "issue": record.get("issue"),
    }


def _file_issue(
    request_id: str,
    repo_id: str,
    body: str,
    title: Optional[str],
    source: str,
    metadata: Optional[dict[str, Any]],
    response: Response,
) -> dict[str, Any]:
    """Validate, resolve, file, record. Shared by both entry points.

    request_id doubles as the record id, the same pattern the capture routes
    use. The firmware retries on a lost response, and one spoken sentence must
    not become two issues - so a repeat of a *succeeded* request returns the
    original. A repeat of a *failed* one is allowed through, since the point
    of a retry is that the second attempt can work.
    """
    if not request_id or not SAFE_ID_RE.match(request_id) or request_id in {".", ".."}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="request_id is required and must match ^[A-Za-z0-9_.-]{1,200}$",
        )
    if not body or not body.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="body is required",
        )

    existing = store.load_record(request_id)
    if existing is not None and existing.get("status") == "created":
        response.status_code = status.HTTP_200_OK
        return {**_public(existing), "duplicate": True}

    # The allowlist. A device - or anything else reaching this endpoint - can
    # only file against a repository someone put in config/projects.json.
    try:
        repo = projects_catalog.resolve_repo(repo_id)
    except projects_catalog.CatalogError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"project catalog is misconfigured: {exc}",
        ) from exc
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown repo_id {repo_id!r} - see GET /api/v1/projects",
        )

    resolved_title = (title or "").strip() or _derive_title(body)
    store.create_record(
        request_id=request_id, repo_id=repo_id, repo=repo,
        title=resolved_title, body=body, source=source, metadata=metadata,
    )

    try:
        issue = github_client.create_issue(
            repo=repo,
            title=resolved_title,
            body=_compose_body(body, source, request_id, metadata),
        )
    except github_client.GitHubClientError as exc:
        # Recorded before raising: a failed attempt that leaves no trace is
        # one nobody can retry deliberately or explain afterwards.
        store.mark_failed(request_id, str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    record = store.mark_created(request_id, issue)
    return {**_public(record), "duplicate": False}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_issue(req: IssueCreateRequest, response: Response) -> dict[str, Any]:
    """Text in, issue out. No audio - see /finish for the voice path."""
    return _file_issue(
        request_id=req.request_id, repo_id=req.repo_id, body=req.body,
        title=req.title, source=req.source, metadata=req.metadata, response=response,
    )


# --- voice capture: same chunk/finish/cancel shape as the other kinds ------
#
# Identical on the wire to notes/voice-inbox/entries, so main/stream_upload.c
# works unchanged and the device only needs a client that adds repo_id to the
# finish body.


@router.post("/{request_id}/chunk", status_code=status.HTTP_202_ACCEPTED)
async def upload_issue_chunk(request_id: str, request: Request, offset: int | None = None) -> dict:
    """Raw PCM as the whole body. `offset` is the ordering guard - see
    streaming_capture.append_chunk()."""
    data = await request.body()
    try:
        total, duplicate = streaming_capture.append_chunk(
            _STREAMING_KIND, request_id, data, offset=offset
        )
    except UploadValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except streaming_capture.ChunkOrderError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"received_bytes": len(data), "total_bytes": total, "duplicate": duplicate}


@router.post("/{request_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_issue_chunks(request_id: str) -> dict:
    try:
        streaming_capture.discard_chunks(_STREAMING_KIND, request_id)
    except UploadValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return {"request_id": request_id, "status": "cancelled"}


@router.post("/finish", status_code=status.HTTP_201_CREATED)
async def finish_issue_capture(
    response: Response,
    request_id: str = Form(...),
    repo_id: str = Form(...),
    source: str = Form(...),
    sample_rate_hz: int = Form(...),
    bits_per_sample: int = Form(16),
    channels: int = Form(1),
) -> dict[str, Any]:
    """Assemble the chunks, transcribe, file the issue - and answer with the
    result rather than a job id.

    Synchronous on purpose, unlike the notes/voice-inbox routes which return
    202 and process in the background. The operator is standing at the device
    waiting to be told the issue number, and a device that had to poll for it
    would need state it does not otherwise need. The cost is that this call
    holds for transcription plus the GitHub write - a few seconds for a 15s
    clip - so the firmware allows a longer timeout on this one finish call.
    Safe: the mic is already closed by then, so a slow finish costs no audio.
    """
    # Ahead of the work, not after: a retry of an already-filed capture must
    # not transcribe again, let alone file again.
    existing = store.load_record(request_id)
    if existing is not None and existing.get("status") == "created":
        response.status_code = status.HTTP_200_OK
        return {**_public(existing), "duplicate": True}

    try:
        audio_bytes = streaming_capture.finalize_wav(
            _STREAMING_KIND, request_id, sample_rate_hz, bits_per_sample, channels
        )
    except UploadValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    # finalize_wav() returns a valid 44-byte header-only WAV when no chunk
    # ever arrived, and validate_upload's "audio file is empty" check does not
    # fire on that - 44 is not zero. Without this guard a finish with no audio
    # would be sent to transcription and could file an issue from nothing.
    if not streaming_capture.has_audio(audio_bytes):
        store.mark_failed(request_id, "no audio was captured")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="no audio was captured - nothing to transcribe",
        )

    block_align = channels * (bits_per_sample // 8)
    duration_seconds = (
        (len(audio_bytes) - 44) / (sample_rate_hz * block_align)
        if block_align and sample_rate_hz else None
    )

    try:
        validate_upload(request_id, source, audio_bytes, duration_seconds, sample_rate_hz)
    except UploadValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    audio_path = store.save_audio(request_id, audio_bytes)
    try:
        result = await asyncio.to_thread(transcription.transcribe_audio, audio_path)
    except Exception as exc:  # noqa: BLE001 - surfaced to the device as-is
        store.mark_failed(request_id, f"transcription failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"transcription failed: {exc}",
        ) from exc

    text = (result.text or "").strip()
    if not text:
        # Silence, or a capture that picked up nothing. Filing an empty issue
        # would be worse than saying so.
        store.mark_failed(request_id, "transcript was empty")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="transcript was empty - nothing to file",
        )

    metadata: dict[str, Any] = {"source": source}
    if duration_seconds is not None:
        metadata["duration_seconds"] = round(duration_seconds, 2)

    return _file_issue(
        request_id=request_id, repo_id=repo_id, body=text, title=None,
        source=source, metadata=metadata, response=response,
    )


@router.get("/{request_id}")
async def get_issue(request_id: str) -> dict[str, Any]:
    record = store.load_record(request_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="issue request not found")
    return {**_public(record), "error": record.get("error")}
