from datetime import datetime, timezone

from app.entries import prompts
from app.entries.schemas import EntryArchitectResult, EntryDraftReview
from app.graph.structured import invoke_structured
from app.entries.state import EntryState
from app.voice.nodes import LlmFactory

DEFAULT_MAX_ATTEMPTS = 2

# Fallback draft used only if the LLM never produces valid structured
# output at all after every JSON-validity retry (see make_draft_entry_node) -
# distinct from the LangGraph-level RetryPolicy (see graph.py), which
# handles transient upstream errors; this handles a persistently malformed
# response. Preserves the raw transcript as evidence rather than losing it,
# same reasoning entries_runner.py's own fallback documents.
_FALLBACK_SOURCE = {
    "name": "",
    "cue": "",
    "summary": "",
    "observation": "",
    "open_questions": "Entry architect could not process this transcript automatically.",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event(node: str, kind: str, **extra) -> dict:
    return {"ts": _now(), "node": node, "type": kind, **extra}


def _issues_as_feedback(review: dict) -> str:
    issues = review.get("issues", [])
    if issues:
        return "; ".join(issues)
    return review.get("summary", "")


def make_draft_entry_node(llm_factory: LlmFactory):
    def node(state: EntryState) -> dict:
        llm = llm_factory()
        max_attempts = state.get("max_attempts", DEFAULT_MAX_ATTEMPTS)

        is_correction = state.get("review") is not None
        correction_attempts = state.get("correction_attempts", 0)
        feedback = None
        if is_correction:
            correction_attempts += 1
            feedback = _issues_as_feedback(state["review"])

        user_prompt = prompts.build_entry_architect_user_prompt(
            state["transcript"], feedback, state.get("project_hint")
        )
        result = invoke_structured(
            llm, prompts.ENTRY_ARCHITECT_PERSONA, user_prompt, EntryArchitectResult, max_attempts
        )

        events = [_event("draft_entry", "invoked", attempts=result.attempts, is_correction=is_correction)]
        warnings: list[str] = []
        if result.model is None:
            draft = dict(_FALLBACK_SOURCE)
            events.append(_event("draft_entry", "fallback", error=result.error))
            warnings.append(f"Entry architect failed to produce valid output: {result.error}")
            draft_out = {"source": draft, "van_build_log": None, "clarification_message": (
                "I have a new voice capture but couldn't process it automatically - "
                "you may want to review it directly."
            )}
        else:
            draft_out = result.model.model_dump()
            events.append(_event("draft_entry", "complete", has_van_build_log=draft_out["van_build_log"] is not None))

        return {
            "draft": draft_out,
            "correction_attempts": correction_attempts,
            "events": events,
            "warnings": warnings,
        }

    return node


def make_review_draft_node(llm_factory: LlmFactory):
    def node(state: EntryState) -> dict:
        llm = llm_factory()
        max_attempts = state.get("max_attempts", DEFAULT_MAX_ATTEMPTS)

        user_prompt = prompts.build_draft_review_user_prompt(
            state["transcript"], state.get("draft") or {}, state.get("project_hint")
        )
        result = invoke_structured(
            llm, prompts.DRAFT_REVIEW_PERSONA, user_prompt, EntryDraftReview, max_attempts
        )

        events = [_event("review_draft", "invoked", attempts=result.attempts)]
        warnings: list[str] = []
        if result.model is None:
            # Fails open (grounded=true): a reviewer that can't itself
            # produce valid output shouldn't force a correction loop no one
            # can actually resolve - the draft passes through as-is, same
            # "don't let a meta-failure block the primary output" reasoning
            # entries_runner.py's own architect-failure fallback uses.
            review = EntryDraftReview(grounded=True, issues=[], summary="Draft review unavailable - passed through.")
            events.append(_event("review_draft", "fallback", error=result.error))
            warnings.append(f"Draft review failed to produce valid output: {result.error}")
        else:
            review = result.model
            events.append(_event("review_draft", "complete", grounded=review.grounded))

        return {
            "review": review.model_dump(),
            "events": events,
            "warnings": warnings,
        }

    return node


def route_after_review(state: EntryState) -> str:
    review = state.get("review") or {}
    grounded = review.get("grounded", True)
    attempts = state.get("correction_attempts", 0)
    if not grounded and attempts < 1:
        return "draft_entry"
    return "finalize"


def finalize_node(state: EntryState) -> dict:
    events = [_event("finalize", "complete")]
    return {"final": state.get("draft"), "events": events}
