"""The approved targets the workstation is allowed to see.

Exists so the ESP32 does not need reflashing every time a project or a
GitHub repository becomes a valid destination. Before this, the list lived
in firmware (main/project_selector.h) and changing it meant a build and a
flash.

Two sources, merged:

  config/projects.json  a tracked file, always available, hand-editable.
  AI-OS Projects (Notion)  every project whose Status is Active or Testing.

The Notion half is the point of Mission 19: starting a new project in the
AI-OS should make it selectable at the workbench without touching this
machine at all. The file half stays because it is the floor - it needs no
network, no API key and no Notion, so a Notion outage narrows the list
instead of emptying it, and targets that are not AI-OS projects at all (the
workstation's own two repositories) still have somewhere to live.

Two separate lists within that, deliberately not one:

  projects - AI-OS routing context attached to a NOTE (project_hint), which
             the Voice Inbox uses downstream. Nothing to do with GitHub.
  repos    - where a GO-captured GitHub issue is filed.

They look alike on the device (both are a scroller of short labels) but
they mean different things and are chosen in different flows, so merging
them would only invite sending one where the other belongs. A Notion
project appears in both lists when, and only when, it has a GitHub Repo
URL - a project without a repository simply is not offered for GO.

The device is given ids and labels, never the `repo` slug and never a Notion
page id it could write to. It sends an opaque id back and the backend
resolves it - so the mapping, like the credentials, stays server-side, and
the device cannot file an issue against a repository that is not on this
list, nor attach a note to a page that is not an approved project.
"""

import json
import logging
import re
import time
from typing import Any, Optional

from app.config import _PROJECT_ROOT
from app.integrations import notion_client

logger = logging.getLogger(__name__)

CATALOG_PATH = _PROJECT_ROOT / "config" / "projects.json"

# Kept small on purpose: the device parses this with cJSON and renders it on
# a 320x240 screen, so a label that does not fit is a label that is wrong.
MAX_LABEL_LEN = 12
MAX_ID_LEN = 32

# The Cue rides along to the device so tapping a project can show the memory
# hook for it. Capped because the device renders it on a 320x240 panel and
# holds the whole catalog in a fixed buffer; the AI-OS describes a Cue as
# "2-6 words", so this only bites on one that has outgrown its own definition.
MAX_CUE_LEN = 40

# How long a successful Notion read is reused. The device re-fetches the
# catalog on a timer, and several devices or a retry storm should not turn
# into one Notion request each. Short enough that adding a project in the
# AI-OS shows up at the workbench within a couple of minutes.
NOTION_CACHE_SECONDS = 120.0

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


def _load_local_catalog() -> dict[str, Any]:
    """config/projects.json only - the floor, with no network involved."""
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


# ---------------------------------------------------------------------------
# The Notion half
# ---------------------------------------------------------------------------

# Last good Notion read, kept across failures on purpose. A blip should not
# make projects vanish from the workbench mid-session, so a stale list beats
# an empty one; the age is logged when it is being served past its TTL.
_notion_cache: dict[str, Any] = {"entries": None, "fetched_at": 0.0}


def _device_label(entry: dict[str, str]) -> str:
    """The <=12 character label the device will render for a Notion project.

    Uses the project's `Device Label` when set. When it is not, derives one
    from the name rather than skipping the project: a project missing a label
    should look wrong on screen, not be silently unselectable. Only one of the
    five current AI-OS projects has a name that fits unaided, which is why the
    property exists at all.
    """
    label = entry.get("device_label", "").strip()
    if not label:
        label = entry.get("name", "").strip().upper()
    if len(label) <= MAX_LABEL_LEN:
        return label
    clipped = label[:MAX_LABEL_LEN]
    # Prefer a word boundary, but not if it throws away most of the label.
    space = clipped.rfind(" ")
    if space >= MAX_LABEL_LEN // 2:
        clipped = clipped[:space]
    return clipped.strip()


def _clip_cue(cue: str) -> str:
    cue = " ".join(cue.split())
    if len(cue) <= MAX_CUE_LEN:
        return cue
    clipped = cue[:MAX_CUE_LEN]
    space = clipped.rfind(" ")
    if space >= MAX_CUE_LEN // 2:
        clipped = clipped[:space]
    return clipped.strip()


# "https://github.com/owner/name", with or without .git or a trailing slash.
_REPO_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$"
)


def _repo_slug(url: str) -> Optional[str]:
    """'owner/name' from a GitHub URL, or None if it is not one.

    Parsed here rather than at issue-creation time so a mistyped URL in Notion
    quietly means "this project is not a GO target" instead of failing while
    someone is standing at the workstation waiting for an issue number.
    """
    match = _REPO_URL_RE.match(url.strip())
    return f"{match.group(1)}/{match.group(2)}" if match else None


