# Workstation Backend

FastAPI service behind the ESP32-S3-BOX-3 workstation
(`mshears713/workstation_esp32`). The device captures audio, shows status and
makes HTTP requests; everything else — credentials, transcription, AI
interpretation, routing, Notion writes, GitHub issue creation — happens here.

That split is deliberate and load-bearing: the ESP32 never holds an API key.

---

## Dormant functionality — read this before any large change

Several complete, working pipelines are kept in the tree with **no caller**.
They were deliberately parked, not abandoned, and they are easy to delete by
accident during a rewrite because nothing references them and nothing fails
when they go.

**Anyone planning the next major version, a big refactor, or a dependency
upgrade should read this list first and make an explicit decision about each
item rather than discovering them by grep.**

### Van build log / entry pipeline — parked 2026-09-02

`app/entries/`, `app/api/entries_router.py`, `app/api/entries_runner.py`,
`app/api/entries_store.py`, and the device side in `main/entry_client.c`.

`POST /api/v1/entries` still works end to end: transcribe → audio-reliability
check → entry architect graph (draft → review → finalize) → Notion **Sources**
page → Notion **Van Build Log** page linked back to it → semantic verifier →
spoken notification when the verifier is unsure.

It is dormant only because the **GO** voice command, its sole trigger, became
GitHub-issue capture. Nothing is wrong with the pipeline. If voice-captured
van build entries are wanted again it needs a trigger, not a rewrite.

Covered by `tests/test_entries_api.py` and `tests/test_entries_graph.py`, so
it will keep passing CI while unused — which is exactly why it can rot
unnoticed.

### Local LangGraph notes route

`app/api/notes_*`, `app/voice/graph.py`, device side `main/note_client.c`.

`POST /api/v1/notes` — what **SEND** used before it moved to the Voice Inbox.
Transcribes and runs a local graph, but **writes nothing to Notion**; results
stay on disk as JSON.

### Design-review graph

`app/graph/`, `app/api/runner.py`, `app/api/store.py`, `app/fixed_proposal.py`,
device side `main/graph_client.c`.

`POST /runs` — GO's original one-shot trigger, before GO recorded anything.
Fans out to three reviewers → auditor → chair, and only ever reviews the
hardcoded string in `fixed_proposal.py`. Last real run 2026-07-29.

> **Trap:** `app/graph/model.py` (the OpenRouter client) and
> `app/graph/structured.py` are **live** — imported by `app/voice/graph.py`,
> `app/entries/graph.py` and `app/voice/audio_reliability.py`. The
> `app/graph/` package **cannot be deleted wholesale**.

### Single-shot multipart uploads

`POST /api/v1/notes`, `/voice-inbox`, `/entries` (the non-`/chunk` forms).

The device uses the chunked path exclusively. These remain as the shared
downstream code path and for curl/testing, so they are not dead — but nothing
on the device calls them.

### Handshake

`POST /api/v1/handshake` — fires only from a manual SND button press on the
device's LOG page. **Not** a health check: it ships accelerometer samples and
does real work. Backend reachability is `GET /health`, polled by
`main/backend_health.c`.

---

## Running it

```
.un.ps1
```

That wrapper exists so the two settings that are easy to get wrong, and
expensive to notice later, are not retyped each time:

- **`--host 0.0.0.0`** — the ESP32 cannot reach `127.0.0.1`. Binding to
  loopback looks fine from this machine and fails from the device.
- **`--reload`** — restart on any change under `app/`, `config/`, or `.env`.
  Without it a committed change can sit unloaded for hours while firmware is
  tested against it, which has happened and cost a hardware test plus a
  post-mortem to work out.

`.env` is watched explicitly because uvicorn's reloader only watches Python
files by default, and adding a credential there is exactly the kind of change
you expect to take effect immediately.

Safe to leave running during a recording: a restart mid-capture fails the
in-flight chunk, the firmware retries it, and the accumulated `.pcm` on disk
survives — so the recording continues rather than being lost.

The address must match `BACKEND_BASE_URL` in the firmware's
`main/backend_config.h`. Plain HTTP, no TLS: LAN only.

**After any backend change, check what is actually running:**

```
curl http://127.0.0.1:8000/health
{"status":"ok","commit":"975aeca","started_at":"..."}
```

`commit` should match `git rev-parse --short HEAD`. uvicorn is started by hand
and stays up for hours, so testing firmware against a backend that never
loaded the matching change is an easy and confusing mistake — it has happened.

## Tests

```
.venv\Scripts\python.exe -m pytest
```

No pytest config; discovery relies on defaults plus `tests/__init__.py`.

## Configuration

Copy `.env.example` to `.env`. Four secrets are required for the live paths:
`OPENAI_API_KEY` (transcription + TTS), `OPENROUTER_API_KEY` (the LLM behind
every graph), `NOTION_API_KEY` (Voice Inbox and van build log writes), and
`GITHUB_TOKEN` (only if issue creation is used).

