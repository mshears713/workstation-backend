from typing import Optional

from pydantic import BaseModel

from app.graph.model import get_chat_model
from app.graph.structured import invoke_structured_with_retry
from app.voice import prompts
from app.voice.confidence import transcription_confidence
from app.voice.nodes import LlmFactory
from app.voice.schemas import AudioClarityMessage
from app.voice.transcription import TranscriptionResult

DEFAULT_MAX_ATTEMPTS = 2

_FALLBACK_MESSAGE = "I have a recording I didn't catch clearly - could you say that again?"


class AudioReliabilityResult(BaseModel):
    """Shared verdict both NOTE (voice_inbox_runner.py) and GO
    (entries_runner.py) act on identically - see check_audio_reliability()."""

    reliable: bool
    confidence: Optional[float] = None
    spoken_message: str = ""  # only set when reliable is False


def check_audio_reliability(
    transcript_result: TranscriptionResult,
    threshold: float,
    llm_factory: LlmFactory = get_chat_model,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> AudioReliabilityResult:
    """Whether a transcript was heard clearly enough to trust for further
    processing - purely about speech-to-text confidence (see confidence.py),
    never about content/meaning. Both pipelines call this as their first
    step and, on reliable=False, stop there - see each runner's use of
    app.api.notification_delivery.notify_audio_unreliable().

    An unknown confidence score (model doesn't support logprobs, or no
    logprobs came back) is treated as reliable=True, not an error - see
    confidence.transcription_confidence()'s own None-handling.
    """
    score = transcription_confidence(transcript_result)
    if score is None or score >= threshold:
        return AudioReliabilityResult(reliable=True, confidence=score)

    llm = llm_factory()
    user_prompt = prompts.build_audio_clarity_user_prompt(transcript_result.text, score)
    try:
        result = invoke_structured_with_retry(
            llm, prompts.AUDIO_CLARITY_PERSONA, user_prompt, AudioClarityMessage, max_attempts
        )
        spoken_message = result.model.spoken_message if result.model else _FALLBACK_MESSAGE
    except Exception:  # noqa: BLE001 - persistent upstream failure after retries; fall back rather than crash
        spoken_message = _FALLBACK_MESSAGE
    return AudioReliabilityResult(reliable=False, confidence=score, spoken_message=spoken_message)
