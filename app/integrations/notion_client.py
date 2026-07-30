from datetime import datetime
from typing import Any

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


def create_voice_inbox_page(name: str, captured_at: datetime, transcript: str) -> dict[str, Any]:
    """Creates a page in the Voice Inbox Notion database with only the four
    fields this pipeline owns - Name, Captured At, Source (always
    "Workstation"), and Original Transcript. Everything else (Category,
    Processing Notes, Related Project, page body) is left for Notion's own
    AI automation to fill in afterward. Returns {"id": ..., "url": ...}.
    Raises NotionClientError on any non-2xx response.
    """
    if captured_at.tzinfo is None:
        raise NotionClientError(
            "captured_at must be timezone-aware - a naive datetime would silently "
            "write the wrong instant (or get rejected) as Notion's Captured At property"
        )

    settings = get_settings()
    api_key = _require_api_key()

    transcript_blocks = _chunk_transcript(transcript)

    payload = {
        "parent": {
            "type": "data_source_id",
            "data_source_id": settings.notion_voice_inbox_data_source_id,
        },
        "properties": {
            "Name": {"title": [{"text": {"content": name}}]},
            "Captured At": {"date": {"start": captured_at.isoformat()}},
            "Source": {"select": {"name": "Workstation"}},
            "Original Transcript": {
                "rich_text": [{"text": {"content": block}} for block in transcript_blocks]
            },
            "Status": {"select": {"name": "New"}},
        },
    }

    response = httpx.post(
        f"{NOTION_API_BASE_URL}/pages",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=settings.request_timeout_seconds,
    )
    if response.status_code >= 300:
        raise NotionClientError(
            f"Notion page creation failed: {response.status_code} {response.text}"
        )

    body = response.json()
    return {"id": body.get("id"), "url": body.get("url")}
