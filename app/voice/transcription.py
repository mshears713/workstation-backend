from pathlib import Path
from typing import Any, Optional

from openai import OpenAI
from pydantic import BaseModel

from app.config import get_settings


class TranscriptionResult(BaseModel):
    text: str
    language: Optional[str] = None
    model: str
    raw: dict[str, Any]


def get_openai_client() -> OpenAI:
    """Built lazily, never at import time, so importing this module works even
    before OPENAI_API_KEY is filled in."""
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to .env before transcribing audio."
        )
    return OpenAI(api_key=settings.openai_api_key)


def transcribe_audio(audio_path: Path) -> TranscriptionResult:
    """Transcribe a local audio file via the OpenAI audio transcription API.

    This is the only place in the codebase that calls OpenAI directly - the
    endpoint and the voice-note graph both receive an already-normalized
    TranscriptionResult, never raw audio or the OpenAI client.
    """
    settings = get_settings()
    client = get_openai_client()

    kwargs: dict[str, Any] = {"model": settings.openai_transcription_model}
    if settings.openai_transcription_language:
        kwargs["language"] = settings.openai_transcription_language

    with audio_path.open("rb") as audio_file:
        response = client.audio.transcriptions.create(file=audio_file, **kwargs)

    if hasattr(response, "model_dump"):
        raw = response.model_dump()
    elif isinstance(response, dict):
        raw = response
    else:
        raw = {"text": getattr(response, "text", str(response))}

    return TranscriptionResult(
        text=getattr(response, "text", raw.get("text", "")),
        language=settings.openai_transcription_language or raw.get("language"),
        model=settings.openai_transcription_model,
        raw=raw,
    )
