# KRONOS — Project Summary

## What We're Building

**KRONOS** is an internal credit portfolio intelligence tool that:

1. Accepts an Excel file upload (`.xlsx`, `.xls`, `.csv`)
2. Routes to a mode-specific deterministic Python processor script based on the user's selected analysis type
3. The processor produces `deterministic_narrative_payload` (primary LLM input), `raw_results`, and `metrics`
4. Passes the payload to a **LangGraph StateGraph** (`pipeline/agent.py`) which narrates via Azure OpenAI using `DefaultAzureCredential` bearer token auth (no API key)
5. Returns structured output (narrative + claims citations) with number cross-check verification
6. Logs every request to MLflow (audit / compliance) when `KRONOS_MLFLOW_ENABLED=true` — gated off by default until Databricks / AICE access is provisioned
7. Displays the narrative, claims tab, verification badge, data-used panel, and summary metric tiles in a custom HTML/CSS/JS UI served by Flask

---

## Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Frontend | HTML / CSS / JS | Full design control, no framework constraints |
| Backend | Flask | Serves static files + handles `/upload` API route |
| Hosting | Domino Data Lab | Published app via `app.sh` entry point, port 8888 |
| Excel parsing | `pandas` + `openpyxl` | Standard Python stack |
| LLM framework | **LangGraph** (`langgraph==0.6.7`, `langgraph-checkpoint==2.1.1`) + LangChain (`langchain==0.3.27`, `langchain-openai==0.3.33`) | `StateGraph` with a `narrate` node that wraps `AzureChatOpenAI` with structured output |
| LLM auth | `azure-identity==1.25.0` | `DefaultAzureCredential` + bearer token. `az login` locally, managed identity on Domino |
| LLM model | Azure OpenAI | Via `AzureChatOpenAI` with `with_structured_output()` inside the narrate node |
| Observability | **MLflow** (`mlflow[databricks]==3.7.0`) + `aice-mlflow-plugins==0.1.3` (internal) | Gated by `KRONOS_MLFLOW_ENABLED` env var. Databricks backend via AICE plugin. Trace capture via `mlflow.langchain.autolog()` |
| Serving pattern | **`ResponsesAgent`** (MLflow pyfunc) | Bank-standard for logging agents as deployable models. Registered via `mlflow.models.set_model()` at the bottom of `pipeline/agent.py` |

**Why Flask, not Streamlit:** Full design control for custom layout, animations, multi-turn chat, drag-and-drop. Flask serves HTML/JS as static files and exposes a clean REST API.

**Why LangGraph + ResponsesAgent:** Bank-standard pattern from the AICE LangGraph cookbook. Enables future AICE Studio deployment as a served model without rewriting the agent.

---

## Architecture

```
/kronos
  app.sh                  ← Domino entry point (runs server.py on port 8888)
  server.py               ← Flask app: static files + /upload route (orchestrator)
                             Calls activate_mlflow() at import, wraps /upload in mlflow_run()
  requirements.txt        ← flask, pandas, openpyxl, langchain, langchain-openai,
                             langgraph, langgraph-checkpoint, azure-identity, mlflow[databricks]
  EXPLAINME.md            ← Plain-English integration guide for non-developers
  VSCODE_CHEATSHEET.md    ← VS Code navigation shortcuts for Python tracing
  /static
    index.html            ← Main UI
    styles.css            ← Full design system + dark mode
    main.js               ← All frontend interactions + fetch() calls to Flask
    prompts.json          ← Canned prompt definitions (modular — edit to add/remove buttons)
  /pipeline
    __init__.py           ← Package marker (empty)
    analyze.py            ← Dispatcher: maps mode slug → correct processor script
                             SCRIPT_MAP: { "firm-level": firm_level.run, ...future modes }
                             Falls back to placeholder_processor() for unregistered modes
    firm_level.py         ← First real processor. run(file_obj) → { context, metrics }
                             Reads Excel, validates 8 required columns, computes:
                               distinct parents, distinct industries,
                               total commitment ($M), total outstanding ($M),
                               criticized & classified ($M), C&C % of commitment
    agent.py              ← LangGraph StateGraph + ResponsesAgent (bank-standard model-from-code)
                             • State, Context, MlflowConfigAgentContext (pydantic)
                             • create_llm() — AzureChatOpenAI with DefaultAzureCredential
                             • narrate() / anarrate() — sync + async nodes with structured output
                             • load_graph() — builds StateGraph
                             • ask_agent() — Flask-facing entry point
                             • LangGraphResponsesAgent — MLflow model wrapper
                             • mlflow.models.set_model(...) — model-from-code registration
    tracking.py           ← MLflow tracking layer — GATED by KRONOS_MLFLOW_ENABLED
                             • activate_mlflow() — called once at app import
                             • mlflow_run() — context manager around each /upload
                             • _NoOpRun / _ActiveRun — dual-mode run handle
    prompts.py            ← All LLM prompts: global SYSTEM_PROMPT + per-mode MODE_SYSTEM_PROMPTS
    validate.py           ← cross_check_numbers(): compares narrative figures vs source data
```

