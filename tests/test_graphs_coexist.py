import json
from pathlib import Path

from app.graph.graph import graph as design_review_graph
from app.voice.graph import graph as voice_note_graph
from tests.fakes import FakeStructuredChatModel
from tests.voice_fakes import VOICE_RESPONSES_BY_TITLE


def test_langgraph_json_registers_both_graphs():
    config = json.loads((Path(__file__).resolve().parent.parent / "langgraph.json").read_text())
    assert config["graphs"]["design_review"] == "./app/graph/graph.py:graph"
    assert config["graphs"]["voice_note_processor"] == "./app/voice/graph.py:graph"


def test_both_compiled_graphs_are_distinct_and_independently_invokable():
    assert design_review_graph is not voice_note_graph
    assert set(design_review_graph.nodes.keys()) != set(voice_note_graph.nodes.keys())
    assert "chair" in design_review_graph.nodes
    assert "finalize" in voice_note_graph.nodes


def test_running_the_voice_graph_does_not_affect_the_design_review_graph():
    from app.graph.graph import build_graph as build_design_review_graph

    fake_design = build_design_review_graph(llm_factory=FakeStructuredChatModel)
    fake_voice = build_voice_graph_with_fakes()

    voice_result = fake_voice.invoke({"note_id": "coexist-1", "transcript": "Order more filament."})
    design_result = fake_design.invoke({"run_id": "coexist-1"})

    assert voice_result["final"]["status"] == "completed"
    assert design_result["final"]["disposition"] == "approve_with_changes"


def build_voice_graph_with_fakes():
    from app.voice.graph import build_graph as build_voice_graph

    return build_voice_graph(llm_factory=lambda: FakeStructuredChatModel(VOICE_RESPONSES_BY_TITLE))
