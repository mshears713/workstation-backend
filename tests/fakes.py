import re
import threading

from langchain_core.messages import AIMessage

from app.graph.schemas import AuditReport, FinalDisposition, ReviewerReport

_TITLE_RE = re.compile(r"Schema name:\s*(\w+)")

VALID_REVIEWER_REPORT = ReviewerReport(
    role="Fake Reviewer",
    summary="This is a fake but structurally valid review for testing.",
    findings=[
        {
            "severity": "minor",
            "title": "Example finding",
            "description": "A fake finding used only in tests.",
            "recommendation": "No action needed, this is a test fixture.",
        }
    ],
    assumptions=["The workstation is always reachable on the local network."],
    open_questions=["Is this only ever used on a trusted LAN?"],
).model_dump_json()

VALID_AUDIT_REPORT = AuditReport(
    summary="Fake audit: reports are broadly consistent.",
    conflicts=[],
    cross_cutting_risks=["No authentication on the local network."],
).model_dump_json()

VALID_FINAL_DISPOSITION = FinalDisposition(
    disposition="approve_with_changes",
    rationale="Fake disposition used only in tests.",
    next_steps=["Add a timeout/retry policy.", "Confirm LAN trust assumptions."],
    key_risks=["Free-tier model availability."],
).model_dump_json()


class FakeStructuredChatModel:
    """Returns a canned JSON response keyed by the JSON-schema `title` embedded
    in the system prompt, so it works regardless of node execution order
    (parallel reviewer nodes may call `.invoke()` in any order)."""

    def __init__(self, responses_by_title: dict[str, str] | None = None):
        self.responses_by_title = responses_by_title or {
            "ReviewerReport": VALID_REVIEWER_REPORT,
            "AuditReport": VALID_AUDIT_REPORT,
            "FinalDisposition": VALID_FINAL_DISPOSITION,
        }
        self.calls: list[str] = []

    def invoke(self, messages):
        system_content = messages[0].content if messages else ""
        self.calls.append(system_content)
        match = _TITLE_RE.search(system_content)
        title = match.group(1) if match else None
        content = self.responses_by_title.get(title)
        if content is None:
            raise RuntimeError(f"FakeStructuredChatModel: no canned response for title={title!r}")
        return AIMessage(content=content)


class FakeFlakyChatModel:
    """First call for a given schema title returns invalid JSON, second call
    returns the valid canned response. Used to test bounded retry handling."""

    def __init__(self):
        self._seen_titles: set[str] = set()

    def invoke(self, messages):
        system_content = messages[0].content if messages else ""
        match = _TITLE_RE.search(system_content)
        title = match.group(1) if match else None
        if title not in self._seen_titles:
            self._seen_titles.add(title)
            return AIMessage(content="this is not valid json at all")
        responses = {
            "ReviewerReport": VALID_REVIEWER_REPORT,
            "AuditReport": VALID_AUDIT_REPORT,
            "FinalDisposition": VALID_FINAL_DISPOSITION,
        }
        return AIMessage(content=responses[title])


class FakeAlwaysInvalidChatModel:
    def invoke(self, messages):
        return AIMessage(content="not json")


class FakeSlowChatModel:
    """Blocks on `gate` before responding - used to hold a run "in progress"
    long enough for a test to assert single-active-run behavior."""

    def __init__(self, gate: threading.Event):
        self.gate = gate

    def invoke(self, messages):
        self.gate.wait(timeout=5)
        system_content = messages[0].content if messages else ""
        match = _TITLE_RE.search(system_content)
        title = match.group(1) if match else None
        responses = {
            "ReviewerReport": VALID_REVIEWER_REPORT,
            "AuditReport": VALID_AUDIT_REPORT,
            "FinalDisposition": VALID_FINAL_DISPOSITION,
        }
        return AIMessage(content=responses[title])
