from app.entries import prompts
from app.entries.schemas import SemanticVerification
from app.graph.model import get_chat_model
from app.graph.structured import invoke_structured_with_retry
from app.voice.nodes import LlmFactory

DEFAULT_MAX_ATTEMPTS = 2

_FAIL_CLOSED_QUESTION = (
    "I logged a build note but couldn't fully verify it - could you take a look when you get a chance?"
)


def run_semantic_verifier(
    transcript: str,
    source_fields: dict,
    van_build_log_fields: dict,
    project_hint: str | None = None,
    llm_factory: LlmFactory = get_chat_model,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> SemanticVerification:
    """Independent post-creation check: does the Source + Van Build Log
    record faithfully represent the transcript? Only ever called once both
    records exist (see entries_runner.py) - a missing van_build_log_fields
    is handled entirely upstream of this function, not here (see
    EntryArchitectResult's null-van_build_log path).

    Fails closed toward notifying (confidence="low", notification_required
    True) if the LLM never produces valid structured output after
    max_attempts, or if every transient-error retry is exhausted (see
    invoke_structured_with_retry()'s docstring - this has no LangGraph node
    around it to retry at) - unlike audio_reliability's fallback, silently
    skipping a genuinely uncertain record is worse here than an extra
    notification, since the records are already live in Notion.
    """
    llm = llm_factory()
    user_prompt = prompts.build_semantic_verifier_user_prompt(
        transcript, source_fields, van_build_log_fields, project_hint
    )
    try:
        result = invoke_structured_with_retry(
            llm, prompts.SEMANTIC_VERIFIER_PERSONA, user_prompt, SemanticVerification, max_attempts
        )
    except Exception as exc:  # noqa: BLE001 - persistent upstream failure after retries
        return SemanticVerification(
            confidence="low",
            notification_required=True,
            reason=f"verifier failed after retries: {exc!r}",
            spoken_question=_FAIL_CLOSED_QUESTION,
        )
    if result.model is None:
        return SemanticVerification(
            confidence="low",
            notification_required=True,
            reason=f"verifier failed to produce valid output after {max_attempts} attempt(s): {result.error}",
            spoken_question=_FAIL_CLOSED_QUESTION,
        )
    return result.model
