from langchain_core.messages import AIMessage

from app.voice.audio_reliability import AudioReliabilityResult
from app.voice.schemas import GroundingReport, InterpretedNote, PossibleActions
from app.voice.transcription import TranscriptionResult
from tests.fakes import _TITLE_RE

VALID_INTERPRETED_NOTE = InterpretedNote(
    title="Order more filament and follow up with Mike",
    summary="A fake but structurally valid interpreted note for testing.",
    category="task",
    topics=["3d printing", "supplies"],
    key_points=["Need more filament", "Follow up with Mike about the van door hinge"],
    open_questions=["Which filament color?"],
).model_dump_json()

VALID_POSSIBLE_ACTIONS = PossibleActions(
    possible_next_actions=["Order filament", "Message Mike about the hinge"],
    decisions_detected=[],
    follow_up_questions=["What filament color is needed?"],
    needs_mike_review=False,
).model_dump_json()

VALID_GROUNDING_REPORT = GroundingReport(
    grounded=True,
    issues=[],
    summary="Fake grounding review: note is faithful to the transcript.",
).model_dump_json()

UNGROUNDED_REPORT = GroundingReport(
    grounded=False,
    issues=[
        {
            "category": "invented_fact",
            "description": "Fake issue used only to exercise the correction loop in tests.",
        }
    ],
    summary="Fake grounding review: flags an issue to trigger one correction pass.",
).model_dump_json()

VOICE_RESPONSES_BY_TITLE = {
    "InterpretedNote": VALID_INTERPRETED_NOTE,
    "PossibleActions": VALID_POSSIBLE_ACTIONS,
    "GroundingReport": VALID_GROUNDING_REPORT,
}


class FakeGroundingCorrectionChatModel:
    """Returns grounded=false on the first GroundingReport call, grounded=true
    on the next, to exercise the bounded one-shot correction loop. Other
    schemas always return their valid canned response."""

    def __init__(self):
        self._grounding_calls = 0

    def invoke(self, messages):
        system_content = messages[0].content if messages else ""
        match = _TITLE_RE.search(system_content)
        title = match.group(1) if match else None
        if title == "GroundingReport":
            self._grounding_calls += 1
            return AIMessage(content=UNGROUNDED_REPORT if self._grounding_calls == 1 else VALID_GROUNDING_REPORT)
        content = VOICE_RESPONSES_BY_TITLE.get(title)
        if content is None:
            raise RuntimeError(f"FakeGroundingCorrectionChatModel: no canned response for title={title!r}")
        return AIMessage(content=content)


def fake_transcribe_audio(audio_path):
    return TranscriptionResult(
        text=(
            "Remember to order more filament and follow up with Mike about the "
            "van door hinge next week."
        ),
        language="en",
        model="fake-transcribe-model",
        raw={"text": "fake transcription raw payload"},
    )


def fake_transcribe_audio_empty(audio_path):
    return TranscriptionResult(text="", language="en", model="fake-transcribe-model", raw={"text": ""})


def fake_transcribe_audio_failing(audio_path):
    raise RuntimeError("Simulated transcription failure for testing.")


def fake_create_voice_inbox_page(name, captured_at, transcript, related_project_page_id=None):
    return {"id": "fake-notion-page-id", "url": "https://notion.so/fake-notion-page-id"}


def fake_create_voice_inbox_page_failing(name, captured_at, transcript, related_project_page_id=None):
    raise RuntimeError("Simulated Notion page creation failure for testing.")


# Mean logprob of -5.0 -> math.exp(-5.0) =~ 0.0067, comfortably below the
# default audio_confidence_threshold (0.55) - used to exercise the
# unreliable-audio -> notification branch shared by voice_inbox_runner.py
# (NOTE) and entries_runner.py (GO).
LOW_CONFIDENCE_LOGPROBS = [{"token": "mumble", "bytes": [], "logprob": -5.0}]


def fake_transcribe_audio_low_confidence(audio_path):
    return TranscriptionResult(
        text="something about the the van maybe hinge thing",
        language="en",
        model="fake-transcribe-model",
        raw={"text": "fake transcription raw payload"},
        logprobs=LOW_CONFIDENCE_LOGPROBS,
    )


FAKE_SPOKEN_MESSAGE = "I didn't catch that recording clearly - could you say that again?"


def fake_check_audio_reliability_unreliable(transcript_result, threshold, **kwargs):
    return AudioReliabilityResult(reliable=False, confidence=0.02, spoken_message=FAKE_SPOKEN_MESSAGE)


def fake_check_audio_reliability_reliable(transcript_result, threshold, **kwargs):
    return AudioReliabilityResult(reliable=True, confidence=0.99)


def fake_check_audio_reliability_raising(transcript_result, threshold, **kwargs):
    raise RuntimeError("Simulated audio-reliability check failure for testing.")


FAKE_TTS_AUDIO_BYTES = b"FAKE-PCM-AUDIO-BYTES"


def fake_synthesize_speech(text):
    return FAKE_TTS_AUDIO_BYTES


def fake_synthesize_speech_raising(text):
    raise RuntimeError("Simulated TTS failure for testing.")
