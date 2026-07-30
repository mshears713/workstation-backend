import json
import re
import time
from typing import TypeVar

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from app.graph.prompts import build_system_prompt

ModelT = TypeVar("ModelT", bound=BaseModel)

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def _extract_json(text: str) -> str:
    text = text.strip()
    fenced = _FENCE_RE.search(text)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


class StructuredResult:
    def __init__(self, model: ModelT | None, error: str | None, attempts: int, raw_last: str):
        self.model = model
        self.error = error
        self.attempts = attempts
        self.raw_last = raw_last


def invoke_structured(
    llm,
    persona: str,
    user_prompt: str,
    model_cls: type[ModelT],
    max_attempts: int = 2,
) -> StructuredResult:
    """Call `llm` and validate its reply against `model_cls`, retrying on invalid JSON.

    Bounded: after `max_attempts` failed attempts, returns a StructuredResult with
    model=None and error set, rather than raising or retrying forever.
    """
    system_prompt = build_system_prompt(persona, model_cls)
    messages: list = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    last_error: str | None = None
    raw_last = ""

    for attempt in range(1, max_attempts + 1):
        response = llm.invoke(messages)
        raw_last = getattr(response, "content", str(response))
        try:
            data = json.loads(_extract_json(raw_last))
            validated = model_cls.model_validate(data)
            return StructuredResult(validated, None, attempt, raw_last)
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)
            if attempt < max_attempts:
                messages.append(AIMessage(content=raw_last))
                messages.append(
                    HumanMessage(
                        content=(
                            "Your previous response was not valid JSON matching the "
                            f"required schema. Error: {last_error}. Respond again with "
                            "ONLY valid JSON matching the schema - no prose, no code fences."
                        )
                    )
                )

    return StructuredResult(None, last_error, max_attempts, raw_last)


# Same numbers as app/graph/graph.py's/app/voice/graph.py's own
# LLM_RETRY_POLICY (max_attempts=5, initial_interval=2.0, backoff_factor=2.0)
# - kept as plain constants here rather than a langgraph.types.RetryPolicy
# since this retry has to work outside a StateGraph node (see
# invoke_structured_with_retry's docstring).
_TRANSIENT_RETRY_MAX_ATTEMPTS = 5
_TRANSIENT_RETRY_INITIAL_INTERVAL_S = 2.0
_TRANSIENT_RETRY_BACKOFF_FACTOR = 2.0


def invoke_structured_with_retry(
    llm,
    persona: str,
    user_prompt: str,
    model_cls: type[ModelT],
    max_attempts: int = 2,
) -> StructuredResult:
    """Same contract as invoke_structured() above, with an added outer retry
    for transient upstream errors (rate limits, a momentary provider outage)
    - langchain_openai surfaces these as a plain ValueError raised directly
    out of llm.invoke(), which invoke_structured()'s own retry loop doesn't
    catch (that loop only retries on invalid JSON/schema output - a
    different failure mode, with different corrective feedback sent back to
    the model).

    LangGraph-node callers (app/graph/graph.py, app/voice/graph.py) already
    get equivalent protection from their own node-level RetryPolicy, so
    they call invoke_structured() directly rather than this wrapper - a
    second retry layer here would stack with LangGraph's and turn a
    persistent outage into several minutes of retries. This wrapper is for
    invoke_structured()'s callers that have no LangGraph node to retry at:
    app/voice/audio_reliability.py and app/entries/* (entry_architect.py,
    verifier.py), all one-shot structured calls with no graph around them.
    """
    delay = _TRANSIENT_RETRY_INITIAL_INTERVAL_S
    last_exc: Exception = ValueError("invoke_structured_with_retry: no attempts made")
    for attempt in range(1, _TRANSIENT_RETRY_MAX_ATTEMPTS + 1):
        try:
            return invoke_structured(llm, persona, user_prompt, model_cls, max_attempts)
        except Exception as exc:  # noqa: BLE001 - deliberately broad: any upstream/transport failure
            last_exc = exc
            if attempt < _TRANSIENT_RETRY_MAX_ATTEMPTS:
                time.sleep(delay)
                delay *= _TRANSIENT_RETRY_BACKOFF_FACTOR
    raise last_exc
