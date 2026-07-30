from datetime import datetime, timezone

import pytest

from app.integrations import notion_client


class _FakeSettings:
    notion_api_key = "fake-notion-key"
    notion_voice_inbox_data_source_id = "fake-data-source-id"
    request_timeout_seconds = 5


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def test_create_voice_inbox_page_sends_exactly_the_four_owned_properties(monkeypatch):
    monkeypatch.setattr(notion_client, "get_settings", lambda: _FakeSettings())

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse(200, {"id": "page-123", "url": "https://notion.so/page-123"})

    monkeypatch.setattr(notion_client.httpx, "post", fake_post)

    captured_at = datetime(2026, 7, 23, 2, 51, 0, tzinfo=timezone.utc)
    result = notion_client.create_voice_inbox_page(
        name="2026-07-23 02:51:00 UTC", captured_at=captured_at, transcript="hello world"
    )

    assert result == {"id": "page-123", "url": "https://notion.so/page-123"}
    assert captured["url"] == f"{notion_client.NOTION_API_BASE_URL}/pages"
    assert captured["headers"]["Authorization"] == "Bearer fake-notion-key"
    assert captured["headers"]["Notion-Version"] == notion_client.NOTION_API_VERSION

    payload = captured["json"]
    assert payload["parent"] == {"type": "data_source_id", "data_source_id": "fake-data-source-id"}
    properties = payload["properties"]
    assert set(properties.keys()) == {"Name", "Captured At", "Source", "Original Transcript", "Status"}
    assert properties["Name"]["title"][0]["text"]["content"] == "2026-07-23 02:51:00 UTC"
    assert properties["Captured At"]["date"]["start"] == captured_at.isoformat()
    assert properties["Source"]["select"]["name"] == "Workstation"
    assert properties["Original Transcript"]["rich_text"][0]["text"]["content"] == "hello world"
    assert properties["Status"]["select"]["name"] == "New"


def test_create_voice_inbox_page_chunks_long_transcript_without_splitting_words(monkeypatch):
    monkeypatch.setattr(notion_client, "get_settings", lambda: _FakeSettings())

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(200, {"id": "page-123", "url": "https://notion.so/page-123"})

    monkeypatch.setattr(notion_client.httpx, "post", fake_post)

    long_transcript = ("word " * 1000).strip()  # 4999 chars, well over the 2000-char block limit
    captured_at = datetime(2026, 7, 23, 2, 51, 0, tzinfo=timezone.utc)
    notion_client.create_voice_inbox_page(name="x", captured_at=captured_at, transcript=long_transcript)

    blocks = [b["text"]["content"] for b in captured["json"]["properties"]["Original Transcript"]["rich_text"]]
    assert len(blocks) > 1
    for block in blocks:
        assert len(block) <= 2000
    # Rejoining should reconstruct the original transcript with single spaces
    # between blocks - i.e. no word was split across a chunk boundary.
    assert " ".join(blocks) == long_transcript


def test_create_voice_inbox_page_rejects_naive_datetime(monkeypatch):
    monkeypatch.setattr(notion_client, "get_settings", lambda: _FakeSettings())

    with pytest.raises(notion_client.NotionClientError):
        notion_client.create_voice_inbox_page(
            name="x", captured_at=datetime(2026, 7, 23, 2, 51, 0), transcript="hi"
        )


def test_create_voice_inbox_page_raises_on_non_2xx(monkeypatch):
    monkeypatch.setattr(notion_client, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(
        notion_client.httpx, "post", lambda *a, **kw: _FakeResponse(400, {"message": "bad request"})
    )

    with pytest.raises(notion_client.NotionClientError):
        notion_client.create_voice_inbox_page(
            name="x", captured_at=datetime(2026, 7, 23, 2, 51, 0, tzinfo=timezone.utc), transcript="hi"
        )


def test_create_voice_inbox_page_requires_api_key(monkeypatch):
    class _NoKeySettings(_FakeSettings):
        notion_api_key = ""

    monkeypatch.setattr(notion_client, "get_settings", lambda: _NoKeySettings())

    with pytest.raises(notion_client.NotionClientError):
        notion_client.create_voice_inbox_page(
            name="x", captured_at=datetime(2026, 7, 23, 2, 51, 0, tzinfo=timezone.utc), transcript="hi"
        )
