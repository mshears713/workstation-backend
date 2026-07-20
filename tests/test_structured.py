from app.graph.schemas import ReviewerReport
from app.graph.structured import invoke_structured
from tests.fakes import (
    VALID_REVIEWER_REPORT,
    FakeAlwaysInvalidChatModel,
    FakeFlakyChatModel,
    FakeStructuredChatModel,
)


def test_invoke_structured_succeeds_on_first_valid_response():
    llm = FakeStructuredChatModel({"ReviewerReport": VALID_REVIEWER_REPORT})

    result = invoke_structured(llm, "persona", "prompt", ReviewerReport, max_attempts=2)

    assert result.model is not None
    assert result.error is None
    assert result.attempts == 1
    assert result.model.role == "Fake Reviewer"


def test_invoke_structured_recovers_after_one_invalid_attempt():
    llm = FakeFlakyChatModel()

    result = invoke_structured(llm, "persona", "prompt", ReviewerReport, max_attempts=2)

    assert result.model is not None
    assert result.attempts == 2


def test_invoke_structured_bounded_when_always_invalid():
    llm = FakeAlwaysInvalidChatModel()

    result = invoke_structured(llm, "persona", "prompt", ReviewerReport, max_attempts=2)

    assert result.model is None
    assert result.error is not None
    assert result.attempts == 2
