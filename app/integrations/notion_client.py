from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.config import get_settings

# Tied to the "parent": {"type": "data_source_id", ...} page-creation shape
# below - this workspace's Voice Inbox database uses Notion's newer multi-
# source-database model (data sources distinct from databases), which only
# the 2025-09-03+ API understands. Not a setting: an admin bumping this via
# .env without also verifying the parent shape still matches would silently
# break page creation.
NOTION_API_VERSION = "2025-09-03"
NOTION_API_BASE_URL = "https://api.notion.com/v1"

# Notion's rich_text property allows up to 100 blocks, each with a text
# content limit of 2000 characters.
_RICH_TEXT_BLOCK_LIMIT = 2000


class NotionClientError(Exception):
    pass


def _require_api_key() -> str:
    """Built lazily, never at import time - mirrors
    transcription.get_openai_client()'s pattern so importing this module
    works even before NOTION_API_KEY is filled in."""
    settings = get_settings()
    if not settings.notion_api_key:
        raise NotionClientError(
            "NOTION_API_KEY is not set. Add it to .env before creating Voice Inbox pages."
        )
    return settings.notion_api_key


def _chunk_transcript(text: str) -> list[str]:
    """Splits `text` into blocks no longer than _RICH_TEXT_BLOCK_LIMIT,
    breaking at the nearest preceding whitespace to the boundary rather than
    a hard character slice, so a chunk boundary never lands mid-word (or
    mid multi-byte grapheme)."""
    if not text:
        return [""]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > _RICH_TEXT_BLOCK_LIMIT:
        split_at = remaining.rfind(" ", 0, _RICH_TEXT_BLOCK_LIMIT)
        if split_at <= 0:
            split_at = _RICH_TEXT_BLOCK_LIMIT
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip(" ")
    chunks.append(remaining)
    return chunks


def _rich_text_property(text: str) -> dict[str, Any]:
    """Same chunking as create_voice_inbox_page's Original Transcript field,
    factored out so create_source_page()/create_van_build_log_page() (which
    each have several rich_text properties) don't repeat it. An empty
    string still produces one empty-content block, a valid Notion state."""
    return {"rich_text": [{"text": {"content": block}} for block in _chunk_transcript(text)]}


def _post_page(parent_data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
    """Shared POST /pages call - create_voice_inbox_page()/create_source_page()/
    create_van_build_log_page() only differ in which data source and
    properties they send."""
    settings = get_settings()
    api_key = _require_api_key()

    response = httpx.post(
        f"{NOTION_API_BASE_URL}/pages",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": "application/json",
        },
        json={
            "parent": {"type": "data_source_id", "data_source_id": parent_data_source_id},
            "properties": properties,
        },
        timeout=settings.request_timeout_seconds,
    )
    if response.status_code >= 300:
        raise NotionClientError(f"Notion page creation failed: {response.status_code} {response.text}")

    body = response.json()
    return {"id": body.get("id"), "url": body.get("url")}


