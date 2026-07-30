import logging
from typing import Optional

from app.entries import prompts
from app.entries.schemas import EntryArchitectResult
from app.graph.model import get_chat_model
from app.graph.structured import invoke_structured_with_retry
from app.voice.nodes import LlmFactory

log = logging.getLogger("entry_architect")

DEFAULT_MAX_ATTEMPTS = 2


def run_entry_architect(
    transcript: str,
    llm_factory: LlmFactory = get_chat_model,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Optional[EntryArchitectResult]:
    """One-shot structured call turning a reliable transcript into Source (+
    maybe Van Build Log) fields - see EntryArchitectResult's own doc comment
    for the null-van_build_log contract. Same invoke_structured/LlmFactory
    shape as every other structured call in this codebase, with an added
    outer retry for transient upstream errors (rate limits, momentary
    outages) since this has no LangGraph node around it to retry at - see
    invoke_structured_with_retry()'s own docstring.

    Returns None if the LLM never produces valid structured output at all
    after max_attempts, OR if every transient-error retry is exhausted (not
    the same as van_build_log being null, which is a valid, expected
    response shape) - callers (entries_runner.py) fall back to a minimal
    deterministic Source record in that case, since even a persistent
    upstream failure shouldn't lose the raw transcript as evidence.
    """
    llm = llm_factory()
    user_prompt = prompts.build_entry_architect_user_prompt(transcript)
    try:
        result = invoke_structured_with_retry(
            llm, prompts.ENTRY_ARCHITECT_PERSONA, user_prompt, EntryArchitectResult, max_attempts
        )
    except Exception as exc:  # noqa: BLE001 - persistent upstream failure after retries
        log.warning("entry architect failed after retries: %r", exc)
        return None
    return result.model