`config/projects.json` holds the approved project and repository targets that
are always available regardless of Notion. It is re-read per request, so adding
a target needs no restart - and no firmware flash, which is the point. The AI-OS
Projects database is merged on top of it; see "Where the selectable projects
come from" below.

The `GITHUB_TOKEN` should be a **fine-grained** token limited to the specific
repositories, with `Issues: Read and write`. Even so, only repositories on the
merged catalog can be written to: the device sends an opaque id and
`projects_catalog.resolve_repo()` is the allowlist. Note that this now includes
repositories named by a Notion project's `GitHub Repo` property, so the token's
repository scope is the real boundary - adding a URL in Notion does not grant
access the token does not already have.

## Live paths

| Route | Command | Destination |
|---|---|---|
| `POST /api/v1/voice-inbox/{id}/chunk`, `/finish` | SEND (15 s auto), NOTE (long-form) | Notion Voice Inbox |
| `POST /api/v1/issues/{id}/chunk`, `/finish` | GO (15 s auto) | transcribe → GitHub issue |
| `POST /api/v1/issues` | — (text only; curl, tests) | GitHub issue |
| `GET /api/v1/projects` | selector population | AI-OS Projects + `config/projects.json` |
| `/api/v1/notifications` | YES + background poll | spoken playback |
| `/api/v1/remote` | polled while online | Roku IR |
| `/health` | 3 s poll | reachability + running commit |

`NOTE` also sends `project_hint`, the operator's selection on the device. It is
recorded with the note and written to Notion as the Voice Inbox page's
`Related Project` relation — see "The operator's routing hint" below.

`/api/v1/issues/finish` is the one synchronous capture route: it transcribes
and files the issue *before* answering, so the device can show a real issue
number instead of polling for one. Everything else returns 202 and processes
in the background.

Every audio route accepts chunks at `/{request_id}/chunk?offset=N` and
assembles on `/finish`. The offset is an ordering guard — the firmware uploads
from a task other than the one filling the buffer, so arrival order is no
longer guaranteed by the device being single-threaded.

`request_id` doubles as the resource id on every capture route, so a retry
after a lost response returns the original rather than creating a second
record — or, for issues, a second GitHub issue.

## Where the selectable projects come from

`GET /api/v1/projects` merges two sources, and the order matters:

1. **`config/projects.json`** - a tracked file, hand-editable, always
   available. This is the floor: no network, no API key, no Notion.
2. **The AI-OS Projects database** - every project whose `Status` is `Active`
   or `Testing`, read live from Notion and cached for two minutes.

Starting a project in the AI-OS therefore makes it selectable at the workbench
with no firmware flash and no edit on this machine, which is the whole point.
The file half stays because a Notion outage should narrow the list, not empty
it - and because the workstation's own two repositories are not AI-OS projects
and still need somewhere to live.

Three Projects properties are read, none written:

| Property | Type | Used for |
|---|---|---|
| `Device Label` | text | The label shown on the 320x240 screen. Capped at 12 characters - only one of the five current project names fits unaided, which is why this exists. Derived from the name when unset, so an unlabelled project looks wrong rather than disappearing. |
| `Cue` | text | The AI-OS's own "2-6 word memory hook". Sent to the device so tapping a project can show it - a 12-character label is not enough to be sure you picked the right one. |
| `GitHub Repo` | url | A project with one becomes a GO target; a project without one is simply not offered for GO. A malformed URL means the same thing, quietly - failing here would take the catalog down over a typo the capture path does not need. |

**The Projects database must be shared with the Notion integration.** Without
it the query returns 404, the failure is logged, and the local file is served
alone - capture keeps working, the AI-OS projects just do not appear. Share it
from the database's `...` menu -> Connections -> Workstation.

## The operator's routing hint

The device lets the operator mark a NOTE with which project they think it
belongs to. That selection reaches the backend as `project_hint` - an opaque
catalog id, never a Notion page id - and `projects_catalog.resolve_project_page_id()`
maps it back to the page. The Voice Inbox page is then created with a
**`Related Project`** relation pointing at it.

It is advisory, not routing. The note lands in the Voice Inbox either way; the
relation only tells the downstream agent what the operator had in mind. An
empty hint is the ordinary case - no selection, or a local config-file project
with no AI-OS page behind it - and the note is written without the relation,
exactly as before.

`Category`, `Processing Notes` and the page body are still left untouched for
Notion's own automation.

> Historical note, because it explains a zero: `Related Project` was pointed
> at Notion's stock Projects *template* (demo rows - "Website redesign",
> "Product launch"), not the AI-OS Projects database. It was empty on all 101
> Voice Inbox rows because nothing sensible could ever link there. It was
> repointed on 2026-09-03; this feature depends on that.

Worth being precise about what exists today, because it is easy to assume
more: this backend can write to **three** Notion destinations - Voice Inbox,
Sources, and Van Build Log. There is no Quartermaster or Knowledge writer.
Of the three, only Voice Inbox is reachable from a live capture; the other two
belonged to the parked entries pipeline.
