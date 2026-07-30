from app.voice.audio_reliability import check_audio_reliability
from app.voice.transcription import TranscriptionResult
from tests.fakes import FakeAlwaysInvalidChatModel, FakeStructuredChatModel
from tests.voice_fakes import FAKE_SPOKEN_MESSAGE, LOW_CONFIDENCE_LOGPROBS


def _clear_result(logprobs=None):
    return TranscriptionResult(text="clear transcript", language="en", model="fake", raw={}, logprobs=logprobs)


def test_high_confidence_is_reliable_without_any_llm_call():
    def explode():
        raise AssertionError("llm_factory should never be called when confidence is high")

    result = check_audio_reliability(
        _clear_result(logprobs=[{"token": "hi", "bytes": [], "logprob": -0.001}]),
        threshold=0.55,
        llm_factory=explode,
    )
    assert result.reliable is True
    assert result.confidence > 0.9


def test_unknown_confidence_is_reliable_without_any_llm_call():
    def explode():
        raise AssertionError("llm_factory should never be called when confidence is unknown")

    result = check_audio_reliability(_clear_result(logprobs=None), threshold=0.55, llm_factory=explode)
    assert result.reliable is True
    assert result.confidence is None


def test_low_confidence_drafts_a_spoken_message():
    from app.voice.schemas import AudioClarityMessage

    canned = AudioClarityMessage(spoken_message=FAKE_SPOKEN_MESSAGE).model_dump_json()
    fake_model = FakeStructuredChatModel({"AudioClarityMessage": canned})

    result = check_audio_reliability(
        _clear_result(logprobs=LOW_CONFIDENCE_LOGPROBS), threshold=0.55, llm_factory=lambda: fake_model
    )
    assert result.reliable is False
    assert result.confidence < 0.05
    assert result.spoken_message == FAKE_SPOKEN_MESSAGE


def test_low_confidence_falls_back_to_a_generic_message_on_persistent_llm_failure():
    result = check_audio_reliability(
        _clear_result(logprobs=LOW_CONFIDENCE_LOGPROBS),
        threshold=0.55,
        llm_factory=lambda: FakeAlwaysInvalidChatModel(),
        max_attempts=2,
    )
    assert result.reliable is False
    assert result.spoken_message  # non-empty fallback, not a crash
