import operator
from typing import Annotated, Optional

from typing_extensions import TypedDict


class EntryState(TypedDict, total=False):
    # Input
    transcript: str
    max_attempts: int

    # Set by draft_entry
    draft: Optional[dict]

    # Set by review_draft
    review: Optional[dict]
    correction_attempts: int

    # Set by finalize
    final: Optional[dict]

    # Observability + bounded-handling log, accumulated across nodes -
    # same shape as VoiceNoteState's own events/warnings.
    events: Annotated[list[dict], operator.add]
    warnings: Annotated[list[str], operator.add]
