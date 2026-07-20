from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from app.graph import prompts
from app.graph.model import get_chat_model
from app.graph.nodes import (
    LlmFactory,
    intake_node,
    make_auditor_node,
    make_chair_node,
    make_reviewer_node,
)
from app.graph.state import ReviewState

# Transient upstream errors (e.g. free-tier rate limits, 502s from the model
# provider) are retried automatically. langchain_openai surfaces provider-side
# error payloads as a plain ValueError, which LangGraph's default RetryPolicy
# does NOT retry (it assumes ValueError means a programming bug) - so we widen
# retry_on to cover it explicitly. Invalid-but-200 responses (bad JSON) are a
# separate, bounded concern handled inside invoke_structured.
LLM_RETRY_POLICY = RetryPolicy(
    max_attempts=5,
    initial_interval=2.0,
    backoff_factor=2.0,
    retry_on=(Exception,),
)


def build_graph(llm_factory: LlmFactory = get_chat_model):
    """Build the design-review graph.

    `llm_factory` is a zero-arg callable returning a chat model. It defaults to
    the real OpenRouter/Nemotron client but tests pass a fake to avoid network
    calls. The factory is only ever invoked inside node execution, never at
    build/import time.
    """
    builder = StateGraph(ReviewState)

    builder.add_node("intake", intake_node)
    builder.add_node(
        "embedded_reviewer",
        make_reviewer_node(
            "embedded_reviewer",
            "Embedded/Workstation Reviewer",
            prompts.EMBEDDED_PERSONA,
            "embedded_report",
            llm_factory,
        ),
        retry_policy=LLM_RETRY_POLICY,
    )
    builder.add_node(
        "backend_reviewer",
        make_reviewer_node(
            "backend_reviewer",
            "FastAPI/Backend Reviewer",
            prompts.BACKEND_PERSONA,
            "backend_report",
            llm_factory,
        ),
        retry_policy=LLM_RETRY_POLICY,
    )
    builder.add_node(
        "langgraph_reviewer",
        make_reviewer_node(
            "langgraph_reviewer",
            "LangGraph Reviewer",
            prompts.LANGGRAPH_PERSONA,
            "langgraph_report",
            llm_factory,
        ),
        retry_policy=LLM_RETRY_POLICY,
    )
    builder.add_node("auditor", make_auditor_node(llm_factory), retry_policy=LLM_RETRY_POLICY)
    builder.add_node("chair", make_chair_node(llm_factory), retry_policy=LLM_RETRY_POLICY)

    builder.add_edge(START, "intake")
    builder.add_edge("intake", "embedded_reviewer")
    builder.add_edge("intake", "backend_reviewer")
    builder.add_edge("intake", "langgraph_reviewer")
    builder.add_edge("embedded_reviewer", "auditor")
    builder.add_edge("backend_reviewer", "auditor")
    builder.add_edge("langgraph_reviewer", "auditor")
    builder.add_edge("auditor", "chair")
    builder.add_edge("chair", END)

    return builder.compile()


# Module-level compiled graph. Used directly by:
#  - langgraph.json (LangGraph Studio / `langgraph dev`)
#  - app/api/runner.py (FastAPI-triggered runs)
# Both entry points therefore run the exact same graph implementation.
graph = build_graph()
