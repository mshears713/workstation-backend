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
