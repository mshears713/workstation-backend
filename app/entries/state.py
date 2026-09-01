import operator
from typing import Annotated, Optional

from typing_extensions import TypedDict


class EntryState(TypedDict, total=False):
    # Input
    transcript: str
    max_attempts: int
    # Mike's VAN1/VAN2/GEN/NONE button selection from the device, if any -
    # "van1"/"van2"/"general"/"none"/None (older firmware that doesn't send
    # the field at all). See prompts.build_entry_architect_user_prompt.
    project_hint: Optional[str]

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
