from typing import Literal, Optional

from pydantic import BaseModel, Field


class InterpretedNote(BaseModel):
    title: str
    summary: str = Field(description="Concise summary of the note")
    category: str
    topics: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class PossibleActions(BaseModel):
    """Suggestions only - nothing here is ever executed automatically."""

    possible_next_actions: list[str] = Field(default_factory=list)
    decisions_detected: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    needs_mike_review: bool = Field(
        description="Whether this note looks important enough for Mike to review directly"
    )


GroundingIssueCategory = Literal[
    "unsupported_claim",
    "invented_fact",
    "overstated_certainty",
    "discarded_constraint",
    "unauthorized_action",
]


class GroundingIssue(BaseModel):
    category: GroundingIssueCategory
    description: str


class GroundingReport(BaseModel):
    grounded: bool = Field(description="True only if the note and actions are fully supported by the transcript")
    issues: list[GroundingIssue] = Field(default_factory=list)
    summary: str


class FinalNoteResult(BaseModel):
    status: Literal["completed", "completed_with_warnings", "failed"]
    grounded: Optional[bool] = None
    correction_attempts: int = 0
    warnings: list[str] = Field(default_factory=list)


class AudioClarityMessage(BaseModel):
    """Output of the shared audio-reliability check - see
    app/voice/audio_reliability.py. Only ever invoked once transcription
    confidence has already been measured below threshold, so there's no
    separate "is this worth mentioning" judgment call here (unlike the old
    NotificationVerification this replaces) - low confidence alone is
    reason enough to ask for a repeat. The model's only job is to phrase
    that naturally, referencing the best-guess content only if it's legible
    enough to be useful."""

    spoken_message: str = Field(
        description="Short (1-2 sentence) natural spoken message telling "
        "Mike a recording came in that wasn't caught clearly, asking him to "
        "repeat or clarify it. Reference the best-guess transcript only if "
        "doing so would actually help him recognize what he said; otherwise "
        "just ask him to say it again."
    )