**Note:** `pipeline/llm.py` was removed — LLM construction is now inline in `pipeline/agent.py` (`create_llm()`), which is the bank-standard pattern per the AICE cookbook.

### Real Codebase Mapping (existing Domino app → KRONOS)

| Existing file | Role in KRONOS |
|---|---|
| `processor.py` | `pipeline/analyze.py` dispatcher + individual processor scripts registered in `SCRIPT_MAP` |
| `agent_client.py` | Replaced by `pipeline/agent.py` (LangGraph StateGraph + ResponsesAgent wrapper) |
| `workbook_agent.py` | Replaced by `create_llm()` inside `pipeline/agent.py` |
| `kronos/llm/prompts/prompts.py` | Replaced by `pipeline/prompts.py` (`SYSTEM_PROMPT` + `MODE_SYSTEM_PROMPTS`) |
| `main.py` (Streamlit) | Replaced by `server.py` (Flask) + `static/main.js` (frontend) |
| (new in KRONOS) | `pipeline/tracking.py` — MLflow audit logging with kill switch |

### API Route

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/` | Serves `index.html` |
| `POST` | `/upload` | Receives file + prompt + mode, runs pipeline, returns JSON |

### POST /upload Transports

The route accepts **two request shapes**; pick whichever works through the proxy in front of it.

| `Content-Type` | Body | When to use |
|---|---|---|
| `application/json` | `{ file_name, file_b64, prompt, mode }` (file_b64 = base64 of the workbook bytes) | **UI default.** Required on Domino workspace URLs (`/workspace/<id>/proxy/<port>/`) — the workspace proxy silently drops multipart POSTs |
| `multipart/form-data` | `file` + `prompt` + `mode` as form fields | curl, local dev, and Domino **published apps** (different proxy, handles multipart) |

`server.py` branches on `request.is_json`. The JSON path decodes `file_b64` and wraps the bytes in a tiny `_Base64File` shim that exposes `.read()`, `.filename`, and `.content_length` — from `analyze()` / processors downstream, the upload looks identical whichever transport was used.

### POST /upload Response Shape

```json
{
  "narrative":    "string — LLM-generated analysis text",
  "metrics":      { "Section Name": [{ "label": "...", "value": "...", "delta": "...", "sentiment": "positive|negative|warning|neutral" }] },
  "claims":       [{ "sentence": "...", "source_field": "...", "cited_value": "..." }],
  "context_sent": "string — exact data payload the LLM received",
  "verification": { "total": 14, "verified_count": 11, "unverified_count": 3, "unverified": ["..."], "all_clear": false },
  "timings_ms":   { "analyze": 342, "llm": 5820, "verify": 12 }
}
```

All fields except `narrative` are optional — the frontend uses optional chaining on each.

`timings_ms` reports real server-side stage durations. The frontend uses these to overwrite the pipeline step time labels after the API returns (see "Pipeline Step Timings" below).

### Data Flow

```
User uploads file + selects mode + enters prompt
      ↓
POST /upload  (application/json from UI: { file_name, file_b64, prompt, mode }
               or multipart/form-data from curl/App: file, prompt, mode)
      ↓
