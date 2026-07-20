import json
import re
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