def create_source_page(
    name: str,
    cue: str,
    summary: str,
    observation: str,
    open_questions: str,
    capture_confidence: str,
    captured_at: datetime,
    source_type: str = "Voice Note",
    status: str = "Captured",
    promotion_readiness: str = "Raw",
    potential_destination: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Creates a page in the AI-OS Sources database from the entry
    architect's SourceFields (see app/entries/schemas.py) plus a few
    deterministic fields entries_runner.py sets itself: source_type/status/
    promotion_readiness default to fixed values for every voice-captured
    entry from this pipeline, and capture_confidence comes from the
    audio-reliability score's tier, not the LLM. Returns {"id": ..., "url": ...}.
    Raises NotionClientError on any non-2xx response.
    """
    if captured_at.tzinfo is None:
        raise NotionClientError(
            "captured_at must be timezone-aware - a naive datetime would silently "
            "write the wrong instant (or get rejected) as Notion's Captured property"
        )

    settings = get_settings()
    properties: dict[str, Any] = {
        "Name": {"title": [{"text": {"content": name}}]},
        "Cue": _rich_text_property(cue),
        "Summary": _rich_text_property(summary),
        "Observation": _rich_text_property(observation),
        "Open Questions": _rich_text_property(open_questions),
        "Captured": {"date": {"start": captured_at.isoformat()}},
        "Status": {"select": {"name": status}},
        "Source Type": {"select": {"name": source_type}},
        "Promotion Readiness": {"select": {"name": promotion_readiness}},
        "Capture Confidence": {"select": {"name": capture_confidence}},
    }
    if potential_destination:
        properties["Potential Destination"] = {"multi_select": [{"name": d} for d in potential_destination]}

    return _post_page(settings.notion_sources_data_source_id, properties)


def create_van_build_log_page(
    name: str,
    van_or_scope: str,
    entry_type: str,
    workstream: str,
    allocation: str,
    summary: str,
    source_page_id: str,
    module_or_component: str = "",
    amount: Optional[float] = None,
    labor_hours: Optional[float] = None,
    documentation_value: str = "Unknown",
    date: Optional[datetime] = None,
    status: str = "Logged",
    media_status: str = "None",
) -> dict[str, Any]:
    """Creates a page in the Van Build Log database from the entry
    architect's VanBuildLogFields (see app/entries/schemas.py), linked back
    to the Source page via the Source Record relation. media_status
    defaults to "None" (this pipeline has no photo-capture capability) and
    status defaults to "Logged" - both deterministic, not LLM-chosen.
    amount/labor_hours are omitted entirely (not sent as null) when absent,
    since Notion's number property doesn't need a present-but-empty entry.
    Returns {"id": ..., "url": ...}. Raises NotionClientError on any non-2xx
    response.
    """
    entry_date = date or datetime.now(timezone.utc)
    if entry_date.tzinfo is None:
        raise NotionClientError(
            "date must be timezone-aware - a naive datetime would silently "
            "write the wrong instant (or get rejected) as Notion's Date property"
        )

    settings = get_settings()
    properties: dict[str, Any] = {
        "Name": {"title": [{"text": {"content": name}}]},
        "Van / Scope": {"select": {"name": van_or_scope}},
        "Entry Type": {"select": {"name": entry_type}},
        "Workstream": {"select": {"name": workstream}},
        "Allocation": {"select": {"name": allocation}},
        "Documentation Value": {"select": {"name": documentation_value}},
        "Media Status": {"select": {"name": media_status}},
        "Status": {"select": {"name": status}},
        "Date": {"date": {"start": entry_date.isoformat()}},
        "Summary": _rich_text_property(summary),
        "Module / Component": _rich_text_property(module_or_component),
        "Source Record": {"relation": [{"id": source_page_id}]},
    }
    if amount is not None:
        properties["Amount"] = {"number": amount}
    if labor_hours is not None:
        properties["Labor Hours"] = {"number": labor_hours}

    return _post_page(settings.notion_van_build_log_data_source_id, properties)


def create_voice_inbox_page(
    name: str,
    captured_at: datetime,
    transcript: str,
    related_project_page_id: Optional[str] = None,
) -> dict[str, Any]:
    """Creates a page in the Voice Inbox Notion database with only the fields
    this pipeline owns - Name, Captured At, Source (always "Workstation"),
    Original Transcript, Status, and Related Project when the operator chose
    one at the device. Category, Processing Notes and the page body are left
    for Notion's own AI automation to fill in afterward.

    `related_project_page_id` is the operator's routing hint, and it is
    advisory rather than routing: the note still lands in the Voice Inbox
    either way, and setting the relation only tells the downstream agent
    which project the operator had in mind. None is a normal case - the
    operator often has no view, and the transcript decides instead.

    Returns {"id": ..., "url": ...}. Raises NotionClientError on any non-2xx
    response.
    """
    if captured_at.tzinfo is None:
        raise NotionClientError(
            "captured_at must be timezone-aware - a naive datetime would silently "
            "write the wrong instant (or get rejected) as Notion's Captured At property"
        )

    settings = get_settings()
    properties = {
        "Name": {"title": [{"text": {"content": name}}]},
        "Captured At": {"date": {"start": captured_at.isoformat()}},
        "Source": {"select": {"name": "Workstation"}},
        "Original Transcript": _rich_text_property(transcript),
        "Status": {"select": {"name": "New"}},
    }
    if related_project_page_id:
        properties["Related Project"] = {"relation": [{"id": related_project_page_id}]}
    return _post_page(settings.notion_voice_inbox_data_source_id, properties)


# ---------------------------------------------------------------------------
# Reading the Projects database
#
# The only read this client does; everything else here creates pages. The
# AI-OS Projects database is the source of truth for what the workstation may
# select, so that starting a new project makes it appear on the device without
# a firmware flash or a backend edit - see app/api/projects_catalog.py.
# ---------------------------------------------------------------------------

# Which projects the device is allowed to see. "Testing" is deliberately
# included: something being started on but not yet live is exactly the kind of
# thing worth capturing notes against.
PROJECT_STATUSES = ("Active", "Testing")

# A ceiling on pagination, not an expectation. The real list is single digits;
# this only stops a filter mistake from walking the whole database.
_PROJECT_PAGE_LIMIT = 5


def _plain_text(prop: Optional[dict[str, Any]], key: str) -> str:
    """Flattens a Notion title/rich_text property to a plain string.

    Notion splits styled text into several spans, so a value typed as one
    phrase can still arrive as multiple blocks - joining them is required, not
    defensive. Returns "" for a missing or empty property, which every caller
    treats as "not set" rather than an error.
    """
    if not isinstance(prop, dict):
        return ""
    spans = prop.get(key)
    if not isinstance(spans, list):
        return ""
    return "".join(
        span.get("plain_text", "") for span in spans if isinstance(span, dict)
    ).strip()


def query_projects() -> list[dict[str, str]]:
    """Every Active or Testing project, as plain dicts.

    Returns a list of {"page_id", "name", "device_label", "cue", "github_repo"}
    with "" for anything unset. Raises NotionClientError on a non-2xx response
    or a missing API key - projects_catalog.py catches that and falls back to
    the local config file, so a Notion outage degrades the selectable list
    rather than taking capture down.
    """
    settings = get_settings()
    api_key = _require_api_key()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "filter": {
            "or": [
                {"property": "Status", "select": {"equals": status}}
                for status in PROJECT_STATUSES
            ]
        },
        "page_size": 100,
    }

    out: list[dict[str, str]] = []
    cursor: Optional[str] = None
    for _ in range(_PROJECT_PAGE_LIMIT):
        if cursor:
            payload["start_cursor"] = cursor
        response = httpx.post(
            f"{NOTION_API_BASE_URL}/data_sources/{settings.notion_projects_data_source_id}/query",
            headers=headers,
            json=payload,
            timeout=settings.request_timeout_seconds,
        )
        if response.status_code >= 300:
            raise NotionClientError(
                f"Notion projects query failed: {response.status_code} {response.text}"
            )
        body = response.json()

        for page in body.get("results", []):
            if not isinstance(page, dict):
                continue
            props = page.get("properties") or {}
            out.append(
                {
                    "page_id": str(page.get("id") or ""),
                    "name": _plain_text(props.get("Name"), "title"),
                    "device_label": _plain_text(props.get("Device Label"), "rich_text"),
                    "cue": _plain_text(props.get("Cue"), "rich_text"),
                    "github_repo": (props.get("GitHub Repo") or {}).get("url") or "",
                }
            )

        if not body.get("has_more"):
            break
        cursor = body.get("next_cursor")
        if not cursor:
            break

    return [entry for entry in out if entry["page_id"] and entry["name"]]
