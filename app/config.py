from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Populates os.environ from .env so LangSmith's tracer (which reads env vars
# directly, not through this Settings object) picks up LANGSMITH_* values too.
load_dotenv(_PROJECT_ROOT / ".env")


class Settings(BaseSettings):
    openrouter_api_key: str = ""
    openrouter_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    langsmith_api_key: str = ""
    langsmith_tracing: bool = False
    langsmith_project: str = "operation-homebound-stage2"
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    openai_api_key: str = ""
    openai_transcription_model: str = "gpt-4o-mini-transcribe"
    openai_transcription_language: str = "en"

    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "alloy"

    # Below this, a transcript is treated as unreliably heard and routed to
    # the shared audio-reliability notification instead of continuing into
    # either pipeline (NOTE's Notion write or GO's entry architect) - see
    # app/voice/confidence.py for how the score itself is computed and
    # app/voice/audio_reliability.py for how both pipelines use it.
    audio_confidence_threshold: float = 0.55

    data_dir: str = "data/runs"
    notes_data_dir: str = "data/notes"
    voice_inbox_data_dir: str = "data/voice_inbox"
    notifications_data_dir: str = "data/notifications"
    entries_data_dir: str = "data/entries"
    issues_data_dir: str = "data/issues"
    # Transient accumulation buffer for chunked ESP32 recordings (see
    # app/api/streaming_capture.py) - not one of the "final artifact" dirs
    # above, entries are deleted as soon as a capture finalizes.
    streaming_tmp_dir: str = "data/_streaming_tmp"
    reviewer_max_attempts: int = 2
    request_timeout_seconds: int = 60

    # Never reaches the ESP32. The device names an approved repo id and this
    # backend resolves and files the issue - see projects_catalog.resolve_repo
    # and app/integrations/github_client.py.
    github_token: str = ""
    github_api_base_url: str = "https://api.github.com"

    notion_api_key: str = ""
    notion_voice_inbox_data_source_id: str = "a250d3f5-ae1f-471c-97da-47cd94f596d0"
    notion_sources_data_source_id: str = "6b0b9164-c5eb-4015-9bf4-b01ebfd938e2"
    notion_van_build_log_data_source_id: str = "342c88fc-e138-46ed-af22-ecf4717bdffb"
    # Read, not written. The AI-OS Projects database is the source of truth
    # for what the workstation may select - see app/api/projects_catalog.py.
    notion_projects_data_source_id: str = "4fe04711-6bac-470a-be5e-4652351ae7ef"

    api_host: str = "127.0.0.1"
    api_port: int = 8000

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def data_dir_path(self) -> Path:
        path = Path(self.data_dir)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        return path

    @property
    def notes_data_dir_path(self) -> Path:
        path = Path(self.notes_data_dir)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        return path

    @property
    def voice_inbox_data_dir_path(self) -> Path:
        path = Path(self.voice_inbox_data_dir)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        return path

    @property
    def notifications_data_dir_path(self) -> Path:
        path = Path(self.notifications_data_dir)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        return path

    @property
    def entries_data_dir_path(self) -> Path:
        path = Path(self.entries_data_dir)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        return path

    @property
    def issues_data_dir_path(self) -> Path:
        path = Path(self.issues_data_dir)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        return path

    @property
    def streaming_tmp_dir_path(self) -> Path:
        path = Path(self.streaming_tmp_dir)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