server.py
  └─ with mlflow_run(mode, file_name, file_size, user_prompt) as run:   ← pipeline/tracking.py
        (no-op when KRONOS_MLFLOW_ENABLED is unset)
        ↓
        analyze(file, mode)                          ← pipeline/analyze.py
          └─ routes via SCRIPT_MAP to processor (or placeholder_processor if not registered)
          └─ returns { context, metrics }
        ↓
        ask_agent(context, prompt, mode)             ← pipeline/agent.py
          └─ _get_graph() — cached CompiledStateGraph
          └─ graph.invoke({ messages, context_data, mode })
               └─ narrate() node
                    └─ get_system_prompt(mode)       ← pipeline/prompts.py
                    └─ create_llm()                  ← AzureChatOpenAI + bearer token
                    └─ llm.with_structured_output(NarrativeResponse)
                                                     → narrative + claims list
                                                     OR plain text fallback → claims = []
          └─ returns { narrative, claims }
          └─ (autolog captures chain trace if MLflow active)
        ↓
        cross_check_numbers(narrative, context)      ← pipeline/validate.py
          └─ returns { total, verified_count, unverified_count, unverified[], all_clear }
        ↓
        run.log(metrics)                             ← logs latency, counts, artifacts
      ↓
server.py returns { narrative, metrics, claims, context_sent, verification }
      ↓
main.js renders:
  - Analysis tab (narrative text)
  - Claims tab (structured citation cards)
  - Verification badge (green=all verified, amber=some unverified)
  - "View source data" expandable panel
  - Data Snapshot metric tiles
```

### `mode` Routing

Every request carries a `mode` slug (e.g. `"portfolio-summary"`) sent from the frontend. `pipeline/analyze.py` maps this to the correct processor script via `SCRIPT_MAP`. Mode slugs must match the `"mode"` field in `static/prompts.json`.

Custom questions (no canned button selected) send `mode = ''` — `analyze()` falls back to `placeholder_processor()`.

**Currently wired modes:**
| Slug | Processor | Status |
|---|---|---|
| `firm-level` | `pipeline/firm_level.py :: run()` | **Live** — first real processor |
| `portfolio-summary` / `concentration-risk` / `delinquency-trends` / `risk-segments` / `exec-briefing` / `stress-outlook` | — | Placeholder fallback only |

The `firm-level` workbook must contain these columns or `firm_level.run()` raises `ValueError` (surfaced as a 500 with the message): `Ultimate Parent Code`, `Risk Assessment Industry`, `Committed Exposure`, `Outstanding Exposure`, `Special Mention Rated Exposure`, `Substandard Rated Exposure`, `Doubtful Rated Exposure`, `Loss Rated Exposure`.

### Environment Variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `AZURE_OPENAI_DEPLOYMENT` | Yes | `gpt-4o` | Model deployment name |
| `OPENAI_API_VERSION` | Yes | `2025-04-01-preview` | Use `api_version=` not `openai_api_version=` |
| `AZURE_OPENAI_ENDPOINT` | Optional | — | Omit on Domino (proxy injects it) |
| `KRONOS_MLFLOW_ENABLED` | Optional | unset (off) | Set to `true` to activate MLflow tracking |
| `MLFLOW_EXPERIMENT_NAME` | Optional | `kronos-dev` | Experiment name. Use `kronos-prod` at deployment |

Auth: `DefaultAzureCredential` — `az login` locally, managed identity on Domino. No API key.

---

## MLflow Tracking Layer (`pipeline/tracking.py`)

**Default state: OFF.** The MLflow packages install but do nothing at runtime unless `KRONOS_MLFLOW_ENABLED=true`. This lets KRONOS deploy before Databricks / AICE access is provisioned.

### Activation

```bash
# Flip the kill switch (do this once you have Databricks access):
export KRONOS_MLFLOW_ENABLED=true
export MLFLOW_EXPERIMENT_NAME=kronos-dev       # or kronos-prod
pip install aice-mlflow-plugins==0.1.3          # internal bank package
```

`activate_mlflow()` is called once at `server.py` import. It:
1. Sets `mlflow.set_tracking_uri("databricks")`
2. Sets `mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)`
3. Calls `mlflow.langchain.autolog()` for automatic chain trace capture
4. Logs a warning (but does not crash) if `aice_mlflow_plugins` import fails

### What gets logged per request

| Type | Key | Value |
|---|---|---|
| Tag | `kronos.mode` | e.g. `portfolio-summary` or `custom` |
| Tag | `kronos.component` | `upload` |
| Param | `user_prompt` | Truncated to 250 chars |
| Param | `file_name` | Uploaded filename |
| Metric | `file_size_bytes` | File size in bytes |
| Metric | `context_length` | Length of data string sent to LLM |
| Metric | `narrative_length` | Length of narrative returned |
| Metric | `claims_count` | Number of structured claims |
| Metric | `verified_count` | Numbers in narrative found in source |
| Metric | `unverified_count` | Numbers not in source (may be calculated) |
| Metric | `analyze_ms` | `analyze()` stage duration |
| Metric | `llm_ms` | `ask_agent()` stage duration |
| Metric | `verify_ms` | `cross_check_numbers()` stage duration |
| Metric | `latency_ms` | End-to-end request latency |
| Artifact | `context_sent.txt` | Exact string the LLM saw (audit) |
| Auto trace | LangChain chain | Prompt, response, tokens (via `autolog()`) |

### Safety

Every MLflow call is wrapped in try/except. A tracking failure never surfaces as a 500 to the user — it logs a warning and the request continues. We'd rather lose a log line than crash the app.

---

## UI — Current State

### Design System
- **Fonts:** DM Sans (UI) + DM Mono (brand, values, timestamps, code)
- **Accent:** Blueprint blue `#1C4ED8` — focus rings, active states, file badge, pipeline dots
- **CTA:** Muted wine red `#8B2C35` — Run Analysis button only (separate `--cta` token)
- **Banner:** Dark charcoal grey `#2B2E35`
- **Page bg:** `#EDEEF2` (light blue-grey)
- **Dark mode:** Full `prefers-color-scheme: dark` implementation

