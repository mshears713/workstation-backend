import pytest

from app.config import get_settings


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
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
