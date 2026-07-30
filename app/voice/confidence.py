import math
from typing import Optional

from app.voice.transcription import TranscriptionResult


def transcription_confidence(result: TranscriptionResult) -> Optional[float]:
    """Turns a TranscriptionResult's per-token logprobs into a single 0-1
    confidence score (mean token probability, via math.exp on the mean
    logprob - not the min, since one rough word in an otherwise clear note
    shouldn't dominate the score the way it would dominate a min).

    Returns None if no logprobs are available (model doesn't support them,
    or the transcript was empty) - callers should treat None as "unknown,
    not low confidence" rather than failing closed, since only having
    logprobs on some models is expected, not an error condition.
    """
    if not result.logprobs:
        return None

    total = sum(entry["logprob"] for entry in result.logprobs)
    mean_logprob = total / len(result.logprobs)
    return max(0.0, min(1.0, math.exp(mean_logprob)))


def is_low_confidence(result: TranscriptionResult, threshold: float) -> bool:
    """False for both "confidently transcribed" and "confidence unknown" -
    see transcription_confidence()'s None case."""
    score = transcription_confidence(result)
    return score is not None and score < threshold