### Layout
- **Input state:** Single unified card, `max-width: 680px`, centered
- **Loading state:** Pipeline card, `max-width: 480px`, centered
- **Results state:** Two-column grid `1.4fr / 1fr` (narrative left, data snapshot right)
- Responsive breakpoints at 860px and 480px

### Input Panel (Unified Card)
1. **Quick Analysis** — 2×3 grid of canned prompt buttons from `prompts.json`
2. **"or write your own question"** divider
3. **Chat area** — auto-growing textarea with drag-drop file attach
4. **File attachment strip** — format badge, filename, size, ✕ remove

### Results Panel — Narrative Column
- **Tab bar** — "Analysis | Claims (N)" toggle above the narrative text
- **Analysis tab** — LLM narrative dumped immediately (no typewriter)
- **Claims tab** — structured citation cards, each showing: quoted sentence, source field badge, cited value. Empty state shown if structured output fell back to plain text
- **Verification badge** — inline in message-meta. Green ("all in source data") or amber ("N of M figures not in source data"). Hover shows which values are unverified. Unverified = may be calculated (e.g. weighted average PD) — it's a transparency signal, not a correctness gate
- **"View source data sent to AI"** — expandable panel showing exact `context` string the LLM received
- **Copy button** — copies plain text to clipboard

### Results Panel — Data Snapshot Column
- Metric cards in grid, grouped by section, stagger-animated
- Tile color by `sentiment`: `positive` → green, `negative` → red, `warning` → amber, `neutral` → grey
- Fully dynamic — rendered from whatever `metrics` object the API returns

### Multi-turn Chat (Follow-up)
- **FAB** (`+` button) — fixed bottom-right, appears after first response. Hidden during an in-flight follow-up to prevent overlapping threads
- **Follow-up input** — slides into narrative column, submits via Send or `Cmd/Ctrl+Enter`
- **Submitting state** — Send button disables, shows spinner + "Sending…", textarea becomes readonly with `aria-busy`
- **Error path** — non-2xx or `{error}` body surfaces as an inline red `.followup-error` row above the textarea with a Retry button. Retry re-reads the textarea so the user can edit before resending
- **Thread structure** — divider → thinking dots → message block with label + timestamp
- **Metrics update** — follow-up with new metrics re-renders the Data Snapshot panel

Note: follow-ups currently hit stateless `/upload`. When true multi-turn state is needed, LangGraph's checkpointing capability is already installed — just add a checkpointer to `load_graph()` and a `thread_id` per session.

