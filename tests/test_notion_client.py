from datetime import datetime, timezone

import pytest

from app.integrations import notion_client


class _FakeSettings:
    notion_api_key = "fake-notion-key"
    notion_voice_inbox_data_source_id = "fake-data-source-id"
    notion_sources_data_source_id = "fake-sources-data-source-id"
    notion_van_build_log_data_source_id = "fake-van-build-log-data-source-id"
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


def test_create_source_page_sends_expected_properties(monkeypatch):
    monkeypatch.setattr(notion_client, "get_settings", lambda: _FakeSettings())

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(200, {"id": "source-page-1", "url": "https://notion.so/source-page-1"})

    monkeypatch.setattr(notion_client.httpx, "post", fake_post)

    captured_at = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
    result = notion_client.create_source_page(
        name="Van 1 electrical: ran 12V line to fridge",
        cue="fridge 12V line",
        summary="Ran a 12V line to the fridge.",
        observation="Mike described routing 12 gauge wire from the panel to the fridge location.",
        open_questions="",
        capture_confidence="High",
        captured_at=captured_at,
    )

    assert result == {"id": "source-page-1", "url": "https://notion.so/source-page-1"}
    payload = captured["json"]
    assert payload["parent"] == {"type": "data_source_id", "data_source_id": "fake-sources-data-source-id"}
    properties = payload["properties"]
    assert properties["Name"]["title"][0]["text"]["content"] == "Van 1 electrical: ran 12V line to fridge"
    assert properties["Source Type"]["select"]["name"] == "Voice Note"
    assert properties["Status"]["select"]["name"] == "Captured"
    assert properties["Promotion Readiness"]["select"]["name"] == "Raw"
    assert properties["Capture Confidence"]["select"]["name"] == "High"
    assert "Potential Destination" not in properties


def test_create_source_page_includes_potential_destination_when_given(monkeypatch):
    monkeypatch.setattr(notion_client, "get_settings", lambda: _FakeSettings())

    captured = {}
    monkeypatch.setattr(
        notion_client.httpx,
        "post",
        lambda url, headers=None, json=None, timeout=None: (captured.update(json=json), _FakeResponse(200, {"id": "x", "url": "y"}))[1],
    )

    notion_client.create_source_page(
        name="x", cue="", summary="", observation="", open_questions="",
        capture_confidence="Medium", captured_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        potential_destination=["Project"],
    )
    assert captured["json"]["properties"]["Potential Destination"]["multi_select"] == [{"name": "Project"}]


def test_create_van_build_log_page_sends_expected_properties(monkeypatch):
    monkeypatch.setattr(notion_client, "get_settings", lambda: _FakeSettings())

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(200, {"id": "vbl-page-1", "url": "https://notion.so/vbl-page-1"})

    monkeypatch.setattr(notion_client.httpx, "post", fake_post)

    result = notion_client.create_van_build_log_page(
        name="Ran 12V line to fridge",
        van_or_scope="Van 1",
        entry_type="Build Note",
        workstream="Electrical",
        allocation="Not Applicable",
        summary="Ran a 12V line to the fridge.",
        source_page_id="source-page-1",
        amount=45.5,
        labor_hours=1.5,
        date=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )

    assert result == {"id": "vbl-page-1", "url": "https://notion.so/vbl-page-1"}
    payload = captured["json"]
    assert payload["parent"] == {
        "type": "data_source_id",
        "data_source_id": "fake-van-build-log-data-source-id",
    }
    properties = payload["properties"]
    assert properties["Van / Scope"]["select"]["name"] == "Van 1"
    assert properties["Entry Type"]["select"]["name"] == "Build Note"
    assert properties["Workstream"]["select"]["name"] == "Electrical"
    assert properties["Media Status"]["select"]["name"] == "None"
    assert properties["Status"]["select"]["name"] == "Logged"
    assert properties["Source Record"]["relation"] == [{"id": "source-page-1"}]
    assert properties["Amount"]["number"] == 45.5
    assert properties["Labor Hours"]["number"] == 1.5


def test_create_van_build_log_page_omits_amount_and_labor_hours_when_absent(monkeypatch):
    monkeypatch.setattr(notion_client, "get_settings", lambda: _FakeSettings())

    captured = {}
    monkeypatch.setattr(
        notion_client.httpx,
        "post",
        lambda url, headers=None, json=None, timeout=None: (captured.update(json=json), _FakeResponse(200, {"id": "x", "url": "y"}))[1],
    )

    notion_client.create_van_build_log_page(
        name="x", van_or_scope="Van 1", entry_type="Build Note", workstream="Electrical",
        allocation="Not Applicable", summary="", source_page_id="source-page-1",
    )
    properties = captured["json"]["properties"]
    assert "Amount" not in properties
    assert "Labor Hours" not in properties


def test_create_van_build_log_page_rejects_naive_datetime(monkeypatch):
    monkeypatch.setattr(notion_client, "get_settings", lambda: _FakeSettings())

    with pytest.raises(notion_client.NotionClientError):
        notion_client.create_van_build_log_page(
            name="x", van_or_scope="Van 1", entry_type="Build Note", workstream="Electrical",
            allocation="Not Applicable", summary="", source_page_id="source-page-1",
            date=datetime(2026, 7, 30),
        )
