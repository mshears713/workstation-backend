from app.graph.graph import build_graph
from tests.fakes import FakeAlwaysInvalidChatModel, FakeStructuredChatModel


def test_graph_happy_path_produces_all_reports_and_final_disposition():
    graph = build_graph(llm_factory=FakeStructuredChatModel)

    result = graph.invoke({"run_id": "test-run-1", "notes": None})

    assert result["proposal"]
    assert result["embedded_report"]["role"] == "Fake Reviewer"
    assert result["backend_report"]["role"] == "Fake Reviewer"
    assert result["langgraph_report"]["role"] == "Fake Reviewer"
    assert result["audit_report"]["summary"]
    assert result["final"]["disposition"] == "approve_with_changes"

    node_names = {ev["node"] for ev in result["events"]}
    assert node_names == {
        "intake",
        "embedded_reviewer",
        "backend_reviewer",
        "langgraph_reviewer",
        "auditor",
        "chair",
    }


def test_graph_uses_bounded_fallback_when_model_never_produces_valid_json():
    graph = build_graph(llm_factory=FakeAlwaysInvalidChatModel)

    result = graph.invoke({"run_id": "test-run-2", "notes": None, "max_attempts": 1})

    # Every stage should fall back gracefully rather than raising.
    assert result["embedded_report"]["open_questions"]
    assert "Structured output validation failed" in result["embedded_report"]["open_questions"][0]
    assert result["final"]["disposition"] == "revise"

    fallback_events = [ev for ev in result["events"] if ev["type"] == "reviewer_fallback"]
    assert len(fallback_events) == 3  # embedded, backend, langgraph all fell back


def test_graph_is_deterministically_reviewing_the_fixed_proposal():
    graph = build_graph(llm_factory=FakeStructuredChatModel)

    result = graph.invoke({"run_id": "test-run-3", "notes": "focus on security"})

    assert "ESP32-S3-BOX-3" in result["proposal"]
    assert "FastAPI" in result["proposal"]
