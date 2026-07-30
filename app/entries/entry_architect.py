import logging
from typing import Optional

from app.entries.graph import build_graph, graph as _default_graph
from app.entries.schemas import EntryArchitectResult
from app.graph.model import get_chat_model
from app.voice.nodes import LlmFactory

log = logging.getLogger("entry_architect")

DEFAULT_MAX_ATTEMPTS = 2


def run_entry_architect(
    transcript: str,
    llm_factory: LlmFactory = get_chat_model,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Optional[EntryArchitectResult]:
    """Runs the draft -> review -> bounded-correction-loop -> finalize graph
    (see app/entries/graph.py) and unwraps its `final` state back into an
    EntryArchitectResult - same call signature/return contract the previous
    single-call implementation had, so entries_runner.py and its tests
    don't need to know this became a graph internally.

    Returns None if the graph itself fails to complete (a LangGraph node
    exhausted its own RetryPolicy on a persistent transient upstream
    error - see graph.py's LLM_RETRY_POLICY) - not the same as a
    van_build_log-is-null response, which is a valid, expected finish.
    Callers fall back to a minimal deterministic Source record in that
    case, since even a persistent upstream failure shouldn't lose the raw
    transcript as evidence.
    """
    g = _default_graph if llm_factory is get_chat_model else build_graph(llm_factory)
    try:
        result_state = g.invoke({"transcript": transcript, "max_attempts": max_attempts})
    except Exception as exc:  # noqa: BLE001 - persistent upstream failure after the graph's own retries
        log.warning("entry architect graph failed: %r", exc)
        return None

    final = result_state.get("final")
    if not final:
        return None
    return EntryArchitectResult.model_validate(final)
