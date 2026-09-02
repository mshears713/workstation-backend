"""GitHub issue endpoint tests (issue #7).

Every test fakes github_client.create_issue - the suite must never file a
real issue. The credential path is covered by asserting the error when no
token is set, not by using one.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.api import issues_router, issues_store, projects_catalog
from app.api.main import app
from app.integrations import github_client

CATALOG = {
    "version": 1,
    "projects": [{"id": "van1", "label": "VAN1"}],
    "repos": [
        {"id": "wk", "label": "WKSTN", "repo": "owner/workstation"},
        {"id": "be", "label": "BACKEND", "repo": "owner/backend"},
    ],
}


@pytest.fixture(autouse=True)
def catalog(tmp_path, monkeypatch):
    path = tmp_path / "projects.json"
    path.write_text(json.dumps(CATALOG), encoding="utf-8")
    monkeypatch.setattr(projects_catalog, "CATALOG_PATH", path)


@pytest.fixture
def fake_github(monkeypatch):
    """Records what would have been sent, so the tests can assert on it."""
    calls = []

    def _create(repo, title, body, labels=None):
        calls.append({"repo": repo, "title": title, "body": body, "labels": labels})
        return {
            "number": 42,
            "url": f"https://github.com/{repo}/issues/42",
            "api_url": f"https://api.github.com/repos/{repo}/issues/42",
        }

    monkeypatch.setattr(issues_router.github_client, "create_issue", _create)
    return calls


def _post(client, **over):
    payload = {
        "request_id": "gh-1",
        "repo_id": "wk",
        "body": "The mic clips at the start of every recording. Worth checking the codec settle time.",
        "source": "esp32-box3",
    }
    payload.update(over)
    return client.post("/api/v1/issues", json=payload)


# --- happy path -----------------------------------------------------------

def test_creates_an_issue_against_the_resolved_repo(fake_github):
    with TestClient(app) as client:
        resp = _post(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["issue"]["number"] == 42
    assert body["repo"] == "owner/workstation"
    assert body["duplicate"] is False
    assert fake_github[0]["repo"] == "owner/workstation"


def test_device_supplied_id_is_resolved_not_trusted(fake_github):
    """The device sends 'wk'; only the backend knows that means
    owner/workstation."""
    with TestClient(app) as client:
        _post(client, repo_id="be")
    assert fake_github[0]["repo"] == "owner/backend"


def test_title_is_derived_from_the_transcript_when_absent(fake_github):
    with TestClient(app) as client:
        resp = _post(client, body="The mic clips at the start. Worth checking the codec.")
    assert resp.json()["title"] == "The mic clips at the start."


def test_long_transcript_without_punctuation_is_truncated_on_a_word_boundary(fake_github):
    with TestClient(app) as client:
        resp = _post(client, body="word " * 40)
    title = resp.json()["title"]
    assert len(title) <= issues_router.TITLE_MAX_LEN + 3
    assert title.endswith("...")
    assert " " in title  # cut between words, not mid-word


def test_explicit_title_wins(fake_github):
    with TestClient(app) as client:
        resp = _post(client, title="Explicit title")
    assert resp.json()["title"] == "Explicit title"


def test_body_carries_provenance(fake_github):
    with TestClient(app) as client:
        _post(client, metadata={"duration_seconds": 15})
    sent = fake_github[0]["body"]
    assert "esp32-box3" in sent
    assert "gh-1" in sent
    assert "duration_seconds: 15" in sent


# --- the allowlist --------------------------------------------------------

def test_unknown_repo_id_is_rejected(fake_github):
    with TestClient(app) as client:
        resp = _post(client, repo_id="not-in-catalog")
    assert resp.status_code == 422
    assert "unknown repo_id" in resp.json()["detail"]
    assert fake_github == [], "must not reach GitHub"


def test_a_raw_owner_name_slug_is_not_accepted_as_an_id(fake_github):
    """The whole point of the catalog: naming a repository directly must not
    work, or the allowlist is decorative."""
    with TestClient(app) as client:
        resp = _post(client, repo_id="owner/workstation")
    assert resp.status_code in (404, 422)
    assert fake_github == []


# --- idempotency ----------------------------------------------------------

def test_repeat_request_id_does_not_file_a_second_issue(fake_github):
    """The firmware retries on a lost response. That must not produce two
    issues for one spoken sentence."""
    with TestClient(app) as client:
        first = _post(client)
        second = _post(client)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["issue"]["number"] == 42
    assert len(fake_github) == 1


def test_a_failed_attempt_can_be_retried(monkeypatch):
    """A failure is recorded but must not poison the request_id - the whole
    point of retrying is that the second attempt is allowed to work."""

    def _failing(repo, title, body, labels=None):
        raise github_client.GitHubClientError("upstream exploded")

    monkeypatch.setattr(issues_router.github_client, "create_issue", _failing)
    with TestClient(app) as client:
        assert _post(client).status_code == 502
        assert issues_store.load_record("gh-1")["status"] == "failed"

        def _ok(repo, title, body, labels=None):
            return {"number": 7, "url": "u", "api_url": "a"}

        monkeypatch.setattr(issues_router.github_client, "create_issue", _ok)
        retry = _post(client)
    assert retry.status_code == 201
    assert retry.json()["issue"]["number"] == 7


# --- validation and failure ----------------------------------------------

@pytest.mark.parametrize("bad_id", ["", "..", ".", "has space", "semi;colon", "x" * 201])
def test_invalid_request_id_rejected(fake_github, bad_id):
    with TestClient(app) as client:
        assert _post(client, request_id=bad_id).status_code == 422
    assert fake_github == []


def test_empty_body_rejected(fake_github):
    with TestClient(app) as client:
        assert _post(client, body="   ").status_code == 422
    assert fake_github == []


def test_github_failure_is_recorded_and_surfaced(monkeypatch):
    def _failing(repo, title, body, labels=None):
        raise github_client.GitHubClientError("401 Bad credentials")

    monkeypatch.setattr(issues_router.github_client, "create_issue", _failing)
    with TestClient(app) as client:
        resp = _post(client)
    assert resp.status_code == 502
    assert "Bad credentials" in resp.json()["detail"]
    record = issues_store.load_record("gh-1")
    assert record["status"] == "failed"
    assert "Bad credentials" in record["error"]


def test_missing_token_names_the_fix(monkeypatch):
    """No token configured is the state a fresh checkout is in, so the error
    has to say what to do rather than just failing."""
    from app.config import get_settings

    monkeypatch.setenv("GITHUB_TOKEN", "")
    get_settings.cache_clear()
    with pytest.raises(github_client.GitHubClientError) as exc:
        github_client.create_issue("owner/name", "t", "b")
    assert "GITHUB_TOKEN" in str(exc.value)
    get_settings.cache_clear()


# --- lookup ---------------------------------------------------------------

def test_status_can_be_read_back(fake_github):
    with TestClient(app) as client:
        _post(client)
        resp = client.get("/api/v1/issues/gh-1")
    assert resp.status_code == 200
    assert resp.json()["issue"]["number"] == 42


def test_unknown_request_id_is_404():
    with TestClient(app) as client:
        assert client.get("/api/v1/issues/never-filed").status_code == 404
