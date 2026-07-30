import pytest

from app.graph import structured
from app.graph.schemas import ReviewerReport
from app.graph.structured import invoke_structured_with_retry
from tests.fakes import VALID_REVIEWER_REPORT, FakeStructuredChatModel


class _FlakyUpstreamModel:
    """Raises the same class of transient upstream error langchain_openai
    surfaces (a plain ValueError) on its first `fail_times` calls, then
    behaves like a normal FakeStructuredChatModel."""

    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.calls = 0
        self._good_model = FakeStructuredChatModel({"ReviewerReport": VALID_REVIEWER_REPORT})

    def invoke(self, messages):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ValueError({"message": "Upstream error from Nvidia: ResourceExhausted", "code": 502})
        return self._good_model.invoke(messages)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # Keeps the retry backoff from actually pausing the test suite.
    monkeypatch.setattr(structured.time, "sleep", lambda seconds: None)


def test_retries_through_transient_upstream_errors_and_succeeds():
    llm = _FlakyUpstreamModel(fail_times=2)

    result = invoke_structured_with_retry(llm, "persona", "prompt", ReviewerReport, max_attempts=2)

    assert result.model is not None
    assert llm.calls == 3  # 2 failures + 1 success


def test_raises_after_exhausting_all_transient_retries():
    llm = _FlakyUpstreamModel(fail_times=999)

    with pytest.raises(ValueError):
        invoke_structured_with_retry(llm, "persona", "prompt", ReviewerReport, max_attempts=2)

    assert llm.calls == structured._TRANSIENT_RETRY_MAX_ATTEMPTS
