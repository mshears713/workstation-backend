"""Catalog tests (issue #6).

The point of this endpoint is that adding a project or repo stops requiring
a firmware flash, so the tests care about two things: the device gets a
small, well-formed list, and a misconfigured catalog fails loudly rather
than quietly handing the workstation an empty selector.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.api import projects_catalog
from app.api.main import app


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    """Points the catalog at a throwaway file so tests never depend on - or
    disturb - the real config/projects.json."""
    path = tmp_path / "projects.json"
    monkeypatch.setattr(projects_catalog, "CATALOG_PATH", path)

    def write(payload):
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    write.path = path
    return write


GOOD = {
    "version": 3,
    "projects": [{"id": "van1", "label": "VAN1"}],
    "repos": [{"id": "wk", "label": "WKSTN", "repo": "owner/workstation"}],
}


# --- what the device sees -------------------------------------------------

def test_device_never_receives_repo_slugs(catalog):
    """The device sends back an opaque id and the backend resolves it, so the
    mapping stays server-side and a device cannot name an arbitrary repo."""
    catalog(GOOD)
    with TestClient(app) as client:
        body = client.get("/api/v1/projects").json()
    assert body["repos"] == [{"id": "wk", "label": "WKSTN"}]
    assert "repo" not in body["repos"][0]
    assert "owner/workstation" not in json.dumps(body)


def test_projects_and_repos_are_separate_lists(catalog):
    """They look alike on screen but mean different things - an AI-OS routing
    hint is not a GitHub target."""
    catalog(GOOD)
    with TestClient(app) as client:
        body = client.get("/api/v1/projects").json()
    assert [p["id"] for p in body["projects"]] == ["van1"]
    assert [r["id"] for r in body["repos"]] == ["wk"]


def test_version_is_passed_through_for_device_side_caching(catalog):
    catalog(GOOD)
    with TestClient(app) as client:
        assert client.get("/api/v1/projects").json()["version"] == 3


def test_payload_stays_small_enough_for_the_device(catalog):
    """cJSON on an ESP32 rendering to a 320x240 screen. A realistic catalog
    should be a few hundred bytes, not a few thousand."""
    catalog({
        "version": 1,
        "projects": [{"id": f"proj{i}", "label": f"P{i}"} for i in range(8)],
        "repos": [{"id": f"r{i}", "label": f"R{i}", "repo": f"owner/repo{i}"} for i in range(8)],
    })
    with TestClient(app) as client:
        resp = client.get("/api/v1/projects")
    assert len(resp.content) < 1024


# --- resolving a device-supplied id --------------------------------------

def test_resolve_repo_maps_an_approved_id(catalog):
    catalog(GOOD)
    assert projects_catalog.resolve_repo("wk") == "owner/workstation"


def test_resolve_repo_rejects_anything_not_in_the_catalog(catalog):
    """This is the allowlist that stops an issue being filed against an
    arbitrary repository - #7 depends on it."""
    catalog(GOOD)
    assert projects_catalog.resolve_repo("some-other-repo") is None
    assert projects_catalog.resolve_repo("owner/workstation") is None  # slug is not an id


# --- a broken catalog fails loudly ---------------------------------------

@pytest.mark.parametrize("payload,reason", [
    ({"version": 1, "repos": [{"id": "a", "label": "A"}]}, "missing repo slug"),
    ({"version": 1, "repos": [{"id": "a", "label": "A", "repo": "no-slash"}]}, "malformed slug"),
    ({"version": 1, "projects": [{"id": "a", "label": "WAY-TOO-LONG-LABEL"}]}, "label too long"),
    ({"version": 1, "projects": [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}]}, "duplicate id"),
    ({"version": 1, "projects": [{"label": "A"}]}, "missing id"),
    ({"version": -1}, "bad version"),
    ({"version": 1, "projects": "not-a-list"}, "projects not a list"),
])
def test_misconfigured_catalog_raises(catalog, payload, reason):
    catalog(payload)
    with pytest.raises(projects_catalog.CatalogError):
        projects_catalog.load_catalog()


def test_endpoint_returns_500_on_a_broken_catalog(catalog):
    """An empty selector and a misconfigured one look identical on the
    device, and only one of them is worth investigating."""
    catalog({"version": 1, "repos": [{"id": "a", "label": "A", "repo": "no-slash"}]})
    with TestClient(app) as client:
        resp = client.get("/api/v1/projects")
    assert resp.status_code == 500
    assert "misconfigured" in resp.json()["detail"]


def test_invalid_json_raises_rather_than_returning_nothing(catalog):
    catalog.path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(projects_catalog.CatalogError):
        projects_catalog.load_catalog()


def test_missing_file_falls_back_instead_of_failing(catalog):
    """A missing file is a fresh checkout, not a typo. The workstation still
    gets a usable (if minimal) list rather than a 500."""
    assert not catalog.path.exists()
    body = projects_catalog.load_catalog()
    assert body["version"] == 0
    assert body["projects"]
    assert body["repos"] == []


# --- the catalog actually shipped ----------------------------------------

def test_the_real_config_file_is_valid():
    """Guards the committed config/projects.json itself - a typo there breaks
    the device, and nothing else would catch it."""
    catalog = projects_catalog.load_catalog()
    assert catalog["version"] >= 1
    assert catalog["projects"], "at least one project target"
    assert catalog["repos"], "at least one repo target"
    for repo in catalog["repos"]:
        assert repo["repo"].count("/") == 1
