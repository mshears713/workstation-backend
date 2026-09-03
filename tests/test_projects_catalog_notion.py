"""The AI-OS half of the catalog (issue #19).

The promise of this feature is "start a project in Notion, capture against it
at the workbench, without touching the firmware or this machine". That makes
two things worth testing hard:

  * the shape handed to the device stays inside the limits it renders and
    parses with - a 12-character label, a 32-character id, a short cue;
  * a Notion outage narrows the list instead of emptying it, because the one
    thing that must never break is the ability to capture.

The fixture rows below are the real AI-OS project rows as of 2026-09-03,
trimmed to the four properties this code reads. Using the real shape matters:
`Device Label` and `GitHub Repo` are genuinely unset on every row today, which
is exactly the case the label derivation exists for.
"""

import pytest

from app.api import projects_catalog
from app.integrations import notion_client

# Real page ids and cues; Device Label / GitHub Repo are empty as they are in
# the workspace right now.
REAL_PROJECTS = [
    {
        "page_id": "391850a9-11d3-81fa-b8c6-d9a79c66559a",
        "name": "Operation Homebound",
        "device_label": "",
        "cue": "Console can hear now",
        "github_repo": "",
    },
    {
        "page_id": "396850a9-11d3-8181-bb3b-ffd63a3b886d",
        "name": "Van Deal Radar",
        "device_label": "",
        "cue": "Radar finds, Mike decides",
        "github_repo": "",
    },
    {
        "page_id": "39d850a9-11d3-8192-bd81-d063ccf71ae1",
        "name": "Procedure Engineering — Learning Campaign",
        "device_label": "",
        "cue": "Procedure engineering campaign",
        "github_repo": "",
    },
]


@pytest.fixture
def notion(monkeypatch):
    """Replaces the live Notion read. Call with the rows a test wants."""

    def use(rows):
        monkeypatch.setattr(notion_client, "query_projects", lambda: list(rows))
        projects_catalog.reset_notion_cache()

    return use


# --- what reaches the device ----------------------------------------------


def test_notion_projects_are_offered_alongside_the_local_ones(notion):
    notion(REAL_PROJECTS)
    catalog = projects_catalog.device_catalog()
    ids = [p["id"] for p in catalog["projects"]]

    # The local file's entries survive - a Notion outage must not be the only
    # thing standing between the operator and a usable selector.
    assert "general" in ids
    # Dashless, so it fits the device's 33-byte id field.
    assert "391850a911d381fab8c6d9a79c66559a" in ids


def test_every_label_and_id_fits_what_the_device_renders(notion):
    notion(REAL_PROJECTS)
    catalog = projects_catalog.device_catalog()

    for entry in catalog["projects"] + catalog["repos"]:
        assert 0 < len(entry["id"]) <= projects_catalog.MAX_ID_LEN, entry
        assert 0 < len(entry["label"]) <= projects_catalog.MAX_LABEL_LEN, entry
    for entry in catalog["projects"]:
        assert len(entry["cue"]) <= projects_catalog.MAX_CUE_LEN, entry


def test_the_cue_rides_along_so_the_device_can_show_it(notion):
    notion(REAL_PROJECTS)
    by_id = {p["id"]: p for p in projects_catalog.device_catalog()["projects"]}
    assert by_id["391850a911d381fab8c6d9a79c66559a"]["cue"] == "Console can hear now"


def test_the_device_is_never_told_the_repo_slug(notion):
    notion([dict(REAL_PROJECTS[0], github_repo="https://github.com/mshears713/workstation_esp32")])
    catalog = projects_catalog.device_catalog()
    assert all("repo" not in entry for entry in catalog["repos"])


# --- labels ----------------------------------------------------------------


def test_device_label_wins_when_it_is_set(notion):
    notion([dict(REAL_PROJECTS[2], device_label="PROC ENG")])
    labels = [p["label"] for p in projects_catalog.device_catalog()["projects"]]
    assert "PROC ENG" in labels


def test_a_project_with_no_device_label_is_still_selectable(notion):
    """Derived, not dropped. An unlabelled project should look wrong on the
    screen so it gets fixed - not disappear so nobody notices."""
    notion([REAL_PROJECTS[2]])  # 41-character name, no Device Label
    labels = [p["label"] for p in projects_catalog.device_catalog()["projects"]]
    assert "PROCEDURE" in labels  # word boundary, not a hard 12-char slice


def test_a_name_that_already_fits_is_left_alone(notion):
    notion([REAL_PROJECTS[1]])  # "Van Deal Radar" -> 14 chars, needs trimming
    labels = [p["label"] for p in projects_catalog.device_catalog()["projects"]]
    assert "VAN DEAL" in labels


