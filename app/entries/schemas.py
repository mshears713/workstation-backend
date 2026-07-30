from typing import Literal, Optional

from pydantic import BaseModel, Field

# Every Literal set below is copied verbatim from the live Notion schema
# (queried via the data source API, not guessed) - see
# app/integrations/notion_client.py's create_source_page()/
# create_van_build_log_page() for where these get written back. Pydantic
# validating against these on every entry-architect response is the
# "deterministic Python checks permitted select values" step - an invalid
# value fails model_validate() inside invoke_structured() and triggers its
# existing retry-on-invalid-output loop, no separate validation pass needed.

SourceType = Literal[
    "Voice Note", "ChatGPT", "Web Page", "Article", "Video", "Datasheet",
    "Product Page", "Book", "Email", "Photo / Screenshot", "Test / Measurement",
    "Personal Note", "Other",
]
SourceStatus = Literal[
    "Captured", "Open", "Promotion Candidate", "Promoted", "Superseded", "Archived", "Discarded",
]
PromotionReadiness = Literal["Raw", "Candidate", "Ready for Review", "Promoted", "Not Useful"]
CaptureConfidence = Literal["Low", "Medium", "High"]

VanOrScope = Literal["Van 1", "Van 2", "Shared / Future Build", "General Van Flipping"]
VanLogStatus = Literal["Logged", "Open", "Resolved", "Superseded"]
Allocation = Literal["Confirmed", "Estimated", "Shared", "Not Applicable"]
EntryType = Literal[
    "Build Note", "Labor", "Purchase / Receipt", "Material Use", "Measurement",
    "Issue / Discovery", "Decision", "Design / CAD", "Photo / Media Update",
    "Maintenance / Repair", "Next Action",
]
Workstream = Literal[
    "Baseline Inspection", "Deconstruction", "Cleaning & Preparation",
    "Mechanical & Roadworthiness", "Exterior & Body", "Roof & Ventilation", "Flooring",
    "Walls / Panels & Insulation", "Electrical", "Lighting", "Cabinetry & Storage",
    "Finish & Comfort", "Documentation & Buyer Handoff", "Listing & Sale",
    "Business / General", "Unclassified",
]
MediaStatus = Literal["None", "Waiting for Attachment", "Attached", "Reviewed"]
DocumentationValue = Literal["Unknown", "Routine", "Useful", "High Value"]


class SourceFields(BaseModel):
    """Maps to the Sources data source's LLM-authored properties. Source
    Type ("Voice Note"), Status ("Captured"), Promotion Readiness ("Raw"),
    and Capture Confidence (from the audio-reliability score, not the LLM)
    are all set deterministically by entries_runner.py, not here - see
    notion_client.create_source_page()."""

    name: str = Field(description="Short descriptive title, e.g. 'Van 1 electrical: ran 12V line to fridge'")
    cue: str = Field(description="A few-word memory cue/tag for finding this source later")
    summary: str = Field(description="1-3 sentence summary of what was captured")
    observation: str = Field(description="The fuller observation/detail, closer to the raw content than summary")
    open_questions: str = Field(default="", description="Anything left unclear or needing follow-up - empty if none")


class VanBuildLogFields(BaseModel):
    """Maps to the Van Build Log data source's LLM-authored properties.
    Media Status ("None" - this pipeline has no photo capability), Status
    ("Logged"), and Date are set deterministically by entries_runner.py, not
    here - see notion_client.create_van_build_log_page()."""

    name: str = Field(description="Short title for this build-log entry")
    van_or_scope: VanOrScope
    entry_type: EntryType
    workstream: Workstream
    module_or_component: str = Field(default="", description="Specific component/system this concerns, free text")
    amount: Optional[float] = Field(default=None, ge=0, description="Dollar amount if a cost was mentioned, else null")
    labor_hours: Optional[float] = Field(default=None, ge=0, description="Hours of labor if mentioned, else null")
    allocation: Allocation
    documentation_value: DocumentationValue = "Unknown"
    summary: str = Field(description="1-3 sentence summary of the build-log entry")


class EntryArchitectResult(BaseModel):
    """One structured call covers both records - see
    app/entries/entry_architect.py. van_build_log is null only when the
    transcript genuinely doesn't support a responsible build-log entry (not
    actually about van-build work, or too vague to categorize at all) - the
    Source is still always produced either way, per the directive to
    preserve raw evidence even when a build-log entry can't be made."""

    source: SourceFields
    van_build_log: Optional[VanBuildLogFields] = Field(
        default=None,
        description="Null only if there isn't enough information to responsibly "
        "create a van-build-log entry from this transcript",
    )
    clarification_message: str = Field(
        default="",
        description="Only used when van_build_log is null: a short spoken message "
        "explaining a build-log entry couldn't be created, reading back the "
        "best-guess content so Mike can clarify. Empty when van_build_log is present.",
    )


class SemanticVerification(BaseModel):
    """Output of the independent post-creation verifier - see
    app/entries/verifier.py. Judges faithfulness/usefulness of the records
    against the transcript, never audio clarity (already handled upstream)
    and never perfectionism (empty optional fields or an imperfect title are
    not grounds for notification_required=True)."""

    confidence: Literal["high", "medium", "low"]
    notification_required: bool = Field(
        description="True only if one short spoken question would materially "
        "improve or correct the record - not for cosmetic imperfections"
    )
    reason: str = Field(description="Brief internal reasoning - never spoken aloud")
    spoken_question: str = Field(
        default="", description="One short natural spoken question, only if notification_required is true"
    )
