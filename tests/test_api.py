import threading
import time

from fastapi.testclient import TestClient

from app.api import runner
from app.api.main import app
from app.graph.graph import build_graph
from tests.fakes import FakeSlowChatModel, FakeStructuredChatModel


def _fake_graph():
    return build_graph(llm_factory=FakeStructuredChatModel)


def _poll_until_terminal(client, run_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        resp = client.get(f"/runs/{run_id}")
        assert resp.status_code == 200
        status = resp.json()["status"]
        if status in ("completed", "failed"):
            return status
        time.sleep(0.02)
    return status


def test_health():
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        # commit/started_at let a human confirm the running server is the
        # code they just committed - the ESP32 only checks the status code.
        assert body["commit"]
        assert body["started_at"]


def test_health_reports_the_running_commit():
    """Regression guard for a real miss: a firmware change was tested against
    a uvicorn process started before the matching backend commit, the new
    form field was silently dropped as unknown, and it looked like a device
    bug. Now the running commit is one curl away."""
    import subprocess

    with TestClient(app) as client:
        reported = client.get("/health").json()["commit"]

    try:
        expected = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        expected = ""

    if expected:
        assert reported == expected
    else:
        assert reported == "unknown"  # not a checkout - degrade, never raise


def test_full_run_lifecycle(monkeypatch):
    monkeypatch.setattr(runner, "graph", _fake_graph())

    with TestClient(app) as client:
        resp = client.post("/runs", json={"request_id": "req-1", "notes": "test"})
        assert resp.status_code == 202
        body = resp.json()
        run_id = body["run_id"]
        assert body["duplicate"] is False

        status = _poll_until_terminal(client, run_id)
        assert status == "completed"

        result = client.get(f"/runs/{run_id}/result").json()
        assert result["final"]["disposition"] == "approve_with_changes"
        assert result["reports"]["embedded"]["role"] == "Fake Reviewer"
        assert result["reports"]["backend"]["role"] == "Fake Reviewer"
        assert result["reports"]["langgraph"]["role"] == "Fake Reviewer"
        assert result["reports"]["audit"]["summary"]
        assert len(result["events"]) > 0
        assert result["model"]["name"]
        assert result["trace"]["run_name"] == f"design-review-{run_id}"


def test_duplicate_request_id_does_not_start_second_run(monkeypatch):
    monkeypatch.setattr(runner, "graph", _fake_graph())

    with TestClient(app) as client:
        first = client.post("/runs", json={"request_id": "dup-1"})
        assert first.status_code == 202
        run_id = first.json()["run_id"]
        _poll_until_terminal(client, run_id)

        second = client.post("/runs", json={"request_id": "dup-1"})
        assert second.status_code == 200
        assert second.json()["duplicate"] is True
        assert second.json()["run_id"] == run_id


def test_second_run_rejected_while_one_is_active(monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(runner, "graph", build_graph(llm_factory=lambda: FakeSlowChatModel(gate)))

    with TestClient(app) as client:
        first = client.post("/runs", json={"request_id": "busy-1"})
        assert first.status_code == 202
        run_id = first.json()["run_id"]

        deadline = time.monotonic() + 5.0
        status = None
        while time.monotonic() < deadline:
            status = client.get(f"/runs/{run_id}").json()["status"]
            if status == "running":
                break
            time.sleep(0.02)
        assert status == "running"

        second = client.post("/runs", json={"request_id": "busy-2"})
        assert second.status_code == 409

        gate.set()
        final_status = _poll_until_terminal(client, run_id)
        assert final_status == "completed"


def test_unknown_run_returns_404():
    with TestClient(app) as client:
        resp = client.get("/runs/does-not-exist")
        assert resp.status_code == 404
        resp2 = client.get("/runs/does-not-exist/result")
        assert resp2.status_code == 404
