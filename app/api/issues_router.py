"""POST /api/v1/issues - file a GitHub issue on the device's behalf.

Text in, issue out. Deliberately does not touch audio: #7 asks for this to be
built and tested independently before voice capture is wired to it, so the
transcription step belongs to the caller (#8), not here. That also makes this
usable from curl, which is how it gets exercised without a workstation.

The ESP32 never holds a GitHub token. It names an approved repo *id*, this
resolves it through projects_catalog, and app/integrations/github_client.py
does the writing.
"""

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.api import issues_store as store
from app.api import projects_catalog
from app.api.upload_validation import SAFE_ID_RE
from app.integrations import github_client

router = APIRouter(prefix="/api/v1/issues", tags=["issues"])

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


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_issue(req: IssueCreateRequest, response: Response) -> dict[str, Any]:
    """Files one issue, idempotently.

    request_id doubles as the record id, the same pattern the capture routes
    use. A retried voice capture - which the firmware will do on a lost
    response - must not produce a second issue, so a request_id that already
    succeeded returns the original with duplicate=true and 200.
    """
    request_id = req.request_id
    if not request_id or not SAFE_ID_RE.match(request_id) or request_id in {".", ".."}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="request_id is required and must match ^[A-Za-z0-9_.-]{1,200}$",
        )
    if not req.body or not req.body.strip():
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
        repo = projects_catalog.resolve_repo(req.repo_id)
    except projects_catalog.CatalogError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"project catalog is misconfigured: {exc}",
        ) from exc
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown repo_id {req.repo_id!r} - see GET /api/v1/projects",
        )

    title = (req.title or "").strip() or _derive_title(req.body)
    store.create_record(
        request_id=request_id, repo_id=req.repo_id, repo=repo,
        title=title, body=req.body, source=req.source, metadata=req.metadata,
    )

    try:
        issue = github_client.create_issue(repo=repo, title=title, body=_compose_body(req))
    except github_client.GitHubClientError as exc:
        # Recorded before raising: a failed attempt that leaves no trace is
        # one nobody can retry deliberately or explain afterwards.
        store.mark_failed(request_id, str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    record = store.mark_created(request_id, issue)
    return {**_public(record), "duplicate": False}


def _compose_body(req: IssueCreateRequest) -> str:
    """The transcript, plus a short provenance footer.

    Worth carrying: months later, "was this dictated at the workbench or
    typed?" changes how much the wording is trusted.
    """
    lines = [req.body.strip(), "", "---", f"Captured by `{req.source}` (request `{req.request_id}`)."]
    if req.metadata:
        for key in sorted(req.metadata):
            lines.append(f"- {key}: {req.metadata[key]}")
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


@router.get("/{request_id}")
async def get_issue(request_id: str) -> dict[str, Any]:
    record = store.load_record(request_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="issue request not found")
    return {**_public(record), "error": record.get("error")}
