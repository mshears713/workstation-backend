import operator
from typing import Annotated, Optional

from typing_extensions import TypedDict


class VoiceNoteState(TypedDict, total=False):
    # Input
    note_id: Optional[str]
    request_id: Optional[str]
    source: Optional[str]
    transcript: str
    max_attempts: int

    # Set by validate_transcript
    transcript_valid: bool

    # Set by interpret_note / extract_actions / grounding_review
    interpreted_note: Optional[dict]
    possible_actions: Optional[dict]
    grounding_report: Optional[dict]
    correction_attempts: int

    # Set by finalize
    final: Optional[dict]

    # Observability + bounded-handling log, accumulated across nodes
    events: Annotated[list[dict], operator.add]
    warnings: Annotated[list[str], operator.add]
