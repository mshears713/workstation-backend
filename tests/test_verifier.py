from tests.fakes import FakeAlwaysInvalidChatModel, FakeStructuredChatModel
from tests.voice_fakes import VALID_NOTIFICATION_VERIFICATION, FAKE_SPOKEN_MESSAGE

from app.voice.verifier import verify_low_confidence_transcript


def test_verifier_returns_structured_decision():
    fake_model = FakeStructuredChatModel({"NotificationVerification": VALID_NOTIFICATION_VERIFICATION})

    result = verify_low_confidence_transcript(
        "something about the the van maybe hinge thing", 0.03, llm_factory=lambda: fake_model
    )

    assert result.worth_notifying is True
    assert result.spoken_message == FAKE_SPOKEN_MESSAGE


def test_verifier_fails_closed_on_persistently_invalid_output():
    fake_model = FakeAlwaysInvalidChatModel()

    result = verify_low_confidence_transcript(
        "garbled transcript", 0.02, llm_factory=lambda: fake_model, max_attempts=2
    )

    assert result.worth_notifying is False
    assert result.spoken_message == ""
    assert "failed" in result.reasoning
