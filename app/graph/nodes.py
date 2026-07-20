from datetime import datetime, timezone
from typing import Callable

from app.fixed_proposal import FIXED_PROPOSAL
from app.graph import prompts
from app.graph.schemas import AuditReport, FinalDisposition, ReviewerReport
from app.graph.state import ReviewState
from app.graph.structured import invoke_structured

LlmFactory = Callable[[], object]

DEFAULT_MAX_ATTEMPTS = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event(node: str, kind: str, **extra) -> dict:
    return {"ts": _now(), "node": node, "type": kind, **extra}


def intake_node(state: ReviewState) -> dict:
    return {
        "proposal": FIXED_PROPOSAL,
        "max_attempts": state.get("max_attempts") or DEFAULT_MAX_ATTEMPTS,
        "events": [_event("intake", "start", run_id=state.get("run_id"))],
    }


def make_reviewer_node(
    node_name: str,
    role_label: str,
    persona: str,
    state_key: str,
    llm_factory: LlmFactory,
):
    def node(state: ReviewState) -> dict:
        llm = llm_factory()
        user_prompt = prompts.build_reviewer_user_prompt(state["proposal"], state.get("notes"))
        max_attempts = state.get("max_attempts", DEFAULT_MAX_ATTEMPTS)

        result = invoke_structured(llm, persona, user_prompt, ReviewerReport, max_attempts)
        events = [_event(node_name, "reviewer_invoked", attempts=result.attempts)]

        if result.model is None:
            report = ReviewerReport(
                role=role_label,
                summary=(
                    "Reviewer failed to produce valid structured output after "
                    f"{max_attempts} attempt(s); treat this review as inconclusive."
                ),
                findings=[],
                assumptions=[],
                open_questions=[f"Structured output validation failed: {result.error}"],
            )
            events.append(_event(node_name, "reviewer_fallback", error=result.error))
        else:
            report = result.model
            events.append(_event(node_name, "reviewer_complete"))

        return {state_key: report.model_dump(), "events": events}

    return node


def make_auditor_node(llm_factory: LlmFactory):
    def node(state: ReviewState) -> dict:
        llm = llm_factory()
        max_attempts = state.get("max_attempts", DEFAULT_MAX_ATTEMPTS)
        user_prompt = prompts.build_auditor_user_prompt(
            state["proposal"],
            state.get("embedded_report") or {},
            state.get("backend_report") or {},
            state.get("langgraph_report") or {},
        )

        result = invoke_structured(llm, prompts.AUDITOR_PERSONA, user_prompt, AuditReport, max_attempts)
        events = [_event("auditor", "invoked", attempts=result.attempts)]

        if result.model is None:
            report = AuditReport(
                summary=(
                    "Audit failed to produce valid structured output after "
                    f"{max_attempts} attempt(s); treat cross-checking as incomplete."
                ),
                conflicts=[],
                cross_cutting_risks=[f"Audit error: {result.error}"],
            )
            events.append(_event("auditor", "fallback", error=result.error))
        else:
            report = result.model
            events.append(_event("auditor", "complete"))

        return {"audit_report": report.model_dump(), "events": events}

    return node


def make_chair_node(llm_factory: LlmFactory):
    def node(state: ReviewState) -> dict:
        llm = llm_factory()
        max_attempts = state.get("max_attempts", DEFAULT_MAX_ATTEMPTS)
        user_prompt = prompts.build_chair_user_prompt(
            state["proposal"],
            state.get("embedded_report") or {},
            state.get("backend_report") or {},
            state.get("langgraph_report") or {},
            state.get("audit_report") or {},
        )

        result = invoke_structured(llm, prompts.CHAIR_PERSONA, user_prompt, FinalDisposition, max_attempts)
        events = [_event("chair", "invoked", attempts=result.attempts)]

        if result.model is None:
            final = FinalDisposition(
                disposition="revise",
                rationale=(
                    "Final chair failed to produce valid structured output after "
                    f"{max_attempts} attempt(s); defaulting to 'revise' pending human review."
                ),
                next_steps=["Have a human review the specialist and audit reports directly."],
                key_risks=[f"Chair error: {result.error}"],
            )
            events.append(_event("chair", "fallback", error=result.error))
        else:
            final = result.model
            events.append(_event("chair", "complete"))

        return {"final": final.model_dump(), "events": events}

    return node
