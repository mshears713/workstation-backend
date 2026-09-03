from datetime import datetime, timezone

import pytest

from app.integrations import notion_client

# conftest.py stubs query_projects out for every test so nothing reaches
# Notion by accident. This module is the one place that tests the real
# implementation, so it puts the original back.
_REAL_QUERY_PROJECTS = notion_client.query_projects


@pytest.fixture(autouse=True)
def _use_real_query_projects(monkeypatch):
    monkeypatch.setattr(notion_client, "query_projects", _REAL_QUERY_PROJECTS)


class _FakeSettings:
    notion_api_key = "fake-notion-key"
    notion_voice_inbox_data_source_id = "fake-data-source-id"
    notion_sources_data_source_id = "fake-sources-data-source-id"
    notion_van_build_log_data_source_id = "fake-van-build-log-data-source-id"
    notion_projects_data_source_id = "fake-projects-data-source-id"
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


# --- the operator's routing hint -------------------------------------------
#
# The hint is advisory: the note lands in the Voice Inbox either way, and this
# relation only tells the downstream agent which project the operator had in
# mind. The "exactly the owned properties" test above already pins the absent
# case - no hint, no Related Project key at all.


def test_create_voice_inbox_page_sets_related_project_when_the_operator_chose_one(monkeypatch):
    monkeypatch.setattr(notion_client, "get_settings", lambda: _FakeSettings())

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(200, {"id": "page-123", "url": "https://notion.so/page-123"})

    monkeypatch.setattr(notion_client.httpx, "post", fake_post)

    notion_client.create_voice_inbox_page(
        name="2026-09-03 10:00:00 UTC",
        captured_at=datetime(2026, 9, 3, 10, 0, 0, tzinfo=timezone.utc),
        transcript="the alternator bracket needs a spacer",
        related_project_page_id="396850a9-11d3-818c-b435-d689b8d63db0",
    )

    properties = captured["json"]["properties"]
    assert properties["Related Project"] == {
        "relation": [{"id": "396850a9-11d3-818c-b435-d689b8d63db0"}]
    }
    # Still nothing written to the three fields Notion's own automation owns.
    assert "Category" not in properties
    assert "Processing Notes" not in properties


# --- reading the Projects database -----------------------------------------


def _projects_response(results, has_more=False, next_cursor=None):
    return {"results": results, "has_more": has_more, "next_cursor": next_cursor}


def _page(page_id, name, status="Active", cue="", device_label="", repo=None):
    return {
        "id": page_id,
        "properties": {
            "Name": {"title": [{"plain_text": name}]},
            "Cue": {"rich_text": [{"plain_text": cue}]},
            "Device Label": {"rich_text": [{"plain_text": device_label}]},
            "GitHub Repo": {"url": repo},
            "Status": {"select": {"name": status}},
        },
    }


def test_query_projects_asks_for_active_and_testing_only(monkeypatch):
    monkeypatch.setattr(notion_client, "get_settings", lambda: _FakeSettings())

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(200, _projects_response([]))

    monkeypatch.setattr(notion_client.httpx, "post", fake_post)
    notion_client.query_projects()

    assert captured["url"].endswith("/data_sources/fake-projects-data-source-id/query")
    statuses = [
        clause["select"]["equals"] for clause in captured["json"]["filter"]["or"]
    ]
    assert statuses == ["Active", "Testing"]


def test_query_projects_flattens_the_properties_it_reads(monkeypatch):
    monkeypatch.setattr(notion_client, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(
        notion_client.httpx,
        "post",
        lambda *a, **kw: _FakeResponse(
            200,
            _projects_response([
                _page(
                    "396850a9-11d3-818c-b435-d689b8d63db0",
                    "Van Flipping",
                    cue="Fleet van to trusted camper",
                    repo="https://github.com/mshears713/vanflip",
                )
            ]),
        ),
    )

    assert notion_client.query_projects() == [
        {
            "page_id": "396850a9-11d3-818c-b435-d689b8d63db0",
            "name": "Van Flipping",
            "device_label": "",
            "cue": "Fleet van to trusted camper",
            "github_repo": "https://github.com/mshears713/vanflip",
        }
    ]


def test_query_projects_rejoins_text_notion_split_into_spans(monkeypatch):
    """Notion splits styled text into several spans, so a cue typed as one
    phrase can arrive in pieces. Taking only the first span would silently
    truncate it."""
    monkeypatch.setattr(notion_client, "get_settings", lambda: _FakeSettings())
    page = _page("abc", "x")
    page["properties"]["Cue"] = {
        "rich_text": [{"plain_text": "Fleet van to "}, {"plain_text": "trusted camper"}]
    }
    monkeypatch.setattr(
        notion_client.httpx,
        "post",
        lambda *a, **kw: _FakeResponse(200, _projects_response([page])),
    )

    assert notion_client.query_projects()[0]["cue"] == "Fleet van to trusted camper"


def test_query_projects_follows_pagination(monkeypatch):
    monkeypatch.setattr(notion_client, "get_settings", lambda: _FakeSettings())
    pages = [
        _projects_response([_page("a", "First")], has_more=True, next_cursor="cur"),
        _projects_response([_page("b", "Second")]),
    ]
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json.get("start_cursor"))
        return _FakeResponse(200, pages[len(calls) - 1])

    monkeypatch.setattr(notion_client.httpx, "post", fake_post)

    assert [p["name"] for p in notion_client.query_projects()] == ["First", "Second"]
    assert calls == [None, "cur"]


def test_query_projects_raises_on_non_2xx(monkeypatch):
    """projects_catalog catches this and falls back to the local file - but it
    has to be told, not handed an empty list that looks like 'no projects'."""
    monkeypatch.setattr(notion_client, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(
        notion_client.httpx,
        "post",
        lambda *a, **kw: _FakeResponse(404, {"message": "not shared with the integration"}),
    )

    with pytest.raises(notion_client.NotionClientError):
        notion_client.query_projects()