# --- GitHub repos ----------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/mshears713/workstation_esp32", "mshears713/workstation_esp32"),
        ("https://github.com/mshears713/workstation_esp32/", "mshears713/workstation_esp32"),
        ("https://github.com/mshears713/workstation_esp32.git", "mshears713/workstation_esp32"),
        ("http://www.github.com/a/b", "a/b"),
        ("", None),
        ("not a url", None),
        ("https://gitlab.com/a/b", None),
        # A repository page rather than the repository itself - close enough
        # to be typed by mistake, and filing issues against it would be wrong.
        ("https://github.com/a/b/issues", None),
    ],
)
def test_repo_url_parsing(url, expected):
    assert projects_catalog._repo_slug(url) == expected


def test_only_projects_with_a_repo_become_go_targets(notion):
    notion([
        dict(REAL_PROJECTS[0], github_repo="https://github.com/mshears713/workstation_esp32"),
        REAL_PROJECTS[1],  # no repo
    ])
    catalog = projects_catalog.load_catalog()
    repo_ids = {r["id"] for r in catalog["repos"]}

    assert "391850a911d381fab8c6d9a79c66559a" in repo_ids
    assert "396850a911d38181bb3bffd63a3b886d" not in repo_ids
    # And the allowlist resolves it, which is what actually files the issue.
    assert projects_catalog.resolve_repo("391850a911d381fab8c6d9a79c66559a") == (
        "mshears713/workstation_esp32"
    )


def test_a_malformed_repo_url_just_means_not_a_go_target(notion):
    """Quietly, and only for GO. Failing here would take down the whole
    catalog over a typo in a property the capture path does not need."""
    notion([dict(REAL_PROJECTS[0], github_repo="github.com/oops")])
    catalog = projects_catalog.load_catalog()

    assert "391850a911d381fab8c6d9a79c66559a" in {p["id"] for p in catalog["projects"]}
    assert "391850a911d381fab8c6d9a79c66559a" not in {r["id"] for r in catalog["repos"]}


# --- the routing hint ------------------------------------------------------


def test_the_hint_resolves_back_to_a_notion_page(notion):
    notion(REAL_PROJECTS)
    assert projects_catalog.resolve_project_page_id("391850a911d381fab8c6d9a79c66559a") == (
        "391850a9-11d3-81fa-b8c6-d9a79c66559a"
    )


@pytest.mark.parametrize("hint", [None, "", "none", "general", "van1", "deadbeef"])
def test_no_hint_means_no_relation_rather_than_an_error(notion, hint):
    """An empty hint is the ordinary case, not a failure: the operator often
    has no view and the transcript decides instead. Local config-file projects
    land here too - they have no AI-OS page to point at."""
    notion(REAL_PROJECTS)
    assert projects_catalog.resolve_project_page_id(hint) is None


# --- when Notion is not there ---------------------------------------------


def test_a_notion_outage_narrows_the_list_instead_of_emptying_it():
    """conftest leaves query_projects raising, so this is the unconfigured
    and the offline case at once."""
    catalog = projects_catalog.device_catalog()
    assert [p["id"] for p in catalog["projects"]] == ["van1", "van2", "general"]
    assert [r["id"] for r in catalog["repos"]] == ["workstation", "backend"]


def test_a_blip_after_a_good_read_keeps_serving_the_last_good_list(notion, monkeypatch):
    """A stale list beats an empty one. Projects vanishing mid-session because
    a single request timed out would be worse than showing one that is two
    minutes old."""
    notion(REAL_PROJECTS)
    assert len(projects_catalog.load_catalog()["projects"]) == 3 + len(REAL_PROJECTS)

    def boom():
        raise notion_client.NotionClientError("timeout")

    monkeypatch.setattr(notion_client, "query_projects", boom)
    monkeypatch.setattr(projects_catalog, "NOTION_CACHE_SECONDS", 0.0)  # force a re-read

    assert len(projects_catalog.load_catalog()["projects"]) == 3 + len(REAL_PROJECTS)


def test_a_successful_read_is_cached_rather_than_made_per_request(notion, monkeypatch):
    calls = []

    def counted():
        calls.append(1)
        return list(REAL_PROJECTS)

    monkeypatch.setattr(notion_client, "query_projects", counted)
    projects_catalog.reset_notion_cache()

    projects_catalog.device_catalog()
    projects_catalog.device_catalog()
    projects_catalog.device_catalog()
    assert len(calls) == 1