### Pipeline Step Timings
The landing-page pipeline animation previously used hardcoded fake delays (`STEP_DELAYS = [800, 1400, 600, 0, 500]`). Now:
- `STEP_DELAYS` shrunk to `[150, 250, 100, 0, 150]` — short visual placeholders so the animation doesn't block on theater
- Server returns `timings_ms: { analyze, llm, verify }` on every `/upload` response
- After `apiResult` resolves, `runAnalysis()` overwrites `time-0`…`time-4` labels with values derived from `timings_ms` (analyze split across steps 0+1, llm → step 3, verify → step 4)
- If `timings_ms` is absent (old server, mock fallback), wall-clock labels remain — nothing breaks

**Known limitation:** the *animation pacing* during the wait is still artificial — only the *displayed numbers* are honest. For true live-advancing progress, a streaming NDJSON endpoint would be needed (deferred due to Domino proxy-buffering risk).

---

## `prompts.json` — Modular Canned Prompts

Buttons rendered from `static/prompts.json`. To add/remove/edit buttons, only touch this file:

```json
{
  "title": "Button Label",
  "desc": "Subtitle",
  "mode": "mode-slug",
  "prompt": "Full prompt text sent to the LLM"
}
```

`mode` slug must match: `SCRIPT_MAP` key in `pipeline/analyze.py` AND `MODE_SYSTEM_PROMPTS` key in `pipeline/prompts.py`.

Current slugs: `portfolio-summary`, `concentration-risk`, `delinquency-trends`, `risk-segments`, `exec-briefing`, `stress-outlook`.

---

## Transparency Layer

Three features for output validation and traceability:

### 1. Structured Claims (Claims Tab)
`pipeline/agent.py` uses `llm.with_structured_output(NarrativeResponse)` inside the narrate node. Forces the LLM to return narrative + a list of factual claims, each citing its source field and value. Falls back to plain `llm.invoke()` if the model doesn't comply — the narrative always renders, claims tab shows empty state.

### 2. Number Cross-Check (Verification Badge)
`pipeline/validate.py` extracts all numeric tokens from the narrative and checks whether each appears in the `context` string. Returns counts of verified vs unverified figures. Unverified doesn't mean wrong — calculated metrics (weighted averages, deltas) won't appear verbatim in source data.

### 3. Data Used Panel (Source Data Expandable)
The API returns `context_sent` — the exact string the LLM analyzed. Rendered as a collapsible monospace panel below the narrative. Users can see exactly what data the AI had access to.

---

## Bank-Standard Deployment Pattern

`pipeline/agent.py` follows the AICE LangGraph + MLflow cookbook:
- **`ResponsesAgent`** wrapper (from `mlflow.pyfunc`) — enables model-from-code logging
- **`CompiledStateGraph`** — the LangGraph graph being wrapped
- **`mlflow.models.set_model(LangGraphResponsesAgent(_get_graph()))`** at module bottom — registers the agent for `mlflow.pyfunc.log_model(python_model="pipeline/agent.py")`
- **`MlflowConfigAgentContext` + `Context`** — runtime params via pydantic with env var defaults
- **`RunnableCallable(narrate, anarrate)`** — sync + async node variants

Today Flask calls `ask_agent()` directly (no MLflow serving). When ready to deploy as a served model to AICE Studio, no code changes — just `mlflow.pyfunc.log_model(python_model="pipeline/agent.py")` and register the run with AICE.

---

## Payload Architecture (Key Design Decision)

Three objects flow through the pipeline:

| Object | Size | Role | Status |
|---|---|---|---|
| `raw_results` | Large | Full deterministic JSON output | Keep as secondary payload |
| `commentary_facts` | Small | Original reduced narrative subset | Keep for backward compat — no longer primary LLM input |
| `deterministic_narrative_payload` | Medium | **Primary LLM input** — mode-scoped, richer than `commentary_facts` | Build this in `processor.py` |

Currently `pipeline/analyze.py` has a `placeholder_processor()` that reads any Excel/CSV with pandas and produces a basic data summary. This lets the app run end-to-end before real processor scripts are registered.

---

## Mock Data / Demo Mode

`main.js` has a `USE_MOCK_RESULTS` boolean flag near the top. Default is `false`: the app talks to POST `/upload` and any failure (network, server, validation) renders as a visible error message in the narrative column instead of silently faking success.

