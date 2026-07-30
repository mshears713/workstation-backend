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
    # Per-token {token, bytes, logprob} dicts, present only when the model
    # supports include=["logprobs"] (gpt-4o-mini-transcribe and siblings -
    # see confidence.py, which turns this into the single score the
    # voice-inbox pipeline branches on). None for models that don't return it.
    logprobs: Optional[list[dict[str, Any]]] = None


def get_openai_client() -> OpenAI:
    """Built lazily, never at import time, so importing this module works even
    before OPENAI_API_KEY is filled in."""
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to .env before transcribing audio."
        )
    return OpenAI(api_key=settings.openai_api_key)


# The only transcription models whose API supports include=["logprobs"] -
# see app/voice/confidence.py, which is what actually consumes this data.
# Requesting it against an unsupported model (e.g. whisper-1) would either be
# ignored or rejected depending on the model, so this is checked before
# asking for it rather than assumed from settings.openai_transcription_model.
_LOGPROBS_CAPABLE_MODELS = {
    "gpt-4o-transcribe",
    "gpt-4o-mini-transcribe",
    "gpt-4o-mini-transcribe-2025-12-15",
}


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

    logprobs_requested = settings.openai_transcription_model in _LOGPROBS_CAPABLE_MODELS
    if logprobs_requested:
        # logprobs only works with response_format="json" - the default
        # ("json" for these models already, but pinned explicitly since
        # that's the contract logprobs depends on, not an assumption).
        kwargs["response_format"] = "json"
        kwargs["include"] = ["logprobs"]

    with audio_path.open("rb") as audio_file:
        response = client.audio.transcriptions.create(file=audio_file, **kwargs)

    if hasattr(response, "model_dump"):
        raw = response.model_dump()
    elif isinstance(response, dict):
        raw = response
    else:
        raw = {"text": getattr(response, "text", str(response))}

    logprobs_out = None
    if logprobs_requested:
        response_logprobs = getattr(response, "logprobs", None) or raw.get("logprobs")
        if response_logprobs:
            logprobs_out = [
                entry.model_dump() if hasattr(entry, "model_dump") else dict(entry)
                for entry in response_logprobs
            ]

    return TranscriptionResult(
        text=getattr(response, "text", raw.get("text", "")),
        language=settings.openai_transcription_language or raw.get("language"),
        model=settings.openai_transcription_model,
        raw=raw,
        logprobs=logprobs_out,
    )
