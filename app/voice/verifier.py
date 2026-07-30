from typing import Optional

from app.graph.model import get_chat_model
from app.graph.structured import invoke_structured
from app.voice import prompts
from app.voice.nodes import LlmFactory
from app.voice.schemas import NotificationVerification

DEFAULT_MAX_ATTEMPTS = 2


def verify_low_confidence_transcript(
    transcript: str,
    confidence: Optional[float],
    llm_factory: LlmFactory = get_chat_model,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> NotificationVerification:
    """One-shot LLM judgment call for a low-confidence voice-inbox transcript
    - not a full LangGraph graph, since there's nothing here that needs a
    correction loop the way interpret_note/grounding_review do. Uses the same
    invoke_structured/LlmFactory primitives those nodes are built on, so
    tests can inject a fake chat model the same way tests/voice_fakes.py
    already does for the notes pipeline.

    Fails closed (worth_notifying=False) if the LLM never produces valid
    structured output after max_attempts - a missed notification is
    recoverable (the item still finalizes to Notion regardless, see
    voice_inbox_runner.py), a crashed pipeline run is not.
    """
    llm = llm_factory()
    user_prompt = prompts.build_notification_verifier_user_prompt(transcript, confidence)
    result = invoke_structured(
        llm,
        prompts.NOTIFICATION_VERIFIER_PERSONA,
        user_prompt,
        NotificationVerification,
        max_attempts,
    )
    if result.model is None:
        return NotificationVerification(
            worth_notifying=False,
            spoken_message="",
            reasoning=f"verifier failed to produce valid output after {max_attempts} attempt(s): {result.error}",
        )
    return result.model
