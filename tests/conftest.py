import pytest

from app.api import projects_catalog
from app.config import get_settings
from app.integrations import notion_client


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Every test writes run artifacts to a throwaway temp dir instead of the
    project's real data/runs directory, and never attempts real network calls
    (LangSmith tracing included) since no real API keys are configured here."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("NOTES_DATA_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("VOICE_INBOX_DATA_DIR", str(tmp_path / "voice_inbox"))
    monkeypatch.setenv("NOTIFICATIONS_DATA_DIR", str(tmp_path / "notifications"))
    monkeypatch.setenv("ENTRIES_DATA_DIR", str(tmp_path / "entries"))
    # Without this the chunked-upload tests would accumulate .pcm files in
    # the project's real data/_streaming_tmp instead of a throwaway dir.
    monkeypatch.setenv("STREAMING_TMP_DIR", str(tmp_path / "_streaming_tmp"))
    monkeypatch.setenv("ISSUES_DATA_DIR", str(tmp_path / "issues"))
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def no_live_notion_projects(monkeypatch):
    """Keeps the catalog off the network.

    projects_catalog now reads the AI-OS Projects database, so without this
    every test that touches the catalog - including ones that only care about
    config/projects.json - would make a real Notion request and then take the
    fallback path anyway: slow, and quietly dependent on whether Notion is up.

    The default is a failure rather than an empty list, because that is what
    an unconfigured backend actually does, and it keeps the fallback honest.
    Tests that want Notion data monkeypatch query_projects themselves.
    """
    def unavailable():
        raise notion_client.NotionClientError("Notion disabled in tests")

    monkeypatch.setattr(notion_client, "query_projects", unavailable)
    projects_catalog.reset_notion_cache()
    yield
    projects_catalog.reset_notion_cache()
