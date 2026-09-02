"""The approved targets the workstation is allowed to see.

Exists so the ESP32 does not need reflashing every time a project or a
GitHub repository becomes a valid destination. Before this, the list lived
in firmware (main/project_selector.h) and changing it meant a build and a
flash.

Two separate lists, deliberately not one:

  projects - AI-OS routing context attached to a NOTE (project_hint), which
             the Voice Inbox uses downstream. Nothing to do with GitHub.
  repos    - where a GO-captured GitHub issue is filed.

They look alike on the device (both are a scroller of short labels) but
they mean different things and are chosen in different flows, so merging
them would only invite sending one where the other belongs.

The device is given ids and labels, never the `repo` slug. It sends an
opaque id back and the backend resolves it - so the mapping, like the
credentials, stays server-side, and the device cannot file an issue against
a repository that is not on this list.

Config is a tracked JSON file rather than a database or a settings field,
because "add a repo" should be a one-line edit that a human can make and
read back. It is re-read per request, so a change takes effect without a
restart.
"""

import json
from pathlib import Path
from typing import Any

from app.config import _PROJECT_ROOT

CATALOG_PATH = _PROJECT_ROOT / "config" / "projects.json"

# Kept small on purpose: the device parses this with cJSON and renders it on
# a 320x240 screen, so a label that does not fit is a label that is wrong.
MAX_LABEL_LEN = 12
MAX_ID_LEN = 32

# Used when the file is missing or unreadable. A workstation with a broken
# config file should still offer its known-good targets rather than showing
# the operator an empty list - the failure is logged, not hidden, but it does
# not take the capture flow down with it.
_FALLBACK: dict[str, Any] = {
    "version": 0,
    "projects": [{"id": "general", "label": "GEN"}],
    "repos": [],
}


class CatalogError(Exception):
    """The catalog file exists but is not usable."""


def _clean_entries(raw: Any, *, require_repo: bool) -> list[dict[str, str]]:
    """Keeps only well-formed entries and trims what the device will render.

    Silently dropping a malformed entry would hide a typo until someone
    wondered why their repo never appeared, so anything wrong raises.
    """
    if not isinstance(raw, list):
        raise CatalogError("expected a list")

    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise CatalogError(f"entry is not an object: {entry!r}")
        entry_id = entry.get("id")
        label = entry.get("label")
        if not isinstance(entry_id, str) or not entry_id or len(entry_id) > MAX_ID_LEN:
            raise CatalogError(f"bad id: {entry_id!r}")
        if not isinstance(label, str) or not label or len(label) > MAX_LABEL_LEN:
            raise CatalogError(f"bad label for {entry_id!r}: {label!r} (max {MAX_LABEL_LEN} chars)")
        if entry_id in seen:
            raise CatalogError(f"duplicate id: {entry_id!r}")
        seen.add(entry_id)

        item = {"id": entry_id, "label": label}
        if require_repo:
            repo = entry.get("repo")
            # "owner/name" - checked here rather than at issue-creation time
            # so a typo surfaces when the catalog is read, not when someone
            # is standing at the workstation waiting for a confirmation.
            if not isinstance(repo, str) or repo.count("/") != 1 or not all(repo.split("/")):
                raise CatalogError(f"bad repo for {entry_id!r}: {repo!r} (want 'owner/name')")
            item["repo"] = repo
        out.append(item)
    return out


def load_catalog() -> dict[str, Any]:
    """Full catalog, including the repo slugs. Server-side use only."""
    if not CATALOG_PATH.exists():
        return dict(_FALLBACK)
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{CATALOG_PATH.name} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise CatalogError(f"{CATALOG_PATH.name} must contain an object")

    version = raw.get("version", 0)
    if not isinstance(version, int) or version < 0:
        raise CatalogError(f"bad version: {version!r}")

    return {
        "version": version,
        "projects": _clean_entries(raw.get("projects", []), require_repo=False),
        "repos": _clean_entries(raw.get("repos", []), require_repo=True),
    }


def device_catalog() -> dict[str, Any]:
    """What the ESP32 is given: ids and labels, no repo slugs.

    The device never needs to know which GitHub repository an id maps to,
    and not sending it keeps the payload small and the mapping in one place.
    """
    full = load_catalog()
    return {
        "version": full["version"],
        "projects": full["projects"],
        "repos": [{"id": r["id"], "label": r["label"]} for r in full["repos"]],
    }


def resolve_repo(repo_id: str) -> str | None:
    """Maps a device-supplied repo id to its 'owner/name', or None if it is
    not an approved target.

    This is the allowlist that stops a device (or anything else posting to
    the issue endpoint) from filing against an arbitrary repository.
    """
    for entry in load_catalog()["repos"]:
        if entry["id"] == repo_id:
            return entry["repo"]
    return None
