"""GitHub issue creation.

Lives here, not on the ESP32, and that is the point: a Personal Access Token
grants write access to real repositories, and firmware is flashed, dumped and
shared far too casually to hold one. The device names an approved repo *id*
(projects_catalog) and this module resolves it and does the writing.

Same shape as notion_client.py - direct httpx against the REST API, no SDK,
lazy credential check so importing the module works before the token is
configured.
"""

import httpx

from app.config import get_settings

# Pinned rather than left to default. GitHub's REST API is versioned by this
# header, and an unpinned client silently follows whatever the current
# default becomes.
GITHUB_API_VERSION = "2022-11-28"


class GitHubClientError(Exception):
    pass


def _require_token() -> str:
    """Checked lazily, never at import - mirrors notion_client._require_api_key
    so the module imports cleanly before GITHUB_TOKEN is filled in, and the
    error names the fix."""
    settings = get_settings()
    if not settings.github_token:
        raise GitHubClientError(
            "GITHUB_TOKEN is not set. Add it to .env before creating issues "
            "(`gh auth token` prints a usable one)."
        )
    return settings.github_token


def create_issue(repo: str, title: str, body: str, labels: list[str] | None = None) -> dict[str, object]:
    """Creates one issue and returns {number, url, api_url}.

    `repo` is a full "owner/name" already resolved from an approved id by
    projects_catalog.resolve_repo(); this function does not decide what is
    allowed, it only writes. Keeping the allowlist upstream means there is
    exactly one place to look to answer "what can the workstation write to".
    """
    settings = get_settings()
    token = _require_token()

    response = httpx.post(
        f"{settings.github_api_base_url}/repos/{repo}/issues",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        },
        json={
            "title": title,
            "body": body,
            **({"labels": labels} if labels else {}),
        },
        timeout=settings.request_timeout_seconds,
    )
    if response.status_code >= 300:
        # Deliberately does not include the response body verbatim in a way
        # that could echo the token back; GitHub does not return it, but the
        # message is also surfaced to the device and then to a screen.
        raise GitHubClientError(
            f"GitHub issue creation failed for {repo}: {response.status_code} {response.text[:300]}"
        )

    payload = response.json()
    return {
        "number": payload.get("number"),
        "url": payload.get("html_url"),
        "api_url": payload.get("url"),
    }
