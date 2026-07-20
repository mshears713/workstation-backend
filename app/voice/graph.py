from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from app.graph.model import get_chat_model
from app.voice.nodes import (
    LlmFactory,
    finalize_node,
    make_extract_actions_node,
    make_grounding_review_node,
    make_interpret_note_node,
    route_after_grounding,
    route_after_validate,
    validate_transcript_node,
)
from app.voice.state import VoiceNoteState

# Same rationale as app/graph/graph.py's LLM_RETRY_POLICY: the shared
# OpenRouter model occasionally returns transient upstream errors that
# langchain_openai surfaces as a plain ValueError, which LangGraph's default
# RetryPolicy does not retry. Duplicated here (rather than imported) to avoid
# coupling this graph's module to the design-review graph's module.
LLM_RETRY_POLICY = RetryPolicy(
    max_attempts=5,
    initial_interval=2.0,
    backoff_factor=2.0,
    retry_on=(Exception,),
)


def build_graph(llm_factory: LlmFactory = get_chat_model):
    """Build the voice-note processing graph.

    validate_transcript -> interpret_note -> extract_actions -> grounding_review
        -> (at most one bounded correction loop back to interpret_note) -> finalize

    `llm_factory` defaults to the real OpenRouter/Nemotron client (same model
    as the design-review graph) but tests pass a fake to avoid network calls.
    """
    builder = StateGraph(VoiceNoteState)

    builder.add_node("validate_transcript", validate_transcript_node)
    builder.add_node("interpret_note", make_interpret_note_node(llm_factory), retry_policy=LLM_RETRY_POLICY)
    builder.add_node("extract_actions", make_extract_actions_node(llm_factory), retry_policy=LLM_RETRY_POLICY)
    builder.add_node("grounding_review", make_grounding_review_node(llm_factory), retry_policy=LLM_RETRY_POLICY)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "validate_transcript")
    builder.add_conditional_edges(
        "validate_transcript", route_after_validate, ["interpret_note", "finalize"]
    )
    builder.add_edge("interpret_note", "extract_actions")
    builder.add_edge("extract_actions", "grounding_review")
    builder.add_conditional_edges(
        "grounding_review", route_after_grounding, ["interpret_note", "finalize"]
    )
    builder.add_edge("finalize", END)

    return builder.compile()


# Module-level compiled graph. Used directly by:
#  - langgraph.json (LangGraph Studio / `langgraph dev`, graph id "voice_note_processor")
#  - app/api/notes_runner.py (FastAPI-triggered note processing)
# Both entry points therefore run the exact same graph implementation.
graph = build_graph()
