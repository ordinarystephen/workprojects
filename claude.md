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
  server.py               ← Flask app: static files + /upload + /modes +
                             /cube/parameter-options + /errors/recent
                             Calls activate_mlflow() at import, wraps /upload in mlflow_run()
  requirements.txt        ← flask, pandas, openpyxl, pyyaml, langchain, langchain-openai,
                             langgraph, langgraph-checkpoint, azure-identity, mlflow[databricks]
  EXPLAINME.md            ← Plain-English integration guide for non-developers
  VSCODE_CHEATSHEET.md    ← VS Code navigation shortcuts for Python tracing
  LANGCHAIN_AZURE_FORMULA.md  ← Reusable LangGraph + AzureChatOpenAI recipe (app-agnostic)
  UI_tweaks.md            ← Cheatsheet: where to adjust fonts, layout, card order
  PULL_ME.md              ← Sync checklist (per-round) for Domino workspace pulls
  /static
    index.html            ← Main UI
    styles.css            ← Full design system + dark mode
    main.js               ← All frontend interactions + fetch() calls to Flask
                             (loads buttons from /modes; no static prompts.json)
  /config                 ← Source of truth for the mode registry
    modes.yaml            ← All mode definitions (slug, display, parameters,
                             cube_slice, prompt_template, status). Validated at
                             app import by pipeline/registry.py.
    /prompts              ← Per-mode prompt templates (markdown, {{param}} subst)
      default.md          ← Fallback for placeholder / unparameterized modes
      firm_level.md
      portfolio_summary.md
      portfolio_level.md
      portfolio_comparison.md
  /docs
    calculation-audit.md  ← Read-only audit of cube + slicers (gaps, recs)
  /pipeline
    __init__.py           ← Package marker (empty)
    registry.py           ← YAML-driven mode registry. ModeDefinition (pydantic),
                             @register_slicer decorator, load_registry(),
                             get_mode/list_active_modes/list_modes_for_ui,
                             resolve_parameter_options, validate_parameters,
                             load_prompt (with {{param}} substitution).
    analyze.py            ← Dispatcher: registry lookup → classify → compute cube → slice
                             Calls get_slicer(mode.cube_slice) and invokes its fn.
                             Raises mode_not_implemented for placeholder modes.
    /templates            ← Template ABC + concrete templates per workbook shape
      base.py             ← Template ABC + FieldSpec + Role + ValidationWarning
      lending.py          ← LendingTemplate (37 columns tagged: hierarchy, period,
                             ratings, dims, horizontal flags, stocks, numerators)
    /scales               ← Domain rating scales
      pd_scale.py         ← C00…CDF (15 buckets). Five-category classification:
                             IG = C00–C07, NIG = C08–C13, Defaulted = CDF,
                             Non-Rated = {TBR, NTR, UNRATED, NA, N/A, #REF, blank}.
                             Distressed = C13 (a sub-stat of NIG, not a peer bucket).
                             Helpers: code_for_pd, is_investment_grade, is_non_rated,
                             distressed_code, defaulted_code, NON_RATED_TOKENS, direction.
    /parsers              ← Field-value parsers
      regulatory_rating.py← Split-rating parser ("SS - 18%, D - 42%, L - 40%")
    /loaders              ← Workbook-level entry
      classifier.py       ← Reads all sheets, matches each to a Template by SIGNATURE,
                             validates, returns { classified: {name: df}, metadata }
    /cube                 ← Compute-once layer
      models.py           ← Pydantic schema (LendingCube, KriBlock, GroupingHistory,
                             ContributorBlock, FacilityContributor, MomBlock, …)
                             — all extra="forbid"
                             LendingCube.available_portfolios → sorted by_industry keys
      lending.py          ← compute_lending_cube(df) — firm-level + by_industry +
                             by_horizontal + by_ig_status (IG, NIG) + by_defaulted +
                             by_non_rated + nig_distressed_substats (C13 subset) +
                             watchlist + top_contributors
                             + top_wapd_facility_contributors (firm + per-horizontal)
                             + month_over_month (when ≥ 2 periods). Emits
                             pd_rating_unclassified warning if any rows fall outside
                             the five-category mask (IG | NIG | Defaulted | Non-Rated).
    /processors           ← Slicers (mode → cube subset → context+metrics+verifiable_values)
      lending/firm_level.py        ← @register_slicer("firm_level")
                                     Sections: Firm-Level Overview, Rating Category
                                     Composition (IG / NIG / of-which Distressed /
                                     Defaulted / Non-Rated), Horizontal Portfolios,
                                     Watchlist
      lending/portfolio_summary.py ← @register_slicer("portfolio_summary")
                                     Executive view. Sections: Headline, Rating Category
                                     Composition, Top Industries, Top Parents, Top WAPD
                                     Drivers (Facility), Watchlist, Period Movement
    agent.py              ← LangGraph StateGraph + ResponsesAgent (bank-standard model-from-code)
                             • State (incl. prior_narrative for follow-ups), Context,
                               MlflowConfigAgentContext (pydantic)
                             • create_llm() — AzureChatOpenAI with DefaultAzureCredential
                             • _build_message_sequence(state) — emits first-turn shape
                               or [System, Human(context), AI(prior), Human(question)]
                               when prior_narrative is set
                             • narrate() / anarrate() — sync + async nodes with structured output
                             • load_graph() — builds StateGraph
                             • ask_agent(context, prompt, mode, system_prompt,
                               prior_narrative="") — Flask-facing entry point
                             • LangGraphResponsesAgent — MLflow model wrapper
                             • mlflow.models.set_model(...) — model-from-code registration
    tracking.py           ← MLflow tracking layer — GATED by KRONOS_MLFLOW_ENABLED
                             • activate_mlflow() — called once at app import
                             • mlflow_run() — context manager around each /upload
                             • _NoOpRun / _ActiveRun — dual-mode run handle
    error_log.py          ← Two-tier error capture (always-on JSONL + opt-in MLflow).
                             See "Error Logging Layer" section below.
    validate.py           ← cross_check_numbers(): compares narrative figures vs source data;
                             verify_claims(): per-claim resolve against verifiable_values
                             (mismatch vs field_not_found distinction)
    /tests                ← pytest suite
      test_registry.py    ← YAML schema validation, slicer cross-check, parameter resolve
      test_validate.py    ← Date stripping, claim verification edge cases
      test_label_collisions.py ← Catches verifiable_values label collisions across slicers
```

**Note:** `pipeline/prompts.py` and `static/prompts.json` were removed in the registry refactor. All prompt text now lives under `config/prompts/*.md` and all mode metadata in `config/modes.yaml`. Frontend fetches the button list from `GET /modes` instead of bundling a static JSON. `pipeline/llm.py` was removed earlier — LLM construction is inline in `pipeline/agent.py` (`create_llm()`).

### Real Codebase Mapping (existing Domino app → KRONOS)

| Existing file | Role in KRONOS |
|---|---|
| `processor.py` | `pipeline/analyze.py` dispatcher + per-mode slicers under `pipeline/processors/<template>/` self-registered via `@register_slicer` |
| `agent_client.py` | Replaced by `pipeline/agent.py` (LangGraph StateGraph + ResponsesAgent wrapper) |
| `workbook_agent.py` | Replaced by `create_llm()` inside `pipeline/agent.py` |
| `kronos/llm/prompts/prompts.py` | Replaced by `config/prompts/*.md` (per-mode templates) + `config/modes.yaml` (registry); loaded via `pipeline/registry.py::load_prompt()` |
| `main.py` (Streamlit) | Replaced by `server.py` (Flask) + `static/main.js` (frontend) |
| (new in KRONOS) | `pipeline/tracking.py` — MLflow audit logging with kill switch |
| (new in KRONOS) | `pipeline/error_log.py` — always-on JSONL + opt-in MLflow error capture |
| (new in KRONOS) | `pipeline/registry.py` — YAML-driven mode registry + slicer decorator |

### API Routes

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/` | Serves `index.html` |
| `GET` | `/modes` | Returns the list of UI-visible modes from the registry (`config/modes.yaml`). Frontend renders the Quick Analysis buttons from this — no static `prompts.json`. |
| `GET` | `/cube/parameter-options?mode=<slug>` | For parameterized modes (e.g. `portfolio-level`), returns the live option list pulled from the cube (e.g. industry names). Used to populate dropdowns at request time. |
| `POST` | `/upload` | Receives file + prompt + mode + parameters + prior_narrative, runs pipeline, returns JSON. |
| `GET` | `/errors/recent` | Tail of the JSONL error log. Gated by `KRONOS_ERRORS_ENDPOINT_ENABLED` (404 when disabled). |

### POST /upload Transports

The route accepts **two request shapes**; pick whichever works through the proxy in front of it.

| `Content-Type` | Body | When to use |
|---|---|---|
| `application/json` | `{ file_name, file_b64, prompt, mode, parameters, prior_narrative }` (file_b64 = base64 of the workbook bytes; parameters = object of `{name: value}`; prior_narrative = previous turn's narrative for follow-ups) | **UI default.** Required on Domino workspace URLs (`/workspace/<id>/proxy/<port>/`) — the workspace proxy silently drops multipart POSTs |
| `multipart/form-data` | `file` + `prompt` + `mode` + `parameters` (JSON string) + `prior_narrative` as form fields | curl, local dev, and Domino **published apps** (different proxy, handles multipart) |

`server.py` branches on `request.is_json`. The JSON path decodes `file_b64` and wraps the bytes in a tiny `_Base64File` shim that exposes `.read()`, `.filename`, and `.content_length` — from `analyze()` / processors downstream, the upload looks identical whichever transport was used.

`parameters` is a JSON object of mode-specific arguments (e.g. `{"portfolio": "Energy"}` for `portfolio-level`). Validated by `pipeline/registry.py::validate_parameters()` against the mode's declared schema before the slicer runs. `prior_narrative` is the previous turn's narrative text — present only on follow-up requests, threaded through `agent.py::_build_message_sequence()` to seed the conversation.

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
User uploads file + selects mode + enters prompt (+ optional parameters)
      ↓
POST /upload  (application/json from UI: { file_name, file_b64, prompt, mode,
                                            parameters, prior_narrative }
               or multipart/form-data from curl/App: same fields)
      ↓
server.py
  └─ with mlflow_run(mode, file_name, file_size, user_prompt) as run:   ← pipeline/tracking.py
        (no-op when KRONOS_MLFLOW_ENABLED is unset)
        ↓
        get_mode(slug)                               ← pipeline/registry.py
          └─ Looks up ModeDefinition in registry loaded from config/modes.yaml
          └─ Raises mode_not_implemented for placeholder modes
        ↓
        validate_parameters(mode, params, cube=None) ← pipeline/registry.py (pre-cube pass)
        ↓
        analyze(file, mode, parameters)              ← pipeline/analyze.py
          └─ classify(file)                          ← pipeline/loaders/classifier.py
                └─ matches each sheet to a Template by SIGNATURE
          └─ compute_lending_cube(df)                ← pipeline/cube/lending.py
                └─ firm-level + by_dim + watchlist + top contributors + MoM
          └─ validate_parameters(mode, params, cube) ← second, cube-aware pass
                (e.g. portfolio name must be in cube.available_portfolios)
          └─ get_slicer(mode.cube_slice)(cube, parameters)
                                                     ← @register_slicer-decorated function
                └─ returns { context, metrics, verifiable_values }
        ↓
        load_prompt(mode, parameters)                ← pipeline/registry.py
          └─ Reads config/prompts/<template>.md, substitutes {{param}} placeholders
        ↓
        ask_agent(context, prompt, mode, system_prompt, prior_narrative)
                                                     ← pipeline/agent.py
          └─ _get_graph() — cached CompiledStateGraph
          └─ graph.invoke(state with prior_narrative populated for follow-ups)
               └─ _build_message_sequence(state)
                    └─ first turn: [System, Human(context+question)]
                    └─ follow-up:  [System, Human(context), AI(prior), Human(question)]
               └─ narrate() node
                    └─ create_llm()                  ← AzureChatOpenAI + bearer token
                    └─ llm.with_structured_output(NarrativeResponse)
                                                     → narrative + claims list
                                                     OR plain text fallback → claims = []
          └─ returns { narrative, claims }
          └─ (autolog captures chain trace if MLflow active)
        ↓
        cross_check_numbers(narrative, context)      ← pipeline/validate.py
        verify_claims(claims, verifiable_values)     ← pipeline/validate.py
        ↓
        run.log(metrics)                             ← logs latency, counts, artifacts
      ↓
server.py returns { narrative, metrics, claims, context_sent, verification, timings_ms }
      ↓
main.js renders:
  - Analysis tab (narrative text)
  - Claims tab (structured citation cards)
  - Verification badge (green=all verified, amber=some unverified)
  - "View source data" expandable panel
  - Data Snapshot metric tiles
```

### `mode` Routing

Every request carries a `mode` slug (e.g. `"portfolio-summary"`) sent from the frontend. `pipeline/registry.py::get_mode(slug)` looks up the `ModeDefinition` (loaded from `config/modes.yaml` at app import). The dispatcher in `pipeline/analyze.py` runs the classifier, computes the cube once, then calls the slicer registered under `mode.cube_slice` via `@register_slicer`. Mode slugs come from the registry — the frontend fetches them from `GET /modes`, so they're never hard-coded in two places.

Custom questions (no canned button selected) send `mode = ''` — `analyze()` falls back to `placeholder_processor()`.

**Currently wired modes:**
| Slug | Template | Slicer | Status |
|---|---|---|---|
| `firm-level` | `lending` | `@register_slicer("firm_level")` in `pipeline/processors/lending/firm_level.py` | **Live** |
| `portfolio-summary` | `lending` | `@register_slicer("portfolio_summary")` in `pipeline/processors/lending/portfolio_summary.py` | **Live** |
| `portfolio-level` / `portfolio-comparison` | `lending` | — | **Parameterized placeholder** — registered with `parameters:` in YAML; raises `mode_not_implemented` until slicer is wired |
| `concentration-risk` / `delinquency-trends` / `risk-segments` / `exec-briefing` / `stress-outlook` | — | — | Plain placeholders — visible in UI, raises `mode_not_implemented` |

The classifier matches sheets by `LendingTemplate.SIGNATURE` (`Facility ID`, `Weighted Average PD Numerator`, `Committed Exposure`). A workbook with no matching sheet raises `ValueError` (surfaced as a 500 with the sheets-seen list). Adding a new lending mode means: (a) writing a slicer in `pipeline/processors/lending/` decorated with `@register_slicer("name")`, (b) adding a `ModeDefinition` block to `config/modes.yaml` referencing that slicer name in `cube_slice:`, (c) writing a prompt template at `config/prompts/<name>.md`. Adding a new workbook shape (e.g. Traded Products) means writing a new Template in `pipeline/templates/` and appending it to `classifier.TEMPLATES`.

**Lending workbook field names** (header strings the classifier expects, not arbitrary): `Regulatory Rating` (was `Current Month Regulatory Rating`), `Credit Watch List Flag` (was `Credit Watchlist Flag`). Workbooks using the old names will fail classification with a missing-required-column error.

**Cube — what's available to slicers** (from `compute_lending_cube`):
- `firm_level: GroupingHistory` — current + history KriBlocks
- `by_industry / by_segment / by_branch / by_horizontal: dict[str, GroupingHistory]`
- `by_ig_status: dict[str, GroupingHistory]` — keys are `"IG"` (C00–C07) and `"NIG"` (C08–C13). **NIG no longer absorbs CDF or Non-Rated codes** — those live in `by_defaulted` and `by_non_rated` respectively
- `by_defaulted: dict[str, GroupingHistory]` — single-key dict for PD = CDF (terminal state, not part of NIG)
- `by_non_rated: dict[str, GroupingHistory]` — single-key dict for placeholder PD values (TBR / NTR / Unrated / NA / N/A / #REF / blank). Data-quality signal, not a credit assessment
- `nig_distressed_substats: DistressedSubstats | None` — latest-period C13 subset (`period`, `committed`, `outstanding`, `facility_count`). Reported as an "of which" sub-line under NIG; Distressed is a sub-stat, not a peer bucket
- `watchlist: WatchlistAggregate` (firm-level Watch-List Flag = "Y")
- `top_contributors: ContributorBlock` — parent-level top-10 by committed/outstanding/wapd_numerator/cc_exposure
- `top_wapd_facility_contributors: list[FacilityContributor]` — facility-level top-10 by `Weighted Average PD Numerator`. Each carries facility/parent IDs, committed, numerator, `implied_pd` (numerator ÷ committed), `pd_rating`, `regulatory_rating`, `share_of_numerator` (within scope)
- `wapd_contributors_by_horizontal: dict[str, list[FacilityContributor]]` — same shape as above, computed per horizontal portfolio. Stored in cube; not yet rendered by any slicer
- `month_over_month: MomBlock | None` — populated only when ≥ 2 periods are uploaded

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

## Error Logging Layer (`pipeline/error_log.py`)

Two-tier error capture that runs alongside MLflow. Unlike tracking (dormant by default), the JSONL tier is **always on** so there's an audit trail even before Databricks access is provisioned.

### Tiers

| Tier | When active | What it does |
|---|---|---|
| **JSONL** | Always | Appends one JSON record per error event to `<KRONOS_ERROR_LOG_DIR>/kronos-errors.jsonl`. Rotates on ≥10 MB or date change. |
| **MLflow** | `KRONOS_MLFLOW_ENABLED=true` AND a run is active | Adds `kronos.has_error=true` tag, `kronos_error_<event_type>_count` metric, and a per-event JSON artifact. |

Both tiers are best-effort. A logging failure never raises.

### Entry point

```python
from pipeline.error_log import log_error

log_error(
    "llm_failed",             # event_type — stable slug
    error=exc,                # optional exception
    mode="portfolio-summary",
    parameters={...},
    session_id=sess,          # from X-Kronos-Session header
    user_prompt=prompt,       # truncated to 250 chars
    context_snippet=context,  # truncated to 500 chars
    # …free-form crumbs allowed via **kwargs
)
```

### Event types currently emitted from `server.py`

| Event type | Where |
|---|---|
| `upload_parse_failed` | base64 decode / parameters JSON parse failures |
| `parameter_validation_failed` | pre-validate + cube-aware validate |
| `mode_not_implemented` | placeholder mode invoked |
| `classification_failed` | missing template sheet / signature collision |
| `slicer_failed` | generic slicer exception |
| `llm_failed` | `ask_agent()` exception |
| `verification_mismatch` | at least one claim cited a known field with the wrong value |
| `cube_parameter_options_failed` | parameter option resolution failure |

### Field policy

Logged: timestamp, event_type, mode, parameters, session_id, error_class, error_message, stack_trace, bounded context_snippet (≤500 chars), bounded user_prompt (≤250 chars), caller-supplied `additional` dict.

Never logged: full file contents, full narratives, full user prompts, verifiable values (may carry PII / exposure amounts).

### Session ID

Frontend generates a per-tab UUID (`crypto.randomUUID()` with a hex-token fallback) on first page load, persists it in `sessionStorage` under `kronos.session_id`, and sends it on every request as `X-Kronos-Session`. Server reads and passes it into each `log_error()` call. Not auth — correlation only.

### `GET /errors/recent`

Gated by `KRONOS_ERRORS_ENDPOINT_ENABLED`. Returns the tail of the active JSONL file (default 50, cap 500). Returns 404 (not 403) when disabled so the endpoint is indistinguishable from a missing route. MLflow-backed aggregation is a separate future feature.

### Environment variables

| Variable | Default | Notes |
|---|---|---|
| `KRONOS_ERROR_LOG_DIR` | `logs/` | Directory for the JSONL file. **TODO — confirm Domino persistent path** (`/domino/datasets/...` conventionally, but verify per-deployment before pinning). |
| `KRONOS_ERRORS_ENDPOINT_ENABLED` | unset (off) | Set to `true` to enable `GET /errors/recent`. |

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
1. **Quick Analysis** — grid of canned prompt buttons fetched from `GET /modes` at page load (sourced from `config/modes.yaml`). Modes flagged `parameterized: true` open a parameter dropdown populated from `GET /cube/parameter-options?mode=...` after a file is uploaded.
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
- **Follow-up input** — slides into narrative column, submits via Send or `Cmd/Ctrl+Enter`. Closing the FAB with a typed draft prompts to confirm before discarding
- **Empty submit** — shake + brief CTA-tinted background on the textarea (instead of silent no-op)
- **Submitting state** — Send button disables, shows spinner + "Sending…", textarea becomes readonly with `aria-busy`. Module-level `followupInFlight` guard early-returns on double-submit (e.g. a second Cmd+Enter while readonly)
- **AbortController + 60s timeout** — every follow-up fetch is armed with an AbortController. `runAnalysis()` and the New Analysis button call `abortFollowup()` so a parent reset cancels the in-flight request instead of leaking state into a hidden DOM. A 60s timer fires `controller.abort('timeout')` so a hung Domino proxy can't pin the UI in submitting state forever
- **Error path** — non-2xx, `{error}` body, or timeout surfaces as an inline red `.followup-error` row above the textarea with a Retry button (auto-scrolled into view). Retry re-reads the textarea so the user can edit before resending. Parent-aborts return silently
- **Thread structure** — divider → thinking dots → message block with label + timestamp
- **Metrics update** — follow-up with new metrics re-renders the Data Snapshot panel
- **Conversation inheritance** — every follow-up reuses the original turn's `mode`, `parameters`, AND uploaded `file_b64` so the cube isn't re-derived from a different scope. The previous turn's narrative is sent as `prior_narrative`; `agent.py::_build_message_sequence()` shapes it as `[System, Human(context), AI(prior_narrative), Human(question)]` so the LLM sees genuine conversational history rather than a stateless single-shot

### Loading State — Cancel Button
- The pipeline-progress card on the loading screen has a Cancel button. It calls `primaryController.abort('user-cancel')` on the in-flight `runAnalysis()` fetch and returns the UI to the input state. Same `AbortController` pattern as the FAB follow-up — a hung backend never traps the user on the spinner.

Note: follow-ups currently hit stateless `/upload` and seed history via `prior_narrative`. When true multi-turn state with full token-level memory is needed, LangGraph's checkpointing capability is already installed — just add a checkpointer to `load_graph()` and a `thread_id` per session.

### Pipeline Step Timings
The landing-page pipeline animation previously used hardcoded fake delays (`STEP_DELAYS = [800, 1400, 600, 0, 500]`). Now:
- `STEP_DELAYS` shrunk to `[150, 250, 100, 0, 150]` — short visual placeholders so the animation doesn't block on theater
- Server returns `timings_ms: { analyze, llm, verify }` on every `/upload` response
- After `apiResult` resolves, `runAnalysis()` overwrites `time-0`…`time-4` labels with values derived from `timings_ms` (analyze split across steps 0+1, llm → step 3, verify → step 4)
- If `timings_ms` is absent (old server, mock fallback), wall-clock labels remain — nothing breaks

**Known limitation:** the *animation pacing* during the wait is still artificial — only the *displayed numbers* are honest. For true live-advancing progress, a streaming NDJSON endpoint would be needed (deferred due to Domino proxy-buffering risk).

---

## YAML Mode Registry

The mode list, per-mode metadata, and prompt templates all live in `config/` and are loaded once at app import by `pipeline/registry.py`. There is no `prompts.json` and no `pipeline/prompts.py` — both were deleted in this refactor.

### `config/modes.yaml`

Each mode is a `ModeDefinition` (pydantic, `extra="forbid"`):

```yaml
modes:
  - slug: portfolio-summary
    template: lending
    cube_slice: portfolio_summary       # must match a @register_slicer name
    prompt_template: portfolio_summary  # config/prompts/portfolio_summary.md
    display:
      title: "Portfolio Summary"
      desc:  "Executive view"
    status: active                      # active | parameterized | placeholder
    parameters: []                      # optional list of ParameterSpec

  - slug: portfolio-level
    template: lending
    cube_slice: portfolio_level         # not yet wired → mode_not_implemented
    prompt_template: portfolio_level
    status: parameterized
    parameters:
      - name: portfolio
        source: cube.available_portfolios
        required: true
```

### `config/prompts/*.md`

One markdown file per `prompt_template`. `pipeline/registry.py::load_prompt(mode, parameters)` reads the file and substitutes `{{name}}` placeholders against the validated parameters dict. The `default.md` fallback is used by the placeholder modes.

### Adding a new mode (checklist)
1. Write the slicer in `pipeline/processors/<template>/` and decorate it `@register_slicer("name")`.
2. Append a `ModeDefinition` block to `config/modes.yaml` with `cube_slice: name`.
3. Add `config/prompts/<prompt_template>.md`.
4. Restart the app — `pipeline/registry.py` validates the YAML against the slicers found, and `GET /modes` will surface the new button automatically. No frontend change needed.

### Tests
`pipeline/tests/test_registry.py` enforces: YAML parses, every `cube_slice:` resolves to a registered slicer, every `prompt_template:` file exists, and parameter sources are valid. `pipeline/tests/test_label_collisions.py` catches `verifiable_values` label collisions across slicers.

---

## Transparency Layer

Three features for output validation and traceability:

### 1. Structured Claims (Claims Tab)
`pipeline/agent.py` uses `llm.with_structured_output(NarrativeResponse)` inside the narrate node. Forces the LLM to return narrative + a list of factual claims, each citing its source field and value. Falls back to plain `llm.invoke()` if the model doesn't comply — the narrative always renders, claims tab shows empty state.

### 2. Number Cross-Check (Verification Badge)
`pipeline/validate.py` extracts all numeric tokens from the narrative and checks whether each appears in the `context` string. Returns counts of verified vs unverified figures. Unverified doesn't mean wrong — calculated metrics (weighted averages, deltas) won't appear verbatim in source data.

Dates are stripped from both sides before tokenization (ISO `YYYY-MM-DD` and prose `Month DD, YYYY`). Without this, an as-of date in the context (`2026-02-28`) and the LLM's prose rewrite of it (`February 28, 2026`) tokenize differently and produce a false-positive unverified.

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
- [x] `pipeline/analyze.py` — Dispatcher: registry lookup → classify → cube → `get_slicer()` call. Placeholder fallback for unwired modes
- [x] `pipeline/registry.py` — **YAML-driven mode registry.** `@register_slicer` decorator, `load_registry()` validation, `validate_parameters(mode, params, cube)`, `load_prompt(mode, parameters)` with `{{param}}` substitution, `resolve_parameter_options()` for cube-backed dropdowns
- [x] `config/modes.yaml` + `config/prompts/*.md` — Source of truth for mode list, UI labels, parameter schemas, and per-mode prompt text. Replaces deleted `pipeline/prompts.py` and `static/prompts.json`
- [x] `pipeline/agent.py` — **LangGraph StateGraph + ResponsesAgent (bank-standard model-from-code pattern)** with `with_structured_output(NarrativeResponse)` inside the narrate node + plain text fallback. `_build_message_sequence()` threads `prior_narrative` into follow-up turns as `[System, Human(context), AI(prior), Human(question)]`
- [x] `pipeline/tracking.py` — **MLflow tracking layer gated by `KRONOS_MLFLOW_ENABLED`** (off by default). Logs tags, params, metrics, artifacts per request. Wraps `mlflow.start_run()` + `autolog()`
- [x] `pipeline/error_log.py` — Two-tier error capture (always-on JSONL + opt-in MLflow). Event-type slugs emitted from `server.py` for every failure class
- [x] `pipeline/validate.py` — `cross_check_numbers()` numeric token cross-check + `verify_claims()` structured-claim verification against `verifiable_values`. ISO + prose date stripping suppresses date-rewrite false positives
- [x] `pipeline/tests/` — pytest suite: `test_registry.py` (YAML schema + slicer cross-check), `test_validate.py` (date stripping, claim edge cases), `test_label_collisions.py`
- [x] `requirements.txt` — flask, pandas, openpyxl, langchain, langchain-openai, **langgraph, langgraph-checkpoint**, azure-identity, **mlflow[databricks]**
- [x] `EXPLAINME.md` — Plain-English walkthrough of every file + integration steps
- [x] `VSCODE_CHEATSHEET.md` — Python import tracing + VS Code navigation shortcuts
- [x] `aboutme.md` — Full integration guide with real architecture, checklist, work AI prompt
- [x] Git repo initialized, pushed to GitHub `kronos` branch
- [x] Narrative text dumps immediately (no typewriter delay)
- [x] `mode` field wired end-to-end: `GET /modes` → button → `activeMode` → request body → registry lookup → slicer
- [x] Bank-standard production pattern: LangGraph + ResponsesAgent + MLflow autolog (dormant)
- [x] FAB follow-up: loading state on Send, inline error row with Retry, FAB hidden during in-flight request
- [x] Real server-side stage timings returned in `/upload` response (`timings_ms`) and overwritten onto pipeline step labels; also logged to MLflow as `analyze_ms` / `llm_ms` / `verify_ms`
- [x] `PULL_ME.md` sync-checklist workflow established for Domino pulls
- [x] **Lending pipeline rebuilt as templates → classifier → cube → slicer.** New tree: `pipeline/templates/` (Template ABC + LendingTemplate with 37 columns tagged), `pipeline/scales/pd_scale.py` (C00…CDF + IG/NIG), `pipeline/parsers/regulatory_rating.py` (split-rating parser), `pipeline/loaders/classifier.py` (auto-detect by SIGNATURE), `pipeline/cube/` (pydantic schema + `compute_lending_cube` producing firm-level + by-industry/segment/branch/horizontal/IG-status + watchlist + top-contributors + MoM), `pipeline/processors/lending/firm_level.py` (first slicer). `pipeline/firm_level.py` deleted. Multi-period correctness: cube selects latest period for stock metrics, eliminating the prior Jan+Feb double-counting bug
- [x] `USE_MOCK_RESULTS` demo toggle in `main.js` — default `false` so real API errors are now visible. Flip to `true` to bypass Flask and render `getMockResult()` for presentations
- [x] **JSON upload transport + workspace proxy workaround.** UI posts `application/json` with a base64 file payload so uploads survive the Domino workspace proxy (which drops multipart). `server.py` branches on `Content-Type` and wraps the decoded bytes in a `_Base64File` shim — processors downstream see no difference. Multipart path preserved for curl / published Apps.
- [x] `main.js` resolves the fetch URL via `new URL('upload', document.baseURI).href` so the workspace URL prefix (`/aice-studio/workspace/<id>/proxy/5000/`) is preserved instead of being stripped by an absolute path.
- [x] `[KRONOS]`-prefixed `console.log` / `console.error` instrumentation around both `/upload` fetch sites — request shape, response status, parse errors, non-OK bodies, and raw `err.message` on network/CORS failures. Makes proxy-level failures diagnosable from DevTools alone.
- [x] **Verified end-to-end on Domino workspace (2026-04-20):** upload → firm-level processor → Azure OpenAI narration → tiles rendered successfully inside the AICE Studio workspace (port 5000, host `0.0.0.0`)
- [x] **FAB + follow-up state-machine hardening.** Module-level `followupController` (AbortController) + `followupInFlight` guard. `runAnalysis()` and the New Analysis button call `abortFollowup()` so a parent reset cancels the in-flight fetch. 60s fetch timeout fires `controller.abort('timeout')` so a hung Domino proxy can't pin the UI. Closing the FAB with a typed draft prompts to confirm. Empty submit triggers a shake+tint via reused `.shake` keyframe instead of silent no-op. Error row scrolls into view. Catch distinguishes timeout (user-facing message) vs parent abort (silent return)
- [x] **Validator suppresses date-rewrite false positives.** `pipeline/validate.py` strips ISO (`YYYY-MM-DD`) and prose (`Month DD, YYYY`) dates from both narrative and context before tokenizing, so a context as-of date and the LLM's prose rewrite of it no longer contribute mismatched bare numbers
- [x] **Portfolio Summary slicer wired.** `pipeline/processors/lending/portfolio_summary.py` decorated with `@register_slicer("portfolio_summary")`. Produces an executive view from the existing cube (headline scale, IG/NIG mix, top-5 industries, top-5 parents, watchlist, period-over-period when ≥ 2 periods). Real system prompt at `config/prompts/portfolio_summary.md`.
- [x] **Field renames in lending workbook header strings.** `Current Month Regulatory Rating` → `Regulatory Rating` and `Credit Watchlist Flag` → `Credit Watch List Flag` across `templates/lending.py`, `cube/lending.py`, `parsers/regulatory_rating.py` docs. Workbooks must use the new header names.
- [x] **Facility-level WAPD contributors.** New `FacilityContributor` model + `LendingCube.top_wapd_facility_contributors` (firm-level top 10) + `LendingCube.wapd_contributors_by_horizontal` (top 10 per horizontal). Each entry carries facility/parent IDs, committed, `wapd_numerator`, `implied_pd` (numerator ÷ committed), `pd_rating`, `regulatory_rating`, `share_of_numerator` (within scope). Surfaced in Portfolio Summary as both an LLM-context block and a "Top 5 WAPD Drivers (Facility)" tile section. Per-horizontal map computed and stored in cube; not yet rendered by any slicer.
- [x] **Reusable docs.** `LANGCHAIN_AZURE_FORMULA.md` (app-agnostic LangGraph + Azure OpenAI recipe) + `UI_tweaks.md` (cheatsheet for font/layout/card-order tweaks) at repo root.
- [x] **YAML-driven mode registry (Round 11A).** `config/modes.yaml` + `config/prompts/*.md` + `pipeline/registry.py` replace `pipeline/prompts.py` + `static/prompts.json`. Frontend fetches button list from `GET /modes`. Slicers self-register via `@register_slicer("name")`. Parameter schemas declared per-mode; cube-backed dropdowns served by `GET /cube/parameter-options`. App-import validation: every YAML `cube_slice:` must resolve to a registered slicer, every `prompt_template:` file must exist.
- [x] **Per-claim verifiable_values + verify_claims (Round 11B).** Slicers now return `verifiable_values: {label: value}` alongside `context` + `metrics`. `pipeline/validate.py::verify_claims()` resolves each LLM-returned claim against this dict, distinguishing mismatch (cited a known field with the wrong value) from field_not_found (cited a label outside the verifiable set). `pipeline/tests/test_label_collisions.py` ensures no two slicers ship colliding labels.
- [x] **Cancel button on the loading screen (Round 11C).** `primaryController` (AbortController) wraps `runAnalysis()`; Cancel calls `primaryController.abort('user-cancel')` and resets the UI. A hung backend can no longer trap the user on the spinner.
- [x] **Follow-up inheritance (Round 11D).** Every follow-up reuses the original turn's `mode`, `parameters`, AND uploaded `file_b64`, then sends the previous narrative as `prior_narrative`. `agent.py::_build_message_sequence()` shapes the input as `[System, Human(context), AI(prior), Human(question)]` so the LLM sees true conversational history. Sort determinism audit done across all cube outputs (explicit secondary tiebreakers: Ultimate Parent Code, Facility ID, rating code) so re-runs against the same data produce stable ordering.
- [x] **Two-tier error logging (Round 11E).** `pipeline/error_log.py` writes always-on JSONL (`logs/kronos-errors.jsonl`, rotates ≥10 MB or date change) and emits MLflow artifacts when active. Eight stable event-type slugs hooked into every failure path in `server.py`. Per-tab session ID via `X-Kronos-Session` header for correlation. `GET /errors/recent` (gated, 404-when-disabled) for tail inspection. Field policy: full file/narrative/prompt content never logged; bounded snippets only.
- [x] **Read-only deterministic-calculation audit (Round 12).** `docs/calculation-audit.md` — ten sections (overview, section inventory, KRI inventory matrix, sub-statistics audit, horizontal portfolio deep-dive, cross-section consistency checks, determinism audit, correctness spot-checks, ranked gaps, scoped recommendations). 19 ranked gaps identified. **Top high-severity findings (not fixed; documented for future work):** (1) IG/NIG silently buckets unrated PD codes as NIG via `~is_ig` mask in `pipeline/cube/lending.py:98-104`; (2) `_grouping_by_dim` drops NaN dim values via `.dropna().unique()` so by-industry/segment/branch sums don't reconcile against firm totals; (3) `cube.available_portfolios` returns industries but `portfolio-level`/`portfolio-comparison` modes read like horizontals — semantic ambiguity unresolved.
- [x] **Five-category rating-composition refactor (Round 13).** Closes the first high-severity Round 12 audit finding (IG/NIG unrated bucketing). `pipeline/scales/pd_scale.py` adds `NON_RATED_TOKENS`, `is_non_rated()`, `distressed_code()`, `defaulted_code()`, and narrows `non_investment_grade_codes()` to **C08–C13 only** (no longer absorbs CDF). `pipeline/cube/lending.py` replaces the `~is_ig` fallback with five explicit masks (`ig`, `nig`, `defaulted`, `non_rated`, `distressed`) computed from `PD Rating`; populates new `LendingCube` fields `by_defaulted`, `by_non_rated`, and `nig_distressed_substats` (latest-period C13 subset). New `DistressedSubstats` pydantic model (`extra="forbid"`, fields: `period`, `committed`, `outstanding`, `facility_count`). Any PD value outside the union of the four non-sub-stat masks emits a `pd_rating_unclassified` warning (code + count + up-to-10 sample values) to `CubeMetadata.warnings` and a `log.warning` line. Firm-level and portfolio-summary slicers updated: "Investment-Grade Split" / "Investment-Grade Mix" renamed to "Rating Category Composition"; Distressed rendered as an indented "of which" sub-line/tile under NIG; Defaulted and Non-Rated rendered as separate buckets. `verifiable_values` extended with `Defaulted`, `Non-Rated`, `Distressed (of which)`, `Distressed facility count`, plus `(% of rated commitment)` / `(% of total commitment)` / `(% of NIG)` percentage labels. `% of rated commitment` = IG / (IG + NIG) — legacy semantic preserved; Defaulted and Non-Rated use `% of total commitment`. Prompt templates at `config/prompts/firm_level.md` and `config/prompts/portfolio_summary.md` rewritten to narrate the new composition with explicit guidance: frame Distressed as NIG subset, Defaulted as separate terminal-state concern, Non-Rated as data-quality signal. Tile sentiments: IG=neutral, NIG=neutral, Distressed=warning, Defaulted=negative, Non-Rated=neutral. Upstream data-contract assumption flagged (unverifiable in code): the WAPD numerator is assumed to already treat Non-Rated facilities as C07-weighted — documented on `_weighted_average` docstring.

### Still To Do
- [ ] Install `aice-mlflow-plugins==0.1.3` in the bank Python environment (internal package)
- [ ] Confirm Domino can reach Databricks MLflow from the KRONOS compute environment
- [ ] Flip `KRONOS_MLFLOW_ENABLED=true` when Databricks / AICE access is provisioned
- [ ] Wire the parameterized placeholder modes (`portfolio-level`, `portfolio-comparison`): write the slicer + decorate `@register_slicer(...)`. Decide whether `cube.available_portfolios` should mean industries (current behavior) or horizontals (per Round 12 audit gap) before wiring; if horizontals, add `cube.available_horizontals`
- [ ] Wire the plain placeholder modes (`concentration-risk`, `delinquency-trends`, `risk-segments`, `exec-briefing`, `stress-outlook`) — slicer + YAML entry + `config/prompts/<name>.md`. Cube already computes the data each will need (by_industry, by_horizontal, top_contributors, top_wapd_facility_contributors, wapd_contributors_by_horizontal, month_over_month)
- [ ] Surface `wapd_contributors_by_horizontal` in a slicer (currently computed but unused — natural fit for a per-horizontal narrative section, or a future Concentration Risk slicer)
- [ ] Address the remaining high-severity gaps in `docs/calculation-audit.md` — IG/NIG unrated bucketing closed in Round 13; still open: `_grouping_by_dim` NaN drop (by-industry/segment/branch sums don't reconcile against firm totals) and `available_portfolios` semantics (industries vs horizontals ambiguity)
- [ ] Confirm with the data owner that the WAPD numerator (`Weighted Average PD Numerator`) already treats Non-Rated facilities as C07-weighted upstream — this is a data-contract assumption the cube cannot verify in code (flagged on `_weighted_average` docstring)
- [ ] Build `deterministic_narrative_payload` in each slicer — mode-scoped, replaces `commentary_facts` as primary LLM input
- [ ] Add `TradedProductsTemplate` (when field list is provided) and append to `classifier.TEMPLATES`
- [ ] (optional) Delete the mock data path entirely once the Domino deploy is stable and demos no longer need a backend-less fallback
- [ ] Test with real `.xlsx` files end-to-end
- [ ] Test follow-up turns with metrics update
- [ ] Test structured claims output with real processor data
- [ ] Deploy and smoke test on Domino
- [ ] (Future) Register KRONOS as AICE Studio served model via `mlflow.pyfunc.log_model(python_model="pipeline/agent.py")`
