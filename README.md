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
.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` matters — `127.0.0.1` is unreachable from the ESP32. The
address must match `BACKEND_BASE_URL` in the firmware's
`main/backend_config.h`. Plain HTTP, no TLS: LAN only.

Add `--reload` while iterating; without it a code change needs a restart.

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

`config/projects.json` holds the approved project and repository targets the
workstation may select. It is re-read per request, so adding a target needs no
restart — and no firmware flash, which is the point.

The `GITHUB_TOKEN` should be a **fine-grained** token limited to the specific
repositories, with `Issues: Read and write`. Even so, only repositories listed
in `config/projects.json` can be written to: the device sends an opaque id and
`projects_catalog.resolve_repo()` is the allowlist.

## Live paths

| Route | Command | Destination |
|---|---|---|
| `/api/v1/voice-inbox` | SEND (15 s auto), NOTE (long-form) | Notion Voice Inbox |
| `/api/v1/issues` | GO | GitHub issue |
| `/api/v1/projects` | selector population | — |
| `/api/v1/notifications` | YES + background poll | spoken playback |
| `/api/v1/remote` | 150 ms poll | Roku IR |
| `/health` | 3 s poll | reachability |

Every audio route accepts chunks at `/{request_id}/chunk?offset=N` and
assembles on `/finish`. The offset is an ordering guard — the firmware uploads
from a task other than the one filling the buffer, so arrival order is no
longer guaranteed by the device being single-threaded.
