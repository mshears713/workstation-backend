from app.voice.confidence import is_low_confidence, transcription_confidence
from app.voice.transcription import TranscriptionResult


def _result(logprobs=None):
    return TranscriptionResult(text="hello", language="en", model="fake", raw={}, logprobs=logprobs)


def test_no_logprobs_returns_none_confidence():
    assert transcription_confidence(_result(logprobs=None)) is None


def test_high_logprobs_yield_high_confidence():
    result = _result(logprobs=[{"token": "hi", "bytes": [], "logprob": -0.01}])
    score = transcription_confidence(result)
    assert score is not None
    assert score > 0.98


def test_low_logprobs_yield_low_confidence():
    result = _result(logprobs=[{"token": "mumble", "bytes": [], "logprob": -5.0}])
    score = transcription_confidence(result)
    assert score is not None
    assert score < 0.05


def test_unknown_confidence_is_not_low_confidence():
    # None (unknown) must not be treated as low - see confidence.py's
    # docstring: only having logprobs on some models is expected, not an
    # error condition that should force every item through the verifier.
    assert is_low_confidence(_result(logprobs=None), threshold=0.9) is False


def test_is_low_confidence_respects_threshold():
    result = _result(logprobs=[{"token": "mumble", "bytes": [], "logprob": -5.0}])
    assert is_low_confidence(result, threshold=0.55) is True
    assert is_low_confidence(result, threshold=0.001) is False
