from app.entries.graph import build_graph
from tests.entries_fakes import ENTRIES_RESPONSES_BY_TITLE, FakeEntryDraftCorrectionChatModel
from tests.fakes import FakeAlwaysInvalidChatModel, FakeStructuredChatModel


def _fake_entries_model():
    return FakeStructuredChatModel(ENTRIES_RESPONSES_BY_TITLE)


def test_entries_graph_happy_path():
    graph = build_graph(llm_factory=_fake_entries_model)

    result = graph.invoke({"transcript": "Ran a 12V line to the fridge in Van 1, took about an hour."})

    assert result["draft"]["van_build_log"]["van_or_scope"] == "Van 1"
    assert result["final"] == result["draft"]
    assert result["correction_attempts"] == 0

    node_names = {ev["node"] for ev in result["events"]}
    assert node_names == {"draft_entry", "review_draft", "finalize"}


def test_entries_graph_bounded_correction_loop_on_ungrounded_draft():
    # Must share one instance across node calls - its correction counter
    # needs to persist across the draft/review sequence.
    fake_model = FakeEntryDraftCorrectionChatModel()
    graph = build_graph(llm_factory=lambda: fake_model)

    result = graph.invoke({"transcript": "Ran a 12V line to the fridge in Van 1, took about an hour."})

    # Bounded to exactly one correction pass, same assertion shape as
    # test_voice_graph.py's own correction-loop test.
    assert result["correction_attempts"] == 1
    assert result["review"]["grounded"] is True
    assert result["final"] is not None

    node_names = {ev["node"] for ev in result["events"]}
    assert node_names == {"draft_entry", "review_draft", "finalize"}


def test_entries_graph_falls_back_to_minimal_source_on_persistent_invalid_output():
    graph = build_graph(llm_factory=lambda: FakeAlwaysInvalidChatModel())

    result = graph.invoke({"transcript": "garbled transcript"})

    # draft_entry's own fallback (invoke_structured exhausted its JSON-retry
    # attempts) still produces a usable (if minimal) Source, and the graph
    # still reaches finalize rather than crashing.
    assert result["final"] is not None
    assert result["final"]["van_build_log"] is None
    assert result["final"]["source"]["observation"] == ""
    assert any(w for w in result["warnings"])
