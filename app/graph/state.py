import operator
from typing import Annotated, Optional

from typing_extensions import TypedDict


class ReviewState(TypedDict, total=False):
    # Input
    run_id: Optional[str]
    notes: Optional[str]

    # Set by the intake node
    proposal: str
    max_attempts: int

    # Reviewer outputs (dicts validated against app.graph.schemas.ReviewerReport)
    embedded_report: Optional[dict]
    backend_report: Optional[dict]
    langgraph_report: Optional[dict]

    # Audit + final outputs
    audit_report: Optional[dict]
    final: Optional[dict]

    # Observability log, accumulated across nodes
    events: Annotated[list[dict], operator.add]
