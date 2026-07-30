from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from app.entries.nodes import (
    finalize_node,
    make_draft_entry_node,
    make_review_draft_node,
    route_after_review,
)
from app.entries.state import EntryState
from app.graph.model import get_chat_model
from app.voice.nodes import LlmFactory

# Same rationale as app/graph/graph.py's/app/voice/graph.py's own
# LLM_RETRY_POLICY - duplicated here rather than imported, to avoid coupling
# this graph's module to either of theirs. Each node here runs inside a
# LangGraph StateGraph, so it gets this node-level retry (not
# invoke_structured_with_retry, which is for the callers with no graph
# around them - see app/entries/verifier.py and app/voice/audio_reliability.py).
LLM_RETRY_POLICY = RetryPolicy(
    max_attempts=5,
    initial_interval=2.0,
    backoff_factor=2.0,
    retry_on=(Exception,),
)


def build_graph(llm_factory: LlmFactory = get_chat_model):
    """Pre-write entry-drafting graph:

    draft_entry -> review_draft -> (at most one bounded correction loop back
        to draft_entry) -> finalize

    Runs entirely before any Notion write - entries_runner.py takes the
    finished `final` draft and does the actual Source/Van Build Log page
    creation, then runs the separate, independent post-creation
    SemanticVerification (app/entries/verifier.py) against what actually
    got written. This graph's own review_draft node is a different check:
    catching invented/overstated/misclassified content early enough to
    still fix it by re-drafting, which the post-creation verifier
    deliberately cannot do (see verifier.py's docstring on why it doesn't
    correct Notion records in this phase).

    `llm_factory` defaults to the real OpenRouter model (same as every
    other graph in this codebase) but tests pass a fake to avoid network
    calls.
    """
    builder = StateGraph(EntryState)

    builder.add_node("draft_entry", make_draft_entry_node(llm_factory), retry_policy=LLM_RETRY_POLICY)
    builder.add_node("review_draft", make_review_draft_node(llm_factory), retry_policy=LLM_RETRY_POLICY)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "draft_entry")
    builder.add_edge("draft_entry", "review_draft")
    builder.add_conditional_edges("review_draft", route_after_review, ["draft_entry", "finalize"])
    builder.add_edge("finalize", END)

    return builder.compile()


# Module-level compiled graph - used directly by
# app/entries/entry_architect.py's run_entry_architect(), which wraps it to
# keep the exact same call signature/return contract entries_runner.py and
# its tests already depend on.
graph = build_graph()
