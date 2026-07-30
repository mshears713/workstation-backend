from typing import Optional

from app.entries import prompts
from app.entries.schemas import EntryArchitectResult
from app.graph.model import get_chat_model
from app.graph.structured import invoke_structured
from app.voice.nodes import LlmFactory

DEFAULT_MAX_ATTEMPTS = 2


def run_entry_architect(
    transcript: str,
    llm_factory: LlmFactory = get_chat_model,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Optional[EntryArchitectResult]:
    """One-shot structured call turning a reliable transcript into Source (+
    maybe Van Build Log) fields - see EntryArchitectResult's own doc comment
    for the null-van_build_log contract. Same invoke_structured/LlmFactory
    shape as every other structured call in this codebase.

    Returns None only if the LLM never produces valid structured output at
    all after max_attempts (not the same as van_build_log being null, which
    is a valid, expected response shape) - callers (entries_runner.py) fall
    back to a minimal deterministic Source record in that case, since even a
    parse failure shouldn't lose the raw transcript as evidence.
    """
    llm = llm_factory()
    user_prompt = prompts.build_entry_architect_user_prompt(transcript)
    result = invoke_structured(
        llm, prompts.ENTRY_ARCHITECT_PERSONA, user_prompt, EntryArchitectResult, max_attempts
    )
    return result.model
