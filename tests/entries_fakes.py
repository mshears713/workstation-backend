from app.entries.schemas import EntryArchitectResult, SemanticVerification, SourceFields, VanBuildLogFields

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