Flip `USE_MOCK_RESULTS = true` to bypass the backend and render `getMockResult()` for presentations or UI demos without a live Flask server. In that mode, follow-ups also use the mock narrative after a brief fake-think delay.

Keep in mind: when demo mode is on the canned tiles render the generic mock metrics, not firm-level data. Flip back to `false` before real testing.

Also safe to delete: `typewrite()` function (unused).

---

## Domino-Specific Notes

- **Two deploy modes:**
  - **Published App** (via `app.sh`) — Flask on port **8888**, bound to `0.0.0.0`. Proxy handles multipart POSTs correctly. This is the eventual target.
  - **Workspace (AICE Studio, current)** — Flask runs inside the interactive workspace on port **5000**, bound to `0.0.0.0`. Accessed via the workspace-forwarded URL (`/aice-studio/workspace/<id>/proxy/5000/`). 8888 is typically already in use inside the workspace container, hence 5000. When flipping between modes, update `app.run(...)` in `server.py` accordingly.
- **Workspace proxy quirk:** the `/workspace/<id>/proxy/<port>/` route silently drops `multipart/form-data` POSTs (`net::ERR_FAILED` at the browser, request never reaches Flask). KRONOS sidesteps this by shipping uploads as `application/json` with a base64 file payload — see the "POST /upload Transports" table above. Multipart still works for published Apps and for `curl` against localhost.
- **URL resolution in the browser:** the workspace URL has a path prefix. All `fetch()` calls in `main.js` compute `new URL('upload', document.baseURI).href` so the prefix is preserved. An absolute `/upload` would hit the domain root and fail.
- Domino proxies the URL and handles auth — no login needed inside the app.
- **Do not rely on ephemeral disk storage** — write to `/domino/datasets` for persistent files.
- Environment variables set in Domino project settings (no `.env` file committed).
- Auth: managed identity on Domino (no `az login` needed there).
- **MLflow from Domino:** requires Domino can reach Databricks. User confirmed Domino→Databricks networking is expected to work, but their AICE access wasn't set up yet as of 2026-04-20 — hence the kill switch on MLflow.

---

## GitHub

- **Repo:** `https://github.com/ordinarystephen/workprojects.git`
- **Branch:** `kronos` — all project files are on this branch
- **Local setup:** local branch is `main`, tracking `origin/kronos`. `git push origin main:kronos` publishes changes

---

## Sync Workflow (this repo → Domino)

The user does primary development in a Domino workspace. This repo is the source of truth. Workflow:

1. Changes are made + committed + pushed here (to `origin/kronos`)
2. `PULL_ME.md` at repo root tracks what needs copying into Domino
3. Each change round appends a new `## Round N` section to `PULL_ME.md` with a checklist of touched files and a short "behavior change" note
4. When the user has synced everything, they delete `PULL_ME.md` and commit the deletion

When making a new change for the user, always:
- Commit + push to `kronos`
- Append a Round section to `PULL_ME.md` (create the file if missing) with an unchecked list of files and a one-paragraph description

---

## Status

