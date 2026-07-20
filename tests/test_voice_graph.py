from app.voice.graph import build_graph
from tests.fakes import FakeAlwaysInvalidChatModel, FakeStructuredChatModel
from tests.voice_fakes import VOICE_RESPONSES_BY_TITLE, FakeGroundingCorrectionChatModel


def _fake_voice_model():
    return FakeStructuredChatModel(VOICE_RESPONSES_BY_TITLE)


def test_voice_graph_happy_path():
    graph = build_graph(llm_factory=_fake_voice_model)

    result = graph.invoke(
        {"note_id": "test-note-1", "transcript": "Remember to order more filament."}
    )

    assert result["transcript_valid"] is True
    assert result["interpreted_note"]["category"] == "task"
    assert result["possible_actions"]["needs_mike_review"] is False
    assert result["grounding_report"]["grounded"] is True
    assert result["final"]["status"] == "completed"
    assert result["final"]["grounded"] is True
    assert result["final"]["correction_attempts"] == 0

    node_names = {ev["node"] for ev in result["events"]}
    assert node_names == {
        "validate_transcript",
        "interpret_note",
        "extract_actions",
        "grounding_review",
        "finalize",
    }


def test_voice_graph_invalid_transcript_short_circuits_to_finalize():
    graph = build_graph(llm_factory=_fake_voice_model)

    result = graph.invoke({"note_id": "test-note-2", "transcript": "   "})

    assert result["transcript_valid"] is False
    assert result.get("interpreted_note") is None
    assert result["final"]["status"] == "failed"

    node_names = {ev["node"] for ev in result["events"]}
    assert node_names == {"validate_transcript", "finalize"}


def test_voice_graph_bounded_correction_loop_on_ungrounded_result():
    # Must share one instance across node calls - its correction counter
    # needs to persist across the interpret/extract/grounding sequence.
    fake_model = FakeGroundingCorrectionChatModel()
    graph = build_graph(llm_factory=lambda: fake_model)

    result = graph.invoke(
        {"note_id": "test-note-3", "transcript": "Remember to order more filament."}
    )

    assert result["final"]["correction_attempts"] == 1
    assert result["final"]["grounded"] is True
    assert result["final"]["status"] == "completed"

    # interpret_note/extract_actions/grounding_review each ran twice (initial + one correction)
    interpret_events = [ev for ev in result["events"] if ev["node"] == "interpret_note"]
    grounding_events = [ev for ev in result["events"] if ev["node"] == "grounding_review"]
    assert len(interpret_events) == 4  # invoked+complete, twice
    assert len(grounding_events) == 4


def test_voice_graph_bounded_fallback_when_model_never_produces_valid_json():
    graph = build_graph(llm_factory=FakeAlwaysInvalidChatModel)

    result = graph.invoke(
        {"note_id": "test-note-4", "transcript": "Remember to order more filament.", "max_attempts": 1}
    )

    # Every LLM stage falls back gracefully rather than raising, and the
    # bounded correction loop still terminates after exactly one attempt.
    assert result["final"]["correction_attempts"] == 1
    assert result["final"]["status"] == "completed_with_warnings"
    assert result["final"]["grounded"] is False
    assert any("interpretation failed" in w.lower() for w in result["final"]["warnings"])