def _notion_entries() -> list[dict[str, str]]:
    """Active + Testing AI-OS projects, normalised for the device.

    Never raises. A Notion failure is logged and the last good list is served
    if there is one, otherwise an empty list - the local catalog carries on
    either way, because losing the ability to capture is far worse than
    losing a few selectable labels.
    """
    now = time.monotonic()
    cached = _notion_cache["entries"]
    if cached is not None and now - _notion_cache["fetched_at"] < NOTION_CACHE_SECONDS:
        return cached

    try:
        raw = notion_client.query_projects()
    except Exception as exc:  # noqa: BLE001 - degraded, not fatal
        if cached is None:
            logger.warning("Notion projects unavailable, serving local catalog only: %s", exc)
            return []
        age = int(now - _notion_cache["fetched_at"])
        logger.warning("Notion projects unavailable, serving %ds-old list: %s", age, exc)
        return cached

    entries: list[dict[str, str]] = []
    for item in raw:
        # Notion page ids are dashed UUIDs; the device's id field is 32 chars
        # plus a nul, so the dashes come off here and go back on in
        # resolve_project_page_id(). Same id, one representation each side.
        entry_id = item["page_id"].replace("-", "")
        if len(entry_id) > MAX_ID_LEN:
            logger.warning("skipping Notion project with an unexpected id: %r", item["page_id"])
            continue
        label = _device_label(item)
        if not label:
            logger.warning("skipping Notion project with no usable label: %r", item["page_id"])
            continue
        entry = {"id": entry_id, "label": label, "cue": _clip_cue(item.get("cue", ""))}
        slug = _repo_slug(item.get("github_repo", ""))
        if slug:
            entry["repo"] = slug
        entries.append(entry)

    _notion_cache["entries"] = entries
    _notion_cache["fetched_at"] = now
    logger.info("Notion projects loaded: %d selectable, %d with a repository",
                len(entries), sum(1 for e in entries if "repo" in e))
    return entries


def reset_notion_cache() -> None:
    """Drops the cached Notion read. For tests, and for anything that wants
    the next request to reflect an AI-OS edit immediately."""
    _notion_cache["entries"] = None
    _notion_cache["fetched_at"] = 0.0


# ---------------------------------------------------------------------------
# The merged catalog
# ---------------------------------------------------------------------------


def load_catalog() -> dict[str, Any]:
    """Full catalog - local file plus AI-OS projects, including repo slugs
    and Notion page ids. Server-side use only.

    The local file goes first and wins on an id collision: it is the entry
    someone can actually read and edit in this repository, so it should not be
    shadowed by something typed into Notion. Collisions are not expected -
    local ids are words, Notion ids are hex - but the order still has to be
    decided somewhere.
    """
    local = _load_local_catalog()
    projects = list(local["projects"])
    repos = list(local["repos"])
    seen = {entry["id"] for entry in projects} | {entry["id"] for entry in repos}

    for entry in _notion_entries():
        if entry["id"] in seen:
            continue
        project = {"id": entry["id"], "label": entry["label"], "cue": entry["cue"]}
        projects.append(project)
        if "repo" in entry:
            repos.append({"id": entry["id"], "label": entry["label"], "repo": entry["repo"]})

    return {"version": local["version"], "projects": projects, "repos": repos}


def device_catalog() -> dict[str, Any]:
    """What the ESP32 is given: ids, labels and cues, no repo slugs.

    The device never needs to know which GitHub repository an id maps to,
    and not sending it keeps the payload small and the mapping in one place.
    The cue is sent because the device shows it when a project is tapped -
    a 12-character label is not enough to be sure you picked the right one.
    """
    full = load_catalog()
    return {
        "version": full["version"],
        "projects": [
            {"id": p["id"], "label": p["label"], "cue": p.get("cue", "")}
            for p in full["projects"]
        ],
        "repos": [{"id": r["id"], "label": r["label"]} for r in full["repos"]],
    }


def resolve_repo(repo_id: str) -> Optional[str]:
    """Maps a device-supplied repo id to its 'owner/name', or None if it is
    not an approved target.

    This is the allowlist that stops a device (or anything else posting to
    the issue endpoint) from filing against an arbitrary repository.
    """
    for entry in load_catalog()["repos"]:
        if entry["id"] == repo_id:
            return entry["repo"]
    return None


def resolve_project_page_id(project_id: Optional[str]) -> Optional[str]:
    """Maps a device-supplied project id to the Notion page it stands for, or
    None if it does not stand for one.

    None is the ordinary case, not a failure. It covers the operator choosing
    nothing ("none"), a local config-file project that has no Notion page
    behind it, and an id that is simply not on the list - and every caller
    treats all three the same way: write the note without a Related Project
    relation and let the transcript speak for itself.
    """
    if not project_id or project_id == "none":
        return None
    for entry in _notion_entries():
        if entry["id"] == project_id:
            pid = entry["id"]
            # Back to the dashed UUID form the Notion API expects.
            return "-".join([pid[:8], pid[8:12], pid[12:16], pid[16:20], pid[20:]])
    return None