### Done
- [x] `server.py` — Flask app, `/upload` route fully wired: `analyze()` → `ask_agent()` → `cross_check_numbers()` → JSON, wrapped in `mlflow_run()`
- [x] `app.sh` — Domino entry point
- [x] `index.html` — Full UI markup
- [x] `styles.css` — Complete design system with dark mode + responsive + transparency layer styles
- [x] `main.js` — All interactions: drag-drop, canned prompts, mode routing, pipeline animation, metric rendering, multi-turn chat, copy button, Analysis/Claims tabs, verification badge, data-used panel, mock fallback
- [x] `prompts.json` — Modular canned prompts with mode slugs
- [x] `pipeline/analyze.py` — Dispatcher with `SCRIPT_MAP` + working `placeholder_processor()` for dev/demo
- [x] `pipeline/agent.py` — **LangGraph StateGraph + ResponsesAgent (bank-standard model-from-code pattern)** with `with_structured_output(NarrativeResponse)` inside the narrate node + plain text fallback
- [x] `pipeline/tracking.py` — **MLflow tracking layer gated by `KRONOS_MLFLOW_ENABLED`** (off by default). Logs tags, params, metrics, artifacts per request. Wraps `mlflow.start_run()` + `autolog()`
- [x] `pipeline/prompts.py` — Global `SYSTEM_PROMPT` + per-mode `MODE_SYSTEM_PROMPTS` with TODO placeholders
- [x] `pipeline/validate.py` — `cross_check_numbers()` numeric token cross-check
- [x] `requirements.txt` — flask, pandas, openpyxl, langchain, langchain-openai, **langgraph, langgraph-checkpoint**, azure-identity, **mlflow[databricks]**
- [x] `EXPLAINME.md` — Plain-English walkthrough of every file + integration steps
- [x] `VSCODE_CHEATSHEET.md` — Python import tracing + VS Code navigation shortcuts
- [x] `aboutme.md` — Full integration guide with real architecture, checklist, work AI prompt
- [x] Git repo initialized, pushed to GitHub `kronos` branch
- [x] Narrative text dumps immediately (no typewriter delay)
- [x] `mode` field wired end-to-end: `prompts.json` → button → `activeMode` → `formData` → server → `SCRIPT_MAP`
- [x] Bank-standard production pattern: LangGraph + ResponsesAgent + MLflow autolog (dormant)
- [x] FAB follow-up: loading state on Send, inline error row with Retry, FAB hidden during in-flight request
- [x] Real server-side stage timings returned in `/upload` response (`timings_ms`) and overwritten onto pipeline step labels; also logged to MLflow as `analyze_ms` / `llm_ms` / `verify_ms`
- [x] `PULL_ME.md` sync-checklist workflow established for Domino pulls
- [x] **`pipeline/firm_level.py` wired up as first real `SCRIPT_MAP` entry** — reads uploaded workbook, computes six firm-level figures, narrates via Azure OpenAI, renders tiles. "Firm-Level View" button added at position 1 of `prompts.json`
- [x] `USE_MOCK_RESULTS` demo toggle in `main.js` — default `false` so real API errors are now visible. Flip to `true` to bypass Flask and render `getMockResult()` for presentations
- [x] **JSON upload transport + workspace proxy workaround.** UI posts `application/json` with a base64 file payload so uploads survive the Domino workspace proxy (which drops multipart). `server.py` branches on `Content-Type` and wraps the decoded bytes in a `_Base64File` shim — processors downstream see no difference. Multipart path preserved for curl / published Apps.
- [x] `main.js` resolves the fetch URL via `new URL('upload', document.baseURI).href` so the workspace URL prefix (`/aice-studio/workspace/<id>/proxy/5000/`) is preserved instead of being stripped by an absolute path.
- [x] `[KRONOS]`-prefixed `console.log` / `console.error` instrumentation around both `/upload` fetch sites — request shape, response status, parse errors, non-OK bodies, and raw `err.message` on network/CORS failures. Makes proxy-level failures diagnosable from DevTools alone.
- [x] **Verified end-to-end on Domino workspace (2026-04-20):** upload → firm-level processor → Azure OpenAI narration → tiles rendered successfully inside the AICE Studio workspace (port 5000, host `0.0.0.0`)

### Still To Do
- [ ] Install `aice-mlflow-plugins==0.1.3` in the bank Python environment (internal package)
- [ ] Confirm Domino can reach Databricks MLflow from the KRONOS compute environment
- [ ] Flip `KRONOS_MLFLOW_ENABLED=true` when Databricks / AICE access is provisioned
- [ ] Paste real wrapper prompts into `pipeline/prompts.py` (replace TODO placeholders)
- [ ] Port remaining processors (`portfolio-summary`, `concentration-risk`, `delinquency-trends`, `risk-segments`, `exec-briefing`, `stress-outlook`) and register in `SCRIPT_MAP` — `firm-level` is the working reference pattern
- [ ] Build `deterministic_narrative_payload` in each processor — mode-scoped, replaces `commentary_facts` as primary LLM input
- [ ] (optional) Delete the mock data path entirely once the Domino deploy is stable and demos no longer need a backend-less fallback
- [ ] Update `prompts.json` with real mode slugs and prompt language for actual use cases
- [ ] Test with real `.xlsx` files end-to-end
- [ ] Test follow-up turns with metrics update
- [ ] Test structured claims output with real processor data
- [ ] Deploy and smoke test on Domino
- [ ] (Future) Register KRONOS as AICE Studio served model via `mlflow.pyfunc.log_model(python_model="pipeline/agent.py")`
