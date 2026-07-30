from langchain_core.messages import AIMessage

from app.entries.schemas import EntryArchitectResult, EntryDraftReview, SemanticVerification, SourceFields, VanBuildLogFields
from tests.fakes import _TITLE_RE

VALID_SOURCE_FIELDS = SourceFields(
    name="Van 1 electrical: ran 12V line to fridge",
    cue="fridge 12V line",
    summary="Ran a 12V line to the fridge.",
    observation="Mike described routing 12 gauge wire from the electrical panel to the fridge location.",
    open_questions="",
)

VALID_VAN_BUILD_LOG_FIELDS = VanBuildLogFields(
    name="Ran 12V line to fridge",
    van_or_scope="Van 1",
    entry_type="Build Note",
    workstream="Electrical",
    module_or_component="12V wiring",
    amount=None,
    labor_hours=1.0,
    allocation="Not Applicable",
    documentation_value="Routine",
    summary="Ran a 12V line to the fridge.",
)

CLARIFICATION_MESSAGE = (
    "I logged a source but wasn't sure how to turn it into a build-log entry - "
    "could you clarify what van and workstream this was about?"
)

SEMANTIC_SPOKEN_QUESTION = "Which van was this for - Van 1 or Van 2?"


def fake_run_entry_architect_confident(transcript, **kwargs):
    return EntryArchitectResult(source=VALID_SOURCE_FIELDS, van_build_log=VALID_VAN_BUILD_LOG_FIELDS, clarification_message="")


def fake_run_entry_architect_no_van_log(transcript, **kwargs):
    return EntryArchitectResult(source=VALID_SOURCE_FIELDS, van_build_log=None, clarification_message=CLARIFICATION_MESSAGE)


def fake_run_entry_architect_none(transcript, **kwargs):
    return None


def _must_not_be_called(name):
    def _fn(*args, **kwargs):
        raise AssertionError(f"{name} must not be called on this path")

    return _fn


fake_run_entry_architect_must_not_be_called = _must_not_be_called("run_entry_architect")
fake_run_semantic_verifier_must_not_be_called = _must_not_be_called("run_semantic_verifier")


FAKE_SOURCE_PAGE = {"id": "source-page-fake-1", "url": "https://notion.so/source-page-fake-1"}
FAKE_VAN_BUILD_LOG_PAGE = {"id": "vbl-page-fake-1", "url": "https://notion.so/vbl-page-fake-1"}


def fake_create_source_page(**kwargs):
    return FAKE_SOURCE_PAGE


def fake_create_source_page_raising(**kwargs):
    raise RuntimeError("Simulated Notion Source creation failure for testing.")


def fake_create_van_build_log_page(**kwargs):
    return FAKE_VAN_BUILD_LOG_PAGE


def fake_run_semantic_verifier_high(transcript, source_fields, van_build_log_fields, **kwargs):
    return SemanticVerification(confidence="high", notification_required=False, reason="Fake: faithful and clear.")


def fake_run_semantic_verifier_low(transcript, source_fields, van_build_log_fields, **kwargs):
    return SemanticVerification(
        confidence="low",
        notification_required=True,
        reason="Fake: van scope unclear.",
        spoken_question=SEMANTIC_SPOKEN_QUESTION,
    )


# ---- Fakes for app/entries/graph.py's llm_factory (chat-model level, not
# the run_entry_architect()-level fakes above) - same
# "canned JSON keyed by schema title" convention as tests/fakes.py and
# tests/voice_fakes.py. ----

VALID_ENTRY_ARCHITECT_RESULT = EntryArchitectResult(
    source=VALID_SOURCE_FIELDS, van_build_log=VALID_VAN_BUILD_LOG_FIELDS, clarification_message=""
).model_dump_json()

VALID_ENTRY_DRAFT_REVIEW = EntryDraftReview(
    grounded=True, issues=[], summary="Fake review: draft is faithful to the transcript."
).model_dump_json()

UNGROUNDED_ENTRY_DRAFT_REVIEW = EntryDraftReview(
    grounded=False,
    issues=["Fake issue used only to exercise the correction loop in tests."],
    summary="Fake review: flags an issue to trigger one correction pass.",
).model_dump_json()

ENTRIES_RESPONSES_BY_TITLE = {
    "EntryArchitectResult": VALID_ENTRY_ARCHITECT_RESULT,
    "EntryDraftReview": VALID_ENTRY_DRAFT_REVIEW,
}


class FakeEntryDraftCorrectionChatModel:
    """Returns grounded=false on the first EntryDraftReview call, grounded=true
    on the next, to exercise the bounded one-shot correction loop - same
    shape as tests/voice_fakes.py's FakeGroundingCorrectionChatModel."""

    def __init__(self):
        self._review_calls = 0

    def invoke(self, messages):
        system_content = messages[0].content if messages else ""
        match = _TITLE_RE.search(system_content)
        title = match.group(1) if match else None
        if title == "EntryDraftReview":
            self._review_calls += 1
            return AIMessage(
                content=UNGROUNDED_ENTRY_DRAFT_REVIEW if self._review_calls == 1 else VALID_ENTRY_DRAFT_REVIEW
            )
        content = ENTRIES_RESPONSES_BY_TITLE.get(title)
        if content is None:
            raise RuntimeError(f"FakeEntryDraftCorrectionChatModel: no canned response for title={title!r}")
        return AIMessage(content=content)
