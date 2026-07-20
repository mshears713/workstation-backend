from datetime import datetime, timezone
from typing import Callable

from app.graph.structured import invoke_structured
from app.voice import prompts
from app.voice.schemas import FinalNoteResult, GroundingReport, InterpretedNote, PossibleActions
from app.voice.state import VoiceNoteState

LlmFactory = Callable[[], object]

DEFAULT_MAX_ATTEMPTS = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event(node: str, kind: str, **extra) -> dict:
    return {"ts": _now(), "node": node, "type": kind, **extra}


def _issues_as_feedback(grounding_report: dict) -> str:
    issues = grounding_report.get("issues", [])
    if issues:
        return "; ".join(f"[{i.get('category')}] {i.get('description')}" for i in issues)
    return grounding_report.get("summary", "")


def validate_transcript_node(state: VoiceNoteState) -> dict:
    transcript = (state.get("transcript") or "").strip()
    events = [_event("validate_transcript", "start")]

    if not transcript:
        events.append(_event("validate_transcript", "invalid", reason="empty_transcript"))
        return {
            "transcript_valid": False,
            "events": events,
            "warnings": ["Transcript was empty; note could not be interpreted."],
        }
    if len(transcript) < 3:
        events.append(_event("validate_transcript", "invalid", reason="transcript_too_short"))
        return {
            "transcript_valid": False,
            "events": events,
            "warnings": ["Transcript was too short to interpret meaningfully."],
        }

    events.append(_event("validate_transcript", "valid"))
    return {"transcript_valid": True, "events": events}


def route_after_validate(state: VoiceNoteState) -> str:
    return "interpret_note" if state.get("transcript_valid") else "finalize"


def make_interpret_note_node(llm_factory: LlmFactory):
    def node(state: VoiceNoteState) -> dict:
        llm = llm_factory()
        max_attempts = state.get("max_attempts", DEFAULT_MAX_ATTEMPTS)

        is_correction = state.get("grounding_report") is not None
        correction_attempts = state.get("correction_attempts", 0)
        feedback = None
        if is_correction:
            correction_attempts += 1
            feedback = _issues_as_feedback(state["grounding_report"])

        user_prompt = prompts.build_interpret_user_prompt(state["transcript"], feedback)
        result = invoke_structured(llm, prompts.INTERPRET_PERSONA, user_prompt, InterpretedNote, max_attempts)

        events = [_event("interpret_note", "invoked", attempts=result.attempts, is_correction=is_correction)]
        warnings: list[str] = []
        if result.model is None:
            note = InterpretedNote(
                title="Unable to interpret note",
                summary=(
                    "Interpretation failed to produce valid structured output after "
                    f"{max_attempts} attempt(s)."
                ),
                category="unknown",
            )
            events.append(_event("interpret_note", "fallback", error=result.error))
            warnings.append(f"Note interpretation failed: {result.error}")
        else:
            note = result.model
            events.append(_event("interpret_note", "complete"))

        return {
            "interpreted_note": note.model_dump(),
            "correction_attempts": correction_attempts,
            "events": events,
            "warnings": warnings,
        }

    return node


def make_extract_actions_node(llm_factory: LlmFactory):
    def node(state: VoiceNoteState) -> dict:
        llm = llm_factory()
        max_attempts = state.get("max_attempts", DEFAULT_MAX_ATTEMPTS)

        is_correction = state.get("correction_attempts", 0) > 0 and state.get("grounding_report") is not None
        feedback = _issues_as_feedback(state["grounding_report"]) if is_correction else None

        user_prompt = prompts.build_actions_user_prompt(
            state["transcript"], state.get("interpreted_note") or {}, feedback
        )
        result = invoke_structured(llm, prompts.ACTIONS_PERSONA, user_prompt, PossibleActions, max_attempts)

        events = [_event("extract_actions", "invoked", attempts=result.attempts, is_correction=is_correction)]
        warnings: list[str] = []
        if result.model is None:
            actions = PossibleActions(needs_mike_review=True)
            events.append(_event("extract_actions", "fallback", error=result.error))
            warnings.append(f"Action extraction failed: {result.error}")
        else:
            actions = result.model
            events.append(_event("extract_actions", "complete"))

        return {
            "possible_actions": actions.model_dump(),
            "events": events,
            "warnings": warnings,
        }

    return node


def make_grounding_review_node(llm_factory: LlmFactory):
    def node(state: VoiceNoteState) -> dict:
        llm = llm_factory()
        max_attempts = state.get("max_attempts", DEFAULT_MAX_ATTEMPTS)

        user_prompt = prompts.build_grounding_user_prompt(
            state["transcript"],
            state.get("interpreted_note") or {},
            state.get("possible_actions") or {},
        )
        result = invoke_structured(llm, prompts.GROUNDING_PERSONA, user_prompt, GroundingReport, max_attempts)

        events = [_event("grounding_review", "invoked", attempts=result.attempts)]
        warnings: list[str] = []
        if result.model is None:
            report = GroundingReport(
                grounded=False,
                issues=[],
                summary=(
                    "Grounding review failed to produce valid structured output after "
                    f"{max_attempts} attempt(s); treating this note as unverified."
                ),
            )
            events.append(_event("grounding_review", "fallback", error=result.error))
            warnings.append(f"Grounding review failed: {result.error}")
        else:
            report = result.model
            events.append(_event("grounding_review", "complete", grounded=report.grounded))

        return {
            "grounding_report": report.model_dump(),
            "events": events,
            "warnings": warnings,
        }

    return node


def route_after_grounding(state: VoiceNoteState) -> str:
    report = state.get("grounding_report") or {}
    grounded = report.get("grounded", True)
    attempts = state.get("correction_attempts", 0)
    if not grounded and attempts < 1:
        return "interpret_note"
    return "finalize"


def finalize_node(state: VoiceNoteState) -> dict:
    events = [_event("finalize", "start")]
    warnings = list(state.get("warnings", []))

    if not state.get("transcript_valid", False):
        final = FinalNoteResult(
            status="failed",
            warnings=warnings or ["Transcript was invalid; note could not be processed."],
        )
        events.append(_event("finalize", "complete", status=final.status))
        return {"final": final.model_dump(), "events": events}

    grounding = state.get("grounding_report") or {}
    grounded = grounding.get("grounded")
    correction_attempts = state.get("correction_attempts", 0)

    if grounded is False:
        warnings = warnings + [
            "Grounding review still flagged issues after the bounded correction attempt."
            if correction_attempts
            else "Grounding review flagged issues."
        ]

    status = "completed_with_warnings" if warnings else "completed"

    final = FinalNoteResult(
        status=status,
        grounded=grounded,
        correction_attempts=correction_attempts,
        warnings=warnings,
    )
    events.append(_event("finalize", "complete", status=final.status))
    return {"final": final.model_dump(), "events": events}
