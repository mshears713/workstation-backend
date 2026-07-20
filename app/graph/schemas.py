from typing import Literal

from pydantic import BaseModel, Field


class ReviewFinding(BaseModel):
    severity: Literal["blocker", "major", "minor", "info"]
    title: str
    description: str
    recommendation: str


class ReviewerReport(BaseModel):
    role: str = Field(description="The reviewer role that produced this report")
    summary: str = Field(description="2-4 sentence overview of the review")
    findings: list[ReviewFinding] = Field(default_factory=list)
    assumptions: list[str] = Field(
        default_factory=list,
        description="Assumptions this reviewer made that are not stated in the proposal",
    )
    open_questions: list[str] = Field(default_factory=list)


class AuditFinding(BaseModel):
    type: Literal["conflict", "gap", "unstated_assumption"]
    description: str
    involved_roles: list[str] = Field(default_factory=list)


class AuditReport(BaseModel):
    summary: str
    conflicts: list[AuditFinding] = Field(default_factory=list)
    cross_cutting_risks: list[str] = Field(default_factory=list)


class FinalDisposition(BaseModel):
    disposition: Literal["approve", "approve_with_changes", "revise", "reject"]
    rationale: str
    next_steps: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
