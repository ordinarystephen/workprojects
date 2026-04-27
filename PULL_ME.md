# PULL ME — Files to sync to Domino

Running checklist of files that have changed in this repo and need to be
pulled into your Domino workspace. Check each off as you copy it over.
**Delete this file once the list is empty and you're caught up.**

Add new sections (below) for each new round of changes. Oldest at the top.

---

## Round 1 — Real pipeline timings (Option B)

Commit: _see `git log` on branch `kronos`_

Server now measures each pipeline stage with `time.perf_counter()` and
returns a `timings_ms` object on the /upload response. The frontend uses
those values to overwrite the wall-clock step time labels with honest
per-stage durations after the API resolves. The fake step delays are
reduced to short visual placeholders so we don't block on theater.

- [ ] `server.py` — added `import time`, wrapped `analyze()`, `ask_agent()`, `cross_check_numbers()` with `perf_counter()`, added `timings_ms: { analyze, llm, verify }` to the JSON response. Also logs the same stage durations to MLflow (`analyze_ms`, `llm_ms`, `verify_ms`) when tracking is enabled.
- [ ] `static/main.js` — shrank `STEP_DELAYS` from `[800, 1400, 600, 0, 500]` to `[150, 250, 100, 0, 150]`; after `apiResult` resolves, overwrites `time-0`…`time-4` labels using `apiResult.timings_ms` (split `analyze` across steps 0+1, map `llm` to step 3, `verify` to step 4).

**Behavior change:** the step animation runs faster during the wait, and the final displayed times reflect real server work. The LLM step will visibly be the longest — which is the truth.

**Compatibility:** if a response doesn't include `timings_ms` (old server, mock fallback), the wall-clock times are used — nothing breaks.

---

## Round 2 — Wire up the firm-level processor

Commit: _see `git log` on branch `kronos`_

First real processor script wired into `SCRIPT_MAP`. Uploads a workbook, runs
the `firm-level` calculations, sends the resulting figures to the LLM for
narration, and populates the Data Snapshot tiles with the six firm-level
metrics. The other five canned buttons still fall back to
`placeholder_processor()` until their scripts are ported.

- [ ] `pipeline/firm_level.py` — **NEW FILE.** Adapts the reference script to the `run(file_obj) -> { context, metrics }` contract. Reads the upload via `io.BytesIO`, validates required columns, coerces numerics, computes distinct parents, distinct industries, total commitment ($M), total outstanding ($M), criticized & classified ($M), and C&C % of commitment. Builds a prose context string for the LLM and a `"Firm-Level Overview"` metrics section with six tiles.
- [ ] `pipeline/analyze.py` — added `from pipeline import firm_level` at the top; registered `"firm-level": firm_level.run` as the first entry in `SCRIPT_MAP`.
- [ ] `pipeline/prompts.py` — added a `"firm-level"` entry to `MODE_SYSTEM_PROMPTS` with a wrapper prompt tuned for the six-figure snapshot (cite numbers verbatim, frame C&C, no invented trends).
- [ ] `static/prompts.json` — added a new `Firm-Level View` button at position 1, mode slug `firm-level`. The original six buttons are preserved below it (portfolio-summary, concentration-risk, etc.). The Quick Analysis grid now has seven tiles; the last row will have one item by itself until we consolidate.

**Required workbook columns** (firm-level mode fails fast with a clear error if any are missing):
`Ultimate Parent Code`, `Risk Assessment Industry`, `Committed Exposure`,
`Outstanding Exposure`, `Special Mention Rated Exposure`,
`Substandard Rated Exposure`, `Doubtful Rated Exposure`,
`Loss Rated Exposure`.

**Behavior change:** clicking *Firm-Level View* on the landing page now runs a real calculation against the uploaded file, sends the computed figures to Azure OpenAI for narration, and shows the six metric tiles. Other canned buttons are unchanged — they still use the placeholder processor.

**Compatibility:** missing-column case raises `ValueError` inside `analyze()`, which `server.py` already catches and returns as a 500 with the message. The frontend surfaces it in the existing error UI.

---

## Round 3 — Real results by default, mock behind a demo toggle

Commit: _see `git log` on branch `kronos`_

The frontend no longer silently falls back to mock data when the backend
fails. Real API calls are the default; failures surface as a visible error
message in the narrative column. The mock path is preserved as an opt-in
demo mode controlled by a single flag.

- [ ] `static/main.js` — added `const USE_MOCK_RESULTS = false;` near the top. `runAnalysis()` now wraps `/upload` to return `{ __error }` on non-2xx or network failure, and renders `"Error: ..."` as the narrative instead of falling back to mock. `submitFollowup()` no longer treats network failures as a silent mock fallback — they become an inline error with Retry. In demo mode (flag true), both `runAnalysis()` and `submitFollowup()` skip the network entirely and use `getMockResult()`. The `getMockResult()` function itself is kept unchanged. Also removed the three `TODO REMOVE ON MERGE` comments since the mock is now a supported demo mode, not a temporary shim.

**Behavior change:** If `/upload` 500s (e.g. firm-level fails because the workbook is missing a required column), the user now sees the real error message in the results panel — which is what you want while wiring up real processors. Previously they would have seen a generic fake narrative.

**Demo mode:** flip `USE_MOCK_RESULTS = true` in `static/main.js` before a presentation to render the hard-coded mock narrative and tiles without touching Flask.

---

## Round 4 — Domino workspace bind + browser-side upload diagnostics

Commit: _see `git log` on branch `kronos`_

While debugging an upload failure inside a Domino workspace (Windows VM,
workspace-forwarded URL), the Flask server was switched to bind on all
interfaces and the frontend was given verbose console instrumentation
around the `/upload` fetch so the next failure is easier to diagnose.

- [ ] `server.py` — changed `app.run(port=8888, debug=True)` to `app.run(debug=True, host="0.0.0.0", port=5000)`. Required for Domino workspace forwarding (default `127.0.0.1` bind isn't reachable from the proxy; port 8888 was already in use inside the workspace container). If you publish as a Domino App later, revert to `port=8888` — that's the published-app convention.
- [ ] `static/main.js` — added `[KRONOS]` `console.log` / `console.error` lines around both `/upload` fetch sites (`runAnalysis()` and `submitFollowup()`). Logs the request shape pre-fetch (URL, mode, file name/size, prompt length), the response (status, ok, type, resolved URL) on receipt, JSON-parse failures, non-OK responses with body, and network/CORS/proxy errors with the underlying `err.message`. The user-facing "Network error — could not reach the server." message now includes the raw error message in parens. Both fetches use the absolute `'/upload'` path.

**Behavior change:** none for the happy path. On any failure, DevTools Console now shows what the browser actually attempted and how it failed — needed because Domino's workspace proxy can drop the POST silently before it reaches Flask.

**Investigating an upload failure on Domino workspace:** open DevTools → Console, trigger the upload, and read the `[KRONOS]` lines. If the `/upload fetch failed` line shows `TypeError: Failed to fetch` with no response, the workspace proxy is blocking the POST — the fix is to publish the app via the Domino App tab (uses a different proxy that handles POST + multipart) and revert the port to 8888.

---

## Round 5 — JSON-encoded upload to dodge workspace-proxy multipart block

Commit: _see `git log` on branch `kronos`_

Confirmed via DevTools that the Domino workspace proxy at
`/workspace/<id>/proxy/5000/` was silently dropping `multipart/form-data`
POSTs (`net::ERR_FAILED` before the request hit Flask). Rather than publish
as a Domino App, the frontend now ships the file inside a JSON body as
base64, which the workspace proxy passes through cleanly.

- [ ] `static/main.js` — `runAnalysis()` and `submitFollowup()` no longer build a `FormData`. Both read the file via a new `fileToBase64()` helper and POST `application/json` with `{ file_name, file_b64, prompt, mode }`. Also fixed a latent bug in the follow-up log that referenced an undefined `userMessage.length` (now `question.length`).
- [ ] `server.py` — `/upload` now branches on `Content-Type`. JSON path decodes `file_b64`, wraps the bytes in a new `_Base64File` shim (exposes `.read()`, `.filename`, `.content_length`), and feeds it into the existing `analyze()` → `firm_level.run()` chain with zero changes downstream. Multipart path is preserved for `curl`, local dev, and any future Domino App deploy — that path still works as before.

**Behavior change:** uploads from the KRONOS UI now succeed through the Domino workspace proxy. 437KB test workbook → ~583KB base64 JSON body, comfortably under typical proxy limits.

**Compatibility:** the multipart fallback means a `curl -F file=@...` against `/upload` still works, so shell/CI probes are unaffected.

**If payload size ever becomes an issue:** base64 inflates by ~33%. If users start hitting 413 or proxy limits, next step is to land files in a dataset dir on the Domino container and switch the UI to a file-picker + `GET /files` + small JSON `POST /analyze` with just the filename. Code-wise that's a ~30 min change.

---

## Round 6 — Lending pipeline rebuild (templates → classifier → cube → slicer)

Commit: _see `git log` on branch `kronos`_

Replaced the monolithic `pipeline/firm_level.py` with a **template + classifier + cube + slicer** architecture. The classifier now auto-detects which template a sheet matches by column signature, the cube computes every KRI once per upload, and per-mode slicers pick which slice to send to the LLM. This is the foundation for porting the remaining canned modes without re-reading the workbook each time.

- [ ] `pipeline/templates/` — **NEW DIR.** `__init__.py` (empty), `base.py` (`Template` ABC + `FieldSpec` + `ValidationWarning` + `Role` literal), `lending.py` (`LendingTemplate` with all 37 columns tagged: hierarchy, period, ratings, categorical dims, horizontal flags, stock numerics, weighted-average numerators, watchlist passthrough).
- [ ] `pipeline/scales/` — **NEW DIR.** `pd_scale.py` defines the C00…CDF rating scale (15 buckets, C00-C07 = Investment Grade), with `code_for_pd()`, `is_investment_grade()`, `direction()` (upgrade/downgrade/unchanged), and IG/NIG list helpers.
- [ ] `pipeline/parsers/` — **NEW DIR.** `regulatory_rating.py` parses split regulatory ratings like `"SS - 18%, D - 42%, L - 40%"` into normalized `[(code, fraction), …]` lists. Includes `equals()` (order-insensitive, 0.5pp tolerance), `worst_code()`, `direction()`, `format_percent()`.
- [ ] `pipeline/loaders/` — **NEW DIR.** `classifier.py` reads every sheet in the workbook, matches each against `Template.SIGNATURE`, validates matched sheets, and returns `{ classified: { template_name: df }, metadata: {…} }`. Raises ValueError on no matches, signature collisions, or duplicate template matches.
- [ ] `pipeline/cube/` — **NEW DIR.** `models.py` defines pydantic schemas (`LendingCube`, `KriBlock`, `GroupingHistory`, `ContributorBlock`, `MomBlock`, etc., all `extra="forbid"`). `lending.py` is the cube computer: `compute_lending_cube(df) → LendingCube` produces firm-level KRIs, by-industry / by-segment / by-branch / by-horizontal / by-IG-status sub-cubes, watchlist firm-level aggregate, top contributors at parent level, and full month-over-month derivations (new originations, exits, PD + regulatory rating changes, exposure movers) when the file contains ≥ 2 periods.
- [ ] `pipeline/processors/lending/` — **NEW DIR.** `firm_level.py` implements `slice_firm_level(cube) → { context, metrics }`. Builds prose context with all firm-level KRIs + IG/NIG split + horizontal portfolios + watchlist + period coverage + validation note. Returns metrics dict matching the existing tile-array contract (sections: Firm-Level Overview, Investment-Grade Split, Horizontal Portfolios, Watchlist).
- [ ] `pipeline/analyze.py` — rewritten to use `MODE_MAP` instead of `SCRIPT_MAP`. Looks up `{ template, slicer }` per mode, calls `classify(file)`, computes the cube once, then calls the slicer. Falls back to the verbatim `placeholder_processor()` for custom prompts and unwired modes.
- [ ] `pipeline/firm_level.py` — **DELETED.** Replaced by the template + cube + processor split above.
- [ ] `claude.md` — minor doc updates left over from earlier rounds (transports table, currently-wired modes table, firm-level required columns).

**Behavior change:** the `firm-level` mode now has a much richer narrative context (IG/NIG split, horizontal portfolios, watchlist aggregate, multi-period coverage, regulatory-vs-committed validation note). Tile output adds three new sections: Investment-Grade Split, Horizontal Portfolios, Watchlist. The existing Firm-Level Overview section is preserved.

**Multi-period correctness:** previous monolithic processor double-counted when a workbook contained multiple `Month End` snapshots (e.g. Jan + Feb). The cube selects the latest period for stock metrics — this fixes that bug.

**Compatibility:** the dispatcher still returns `{ context, metrics }` — no frontend or server changes needed. Custom-prompt path still uses `placeholder_processor()` unchanged.

**To test on Domino:** upload your existing firm-level workbook (single or multi-period) and click *Firm-Level View*. Verify (a) tiles render correctly, (b) IG/NIG + horizontal sections appear when those columns are populated, (c) the validation note appears in the narrative if `Pass+SM+SS+Dbt+L+NoReg ≠ Committed` within $2 tolerance.

---

## Round 7 — FAB + follow-up flow: state-machine hardening

Commit: _see `git log` on branch `kronos`_

State-machine audit of the existing FAB + follow-up flow turned up four bugs (P0/P1/P2). All fixed in this round. No new states or visuals — purely closing leaks and adding feedback.

- [ ] `static/main.js` — added module-level `followupController` (AbortController) + `followupInFlight` guard + `FOLLOWUP_TIMEOUT_MS = 60_000`. New `abortFollowup()` helper called from `runAnalysis()` and the New Analysis button so a parent reset cancels the in-flight fetch. `submitFollowup()` early-returns on `followupInFlight`, arms the AbortController, attaches `signal` to the fetch, runs a 60s timeout, distinguishes timeout (user-facing message) vs parent abort (silent return) in the catch, and re-checks `signal.aborted` after `fileToBase64` to cover the pre-fetch race. FAB-close path now confirms before discarding a typed draft. Send + Cmd/Ctrl+Enter call new `flashEmpty(ta)` (shake + brief tint) instead of silent no-op when textarea is empty. `showFollowupError()` now scrolls the error into view.
- [ ] `static/styles.css` — added `.followup-textarea.shake` rule (reuses existing `shake` keyframe + adds a brief CTA-tinted background) for empty-submit feedback.

**Bugs fixed:**
- **P0** — Cmd/Ctrl+Enter during in-flight submit no longer issues a parallel fetch (was producing two response blocks per question).
- **P0** — Mid-submit "New Analysis" no longer leaks state (was appending a stray FAB and message-block to the now-hidden thread).
- **P1** — 60s fetch timeout so a hung Domino proxy can't pin the UI in submitting state forever.
- **P1** — Closing the FAB with a typed draft now confirms before discarding.
- **P2** — Empty-submit (Send or Cmd/Enter) now shakes + tints the textarea instead of silent no-op.
- **P2** — Error row scrolls into view so the user can't miss it after a failed submit.

**Behavior change:** none on the happy path. Failure paths are now visible and recoverable; double-submits are impossible; mid-submit cancels are clean.

**Compatibility:** purely additive. No new DOM, no new IDs, no API changes.

---

## Round 8 — Validator: strip dates before numeric tokenization

Commit: _see `git log` on branch `kronos`_

The verification badge was showing 1-of-N "unverified" figures whenever the LLM rewrote an ISO date from the context as natural-language prose. Root cause: the regex `[\+\-]?\$?\d+\.?\d*[%xBMKbmk]?` treats the dash in `2026-02-28` as a leading sign, so the context tokenizes to `'2026'`, `'-02'`, `'-28'`, while the LLM's prose `"February 28, 2026"` tokenizes to `'2026'`, `'28'` — bare `'28'` doesn't match `'-28'`, false positive.

- [ ] `pipeline/validate.py` — added `_ISO_DATE_PATTERN` (`\b\d{4}-\d{2}-\d{2}\b`) and `_PROSE_DATE_PATTERN` (Jan/January/Feb/… + day + optional `, YYYY`). Both are stripped from the input string at the top of `_extract_numbers()` before the numeric regex runs. Dates contribute zero tokens to either side now, so any date format on either side matches cleanly.

**Behavior change:** the verification badge no longer flags date components as unverified. The `unverified` list returned in the `verification` payload should drop by 1–2 tokens whenever the narrative includes a rewritten date. No change for narratives that don't reference dates.

**Compatibility:** narrower, not wider — fewer tokens get extracted, so fewer false positives. No risk of new false negatives because we only stripped *date* substrings, not anything that could be a real figure.

---

## Round 9 — Portfolio Summary slicer + lending field renames

Commit: _see `git log` on branch `kronos`_

Wires up the second canned mode (`portfolio-summary`) end-to-end against the existing cube, and applies the two source-data field renames you mentioned.

- [ ] `pipeline/processors/lending/portfolio_summary.py` — **NEW FILE.** `slice_portfolio_summary(cube)` produces an executive-health view from the existing `LendingCube`: headline scale (commitment, outstanding, parent / facility / industry counts), credit quality (C&C exposure + % of commitment, weighted PD/LGD), IG vs NIG mix with shares, top-5 industries by committed (with % of commitment), top-5 parent contributors (with % of commitment), watchlist aggregate, and period-over-period movement (originations, exits, PD/reg upgrades + downgrades) when the file contains ≥ 2 periods. Returns the standard `{ context, metrics }` shape.
- [ ] `pipeline/analyze.py` — added `from pipeline.processors.lending import portfolio_summary as lending_portfolio_summary` and registered `"portfolio-summary": { "template": "lending", "slicer": ... }` in `MODE_MAP`. Clicking *Portfolio Summary* now runs the real slicer instead of falling through to `placeholder_processor()`.
- [ ] `pipeline/prompts.py` — replaced the TODO placeholder for `"portfolio-summary"` with a real system prompt that names the actual cube slices the LLM will see (headline, IG/NIG mix, top concentrations, watchlist, MoM if present), with explicit instructions to cite figures verbatim and not invent trends when only one period is present.
- [ ] `pipeline/templates/lending.py` — field renames in column tags: `Current Month Regulatory Rating` → `Regulatory Rating`, `Credit Watchlist Flag` → `Credit Watch List Flag`.
- [ ] `pipeline/cube/lending.py` — same two renames applied throughout the cube computer (group-by keys + filter conditions).
- [ ] `pipeline/parsers/regulatory_rating.py` — header comment + docstring updated to reference `Regulatory Rating` (no behavior change).

**Behavior change:** *Portfolio Summary* button now produces a real deterministic slice + LLM narrative instead of the generic placeholder summary. Tile output adds these sections: Headline, Investment-Grade Mix, Top N Industries by Commitment, Top N Parents by Commitment, Watchlist (when present), Period Movement (when ≥ 2 periods).

**Field-rename compatibility:** the lending workbook header strings `Current Month Regulatory Rating` and `Credit Watchlist Flag` no longer match the template — uploads must use `Regulatory Rating` and `Credit Watch List Flag` after this change. The classifier will fail fast with a missing-required-column error if the workbook still uses the old names.

**Repo-only docs in this commit (no Domino pull needed):** `LANGCHAIN_AZURE_FORMULA.md` (reusable Azure OpenAI + LangGraph recipe), `UI_tweaks.md` (cheatsheet for font/layout/card-order tweaks).

---

## Round 10 — Facility-level WAPD contributors

Commit: _see `git log` on branch `kronos`_

Adds a deterministic answer to "which loans are pulling the WAPD up the most?". Top facilities by `Weighted Average PD Numerator` (PD × Committed Exposure) are computed at the firm level and per horizontal portfolio, then surfaced in the Portfolio Summary narrative + a new tile section.

- [ ] `pipeline/cube/models.py` — **NEW MODEL `FacilityContributor`** (facility_id, facility_name, parent_name, committed, wapd_numerator, implied_pd, pd_rating, regulatory_rating, share_of_numerator). Two new fields on `LendingCube`: `top_wapd_facility_contributors: list[FacilityContributor]` (firm-level top 10) and `wapd_contributors_by_horizontal: dict[str, list[FacilityContributor]]` (top 10 per horizontal portfolio).
- [ ] `pipeline/cube/lending.py` — added `TOP_N_WAPD_FACILITIES = 10` and `_top_wapd_facility_contributors(scope_df, n)` helper. Groups the scope DataFrame by `Facility ID`, sorts by `Weighted Average PD Numerator` desc, computes per-facility `implied_pd` (numerator/committed) and `share_of_numerator` (vs scope total). Called once for the firm-level slice and once per horizontal portfolio. Both new fields are populated in the returned `LendingCube`.
- [ ] `pipeline/processors/lending/portfolio_summary.py` — new context block listing top WAPD drivers (parent, $ committed, numerator, share %, implied PD, PD rating) and a new tile section "Top 5 WAPD Drivers (Facility)" showing share % + committed exposure with `warning` sentiment.
- [ ] `pipeline/prompts.py` — `portfolio-summary` system prompt mentions the new section is in the data and asks the narrative to call out the top one or two drivers and distinguish small-high-PD vs large-lower-PD loans.

**Behavior change:** Portfolio Summary now produces a deterministic top-WAPD-contributors list and a new tile section. The LLM narrative will reference the loans driving the WAPD up. The per-horizontal map (`wapd_contributors_by_horizontal`) is computed and stored in the cube but not yet rendered in any slicer — available for future per-portfolio narration.

**Compatibility:** purely additive on the cube. Existing slicers (firm-level) continue to work unchanged because they don't read the new fields.

---

## Round 11 — FAB/follow-up bug fixes, Cancel button, follow-up inheritance, error logging

Commit: _see `git log` on branch `kronos`_

Four-part pass from a state-machine audit of the FAB + follow-up flow. Parts A–C tighten the follow-up loop; Part D introduces an always-on error log that runs alongside (and independently of) MLflow tracking.

### Part A — FAB/follow-up bug fixes

- [ ] `static/main.js` — `abortFollowup()` now removes `followupFab.classList.remove('open')` so a mid-draft cancel doesn't leave a stale `.open` class on the next open. `flashEmpty(textarea)` strips any sibling `.followup-error` row (stale error banners no longer survive through a shake). Added Esc keydown handler that closes the FAB like clicking `×`. `submitFollowup()` guards `!selectedFile` at the top and routes to `showFollowupError()` with a neutral "start a new analysis" message instead of silently throwing.

### Part B — Cancel button on the analysis transition screen

- [ ] `static/index.html` — added `<button id="cancelBtn">Cancel</button>` in a `.loading-header` row at the top of `#loadingPanel`, and `<div id="inlineNotice" class="inline-notice" role="status" hidden>` inside the unified card.
- [ ] `static/styles.css` — added `.loading-header` (flex-end) and `.inline-notice` (neutral surface-alt background, 300ms fade-out via `.fade-out`).
- [ ] `static/main.js` — module-level `primaryController` (AbortController) + `primaryCancelled` sticky flag. `runAnalysis()` arms the controller synchronously before kicking off `fileToBase64` + fetch, threads `signal`, catches `AbortError` → returns a `{ __aborted: true }` sentinel, and short-circuits after fetch so a cancel during post-fetch animation is still honored before `showResults()`. Cancel button aborts, routes immediately back to the upload card (no loading dismissal animation), and triggers a 3-second `.inline-notice` with 300ms fade. `abortPrimary()` + `showInlineNotice()` helpers added. All user state (file, mode, parameters, prompt text) preserved through a cancel — `runAnalysis()` doesn't mutate any of it on the happy path, so preservation was free.

### Part C — Follow-up mode/parameter inheritance + sort-determinism audit

- [ ] `static/main.js` — module-level `inheritedMode`, `inheritedParameters`, `lastNarrative`. `showResults()` snapshots all three on first render. Follow-up POST body now sends `{ mode: inheritedMode, parameters: inheritedParameters, prior_narrative: lastNarrative }` so the slicer re-runs against the same inputs and the verifier compares against identical `verifiable_values`. `lastNarrative` is advanced after each follow-up renders; the New Analysis button clears all three.
- [ ] `server.py` — extracts `prior_narrative` from both JSON and multipart branches and threads it into `ask_agent(..., prior_narrative=prior_narrative)`.
- [ ] `pipeline/agent.py` — new `State.prior_narrative: str = ""` field; extracted `_build_message_sequence(state)` helper that emits a multi-turn shape `[System, Human(context), AI(prior_narrative), Human(question)]` when `prior_narrative` is set, else the first-turn shape via `HUMAN_TEMPLATE`. Assistant turn carries **plain narrative text only** — no structured claims or verification metadata, matching what a real prior conversation would look like. `ask_agent()` accepts a `prior_narrative=""` kwarg. Added FUTURE cache breadcrumb (file-hash keying; cache entry must preserve both slicer context AND verifiable_values) and an OUT-OF-SCOPE breadcrumb noting that follow-ups genuinely outside the inherited slice are answered with degraded quality, not rerouted (re-slicing belongs in a future plan-node).
- [ ] `pipeline/cube/lending.py` — sort-determinism audit:
  - `_top_contributors`: `grouped.sort_values([<metric>, "Ultimate Parent Code"], ascending=[False, True])` for each of by_committed / by_outstanding / by_wapd_contribution / by_cc_exposure.
  - `_top_wapd_facility_contributors`: `sort_values(["wapd_numerator", "Facility ID"], ascending=[False, True]).head(n)`.
  - `_facility_changes`: `out.sort(key=lambda c: (-c.committed, c.facility_id))`.
  - `_exposure_movers`: now `reset_index() + sort_values(["abs_delta", "Facility ID"], ascending=[False, True])` — explicit secondary key instead of relying on groupby's default sort + stable mergesort.
- [ ] `pipeline/processors/lending/portfolio_summary.py` — `_top_groupings`: `pairs.sort(key=lambda kv: (-kv[1], kv[0]))`.
- [ ] `pipeline/parsers/regulatory_rating.py` — `_normalize`: `sorted(merged.items(), key=lambda kv: (_INDEX.get(kv[0], 999), kv[0]))`.

### Part D — Error logging scaffolding

- [ ] `pipeline/error_log.py` — **NEW FILE.** Two-tier emission via `log_error(event_type, **fields)`:
  - Tier 1 (JSONL, always on): appends to `<KRONOS_ERROR_LOG_DIR>/kronos-errors.jsonl` (default `logs/`), rotates on ≥10 MB OR date change, thread-safe via module-level `threading.Lock()`, date-dated archive filenames.
  - Tier 2 (MLflow, gated by `KRONOS_MLFLOW_ENABLED` + active run): adds `kronos.has_error=true` tag, increments `kronos_error_<event_type>_count` metric, logs full record as `kronos-error-<ts>.json` artifact.
  - `read_recent(limit=50)` returns the JSONL tail (cap 500) for the `/errors/recent` endpoint.
  - Field policy: bounded snippets (context ≤500, user_prompt ≤250); never logs full narratives / full file contents / verifiable_values.
  - All writes are best-effort — a logging failure never raises.
- [ ] `server.py` — instrumented failure sites in `/upload` and `/cube/parameter-options`:
  - `upload_parse_failed` (base64 decode, parameters JSON parse)
  - `parameter_validation_failed` (pre-validate + cube-aware)
  - `mode_not_implemented`
  - `classification_failed` (ValueError from analyze, missing lending sheet in /cube/parameter-options)
  - `slicer_failed` (generic analyze exception)
  - `llm_failed` (ask_agent exception, with context_snippet + prior-narrative flag)
  - `verification_mismatch` (only when `mismatch_count > 0`; captures up to 10 offending claim rows — field_not_found "unverified" is transparency, not an error, and is NOT logged)
  - `cube_parameter_options_failed`
  - New `GET /errors/recent?limit=N` route gated by `KRONOS_ERRORS_ENDPOINT_ENABLED` — returns 404 when disabled.
  - New `_session_id()` helper reads `X-Kronos-Session` header (capped at 64 chars).
- [ ] `static/main.js` — new `KRONOS_SESSION_HEADER` constant + `getSessionId()` helper. Generates a per-tab UUID via `crypto.randomUUID()` (with hex-token fallback) on first call, persists in `sessionStorage` under `kronos.session_id`. Header added to `/modes`, primary `/upload`, and follow-up `/upload` fetches. Falls back to a `window.__kronosSessionId` if `sessionStorage` is unavailable (private mode / sandbox).
- [ ] `claude.md` — new "## Error Logging Layer" section under the MLflow section (entry point, tiers, event types, field policy, session ID, endpoint, env vars).

### New environment variables

| Variable | Default | Notes |
|---|---|---|
| `KRONOS_ERROR_LOG_DIR` | `logs/` | Directory for the JSONL file. **TODO — confirm Domino persistent path** (`/domino/datasets/...` conventionally, but verify per-deployment before pinning; setter can override at deploy time without code change). |
| `KRONOS_ERRORS_ENDPOINT_ENABLED` | unset (off) | Set to `true` to enable `GET /errors/recent`. |

### Compatibility / rollout

- Purely additive on the backend: existing routes unchanged, new route is 404 when its env var is unset.
- Follow-up inheritance is additive on the wire — old clients that don't send `prior_narrative` still work (server reads `""`).
- Sort-audit changes produce identical ordering when values tie; differ from previous behavior only when the old code's ordering was already non-deterministic (i.e. fixing a latent flake, not altering a stable result).
- JSONL file appears at `logs/kronos-errors.jsonl` the first time an error is logged. Add `logs/` to `.gitignore` if it isn't already.

---

## Round 12 — Deterministic calculation audit

Commit: _see `git log` on branch `kronos`_

Read-only audit of every calculation in `pipeline/cube/lending.py` and what each slicer surfaces. No code changed. Output is a single reference doc that scopes follow-up work.

- [ ] `docs/calculation-audit.md` — **NEW FILE.** Ten sections: cube overview, section inventory, KRI inventory matrix (per slicer), sub-statistics audit (GRM / Leveraged Finance / Watchlist), horizontal-portfolio deep dive (incl. industry × horizontal feasibility), cross-section consistency invariants, determinism audit (explicit vs implicit tiebreakers), correctness spot-checks, 19 ranked gaps, 10 scoped recommendations.

**Headline findings (do not fix yet):**
- HIGH: IG/NIG silently misclassifies unrated PD codes as NIG (`~is_ig` mask catches blanks/NaN/out-of-scale).
- HIGH: `by_industry` / `by_segment` / `by_branch` don't reconcile with firm totals when dim values are NaN — rows drop from every bucket but still contribute to the firm sum.
- HIGH: "Portfolio" semantics ambiguous — `cube.available_portfolios` maps to industries, but the `portfolio-level` / `portfolio-comparison` placeholder modes read like they mean horizontals.
- MEDIUM cluster: dormant cube outputs (`by_segment`, `by_branch`, `wapd_contributors_by_horizontal`, `top_exposure_movers`, three of four `top_contributors.by_*` lists, `GroupingHistory.history`) computed but unused by any slicer.

**Behavior change:** none — audit-only.

---

## Round 13 — PD rating-category bucketing (Part 1 of audit-fix series)

Commit: _see `git log` on branch `kronos`_

Addresses Round 12 HIGH finding #1. The IG/NIG `~is_ig` mask silently bucketed unrated/invalid PD codes as NIG. Replaced with explicit four-bucket classification:

- **Investment Grade** (C00–C07)
- **Non-Investment Grade** (C08–C13) — Distressed (C13) remains inside NIG and is reported as a parallel sub-stat
- **Defaulted** (CDF) — new top-level peer, NOT part of NIG
- **Non-Rated** (TBR / NTR / Unrated / NA / N/A / #REF / blank) — new top-level peer, treated as a data-quality signal

Files:

- [ ] `pipeline/scales/pd_scale.py` — added `NON_RATED_TOKENS` constant + `is_non_rated()` helper. **Narrowed `non_investment_grade_codes()` to `["C08"…"C13"]`** (previously returned through `CDF`). Added `distressed_code()` → `"C13"` and `defaulted_code()` → `"CDF"` helpers. Added `NIG_LAST_INDEX`, `DISTRESSED_CODE`, `DEFAULTED_CODE` module constants. Updated header docstring to document the five-category split.
- [ ] `pipeline/cube/models.py` — added `DistressedSubstats` model (period + committed + outstanding + facility_count). Extended `LendingCube` with `by_defaulted: dict[str, GroupingHistory]`, `by_non_rated: dict[str, GroupingHistory]`, and `nig_distressed_substats: Optional[DistressedSubstats]` fields. Updated `by_ig_status` docstring to clarify that NIG includes C13 and CDF is NOT in NIG.
- [ ] `pipeline/cube/lending.py` — replaced 7-line IG/NIG block with a 60-line five-mask classifier. Top-level buckets: IG, NIG, Defaulted, Non-Rated. Distressed (C13) masked separately but as a subset of NIG. **New invariant warning** — if `ig_mask | nig_mask | defaulted_mask | non_rated_mask` doesn't cover every row, logs a warning AND appends `{"code": "pd_rating_unclassified", "count", "sample_values"}` to `CubeMetadata.warnings`. Added WAPD-numerator upstream-contract note on `_weighted_average` docstring. New imports: `logging`, `DistressedSubstats`.
- [ ] `pipeline/processors/lending/firm_level.py` — replaced "Investment-grade split" section with "Rating-category composition" (IG / NIG — with Distressed indented sub-line when present — Defaulted / Non-Rated). Tile group renamed "Investment-Grade Split" → "Rating Category Composition", sentiment encoding: IG=neutral, NIG=neutral, Distressed=warning, Defaulted=negative, Non-Rated=neutral. Two new helpers `_rating_category_section()` / `_rating_category_tiles()` at file bottom. `verifiable_values` now includes `by_defaulted` / `by_non_rated` entries plus `"Distressed (of which)"` and `"Distressed facility count"`.
- [ ] `pipeline/processors/lending/portfolio_summary.py` — same composition extension as firm_level. **"% of rated commitment" denominator preserved as IG + NIG only** (legacy semantic; Defaulted sits outside NIG). Defaulted / Non-Rated rendered with "% of total commitment" shares. Distressed rendered as an indented sub-line with "% of NIG" share. Tile group renamed "Investment-Grade Mix" → "Rating Category Composition". `verifiable_values` extended with new labels + shares.
- [ ] `config/prompts/firm_level.md` — extended to describe rating-category composition. Instructs the LLM when to call out Distressed (sub-line under NIG), Defaulted (separate terminal-state concern), and Non-Rated (data-quality signal). Claims examples updated with new labels.
- [ ] `config/prompts/portfolio_summary.md` — extended narrative instructions for the new composition structure. Claims examples updated with new labels including `"Distressed (of which)"`, `"Distressed facility count"`, and the `"(% of NIG)"` / `"(% of total commitment)"` suffixes.

### Open question — WAPD-numerator upstream contract (NOT fixed; needs data-owner confirmation)

The business rule for Non-Rated facilities is that they are weighted as C07 when the `Weighted Average PD Numerator` column is computed. This substitution is expected to happen in the upstream Power BI exporter, not in the cube. **There is nothing in this codebase that verifies the upstream exporter actually does this.** The cube consumes the numerator column at face value.

Flagged on `_weighted_average()` docstring in `pipeline/cube/lending.py`. Before we rely on WAPD figures that include Non-Rated rows, confirm with the Power BI / data-engineering owner that the numerator already accounts for the C07 substitution. If it doesn't, WAPD silently under-weights Non-Rated credits — an upstream data-contract fix, not a cube fix.

### Behavior change

- A workbook that previously reported NIG = "everything not C00..C07" now reports NIG = "only C08..C13". Unrated and CDF rows that used to inflate NIG now live in their own `Non-Rated` and `Defaulted` buckets. If a prior workbook run reported NIG committed of $X, the new run will report NIG less than or equal to $X, with the difference redistributed to Defaulted and/or Non-Rated.
- New sentiment colors on the rating-category tiles (Defaulted = red, Distressed = amber). IG / NIG / Non-Rated stay `neutral`. Previously all IG/NIG tiles were `neutral`.
- No breakage expected for existing prompts — the new labels are additive; the verifier resolves them the same way.

### Compatibility

- LendingCube model gains new fields with sensible defaults (`default_factory=dict` / `None`). Anything that currently builds a `LendingCube` continues to work; new slicers can pick up the new fields optionally.
- `non_investment_grade_codes()` now returns a shorter list (7 codes instead of 8). Only caller in repo is `pipeline/cube/lending.py`, which has been updated accordingly. `docs/calculation-audit.md` references are now stale (the audit is point-in-time) — no rewrite planned.
- No new dependencies.

### Deferred (out of scope for this round)

- Part 2 of the audit-fix series: unclassified-dim buckets in `_grouping_by_dim`.
- Rendering `by_horizontal × Distressed` or similar cross-cuts.
- Multi-period history for `DistressedSubstats` (currently latest-period only).

---

## Round 19 — Phase 1 of Scope × Length refactor: deprecate `portfolio_summary`

Commit: _see `git log` on branch `kronos`_

First of five phases that decouple the *scope* of an analysis (firm_level / industry_portfolio_level / horizontal_portfolio_level) from the *length* of its narrative (full report / executive summary / snapshot). The executive-summary view that `portfolio_summary` produced will be reproduced in subsequent phases by running the `firm_level` mode with a request-level `length=executive` directive composed onto the base prompt. **No code or data semantics change in this round** — Phase 1 is the demolition pass that removes the slicer, its prompt, its registry import, and its mode entry, and refactors the docs to match.

- [ ] `pipeline/processors/lending/portfolio_summary.py` — **DELETED.**
- [ ] `config/prompts/portfolio_summary.md` — **DELETED.**
- [ ] `pipeline/registry.py` — removed the `import pipeline.processors.lending.portfolio_summary  # noqa: F401` line that triggered slicer self-registration. App boot would crash at import otherwise.
- [ ] `config/modes.yaml` — removed the `portfolio-summary` mode block. `GET /modes` no longer surfaces the button; the UI Quick Analysis grid silently drops it on next page load (frontend renders from the registry list, no hard-coded mode strings).
- [ ] `docs/available-kris.md` — surgically edited (live reference doc — every `portfolio_summary`-only label and dual-publish row removed; bare-name `<industry name>` / `<parent name>` / `<facility>` rows replaced with pointers to firm_level's prefixed labels; cube→slicer matrix column dropped). No portfolio_summary references remain.
- [ ] `docs/adding-a-mode.md` — example label-collision callout re-pointed at "another slicer's bare-name label"; label-form-conventions section dropped the portfolio_summary "Total ..." example and re-framed it as forward-planning guidance for any future slicer that juxtaposes scopes.
- [ ] `docs/calculation-audit.md` — top-of-doc amendment footnote noting the slicer was deprecated in Round 19; **content preserved verbatim** (point-in-time audit; per project convention, amend, don't erase).
- [ ] `docs/future-work.md` — top-of-doc amendment footnote: (a) Path B caller list reduced to firm_level only, (b) the label-divergence table no longer reflects the codebase but is retained as forward-planning context, (c) explicit clarification that the YAML reserved `lengths: []` (synthesis-template length spec) is **orthogonal** to the request-level `length` field that Phase 3 will introduce.
- [ ] `CLAUDE.md` — architecture tree drops the `portfolio_summary.py` line and `portfolio_summary.md` prompt line; "Currently wired modes" table drops the `portfolio-summary` row; `mode` routing example flipped to `"firm-level"`; `kronos.mode` MLflow tag example flipped to `firm-level`; `error_log` `mode=` example flipped to `firm-level`; YAML `modes:` example flipped from `portfolio-summary` to `firm-level`. New Round 19 entry in the Done section explains Phase 1 and explicitly documents the YAML `lengths: []` ≠ request-level `length` distinction.

### YAML reservation vs request-level `length` (one-time clarification)

Two unrelated concepts share the word "length":

1. **YAML `lengths: []`** in `config/modes.yaml` — reserved alongside `syntheses: []` for a future *synthesis-template* length spec at the synthesis layer (full report / executive briefing / quick update for a multi-mode synthesis document that composes outputs from several scopes into one). **Untouched by this refactor.**
2. **Request-level `length` field** (added in Phase 3) — per-`/upload` payload, `"full" | "executive" | "distillation"`, default `"full"`. Modulates which length-directive markdown gets concatenated onto the *base* prompt for a single-mode narration. Does not touch the data payload, the slicer, the cube, or the YAML registry.

Both can coexist — the YAML reservation governs synthesis layout, the request-level field governs single-mode narration length. Don't conflate them when reading future round notes.

### Behavior change

- The "Portfolio Summary" button vanishes from the Quick Analysis grid on next page load.
- Any in-flight UI sessions that still have the button rendered (cached page) will receive `mode_not_implemented` on click, which the existing error log captures via the `mode_not_implemented` event-type slug. No 500 — the request fails fast before the slicer dispatch.
- All other modes (`firm-level`, `industry-portfolio-level`, `horizontal-portfolio-level`, parameterized placeholders, plain placeholders) are unchanged.

### Compatibility

- App boot validates the mode registry against the slicer registry at import time (`pipeline/registry.py::load_registry`). Removing the slicer module and the YAML block in the same commit keeps the cross-check happy.
- No DB / cube / classifier / template changes — purely registry + docs.
- `GET /modes` response is one entry shorter; frontend reads it dynamically with no schema assumptions about which slugs exist.

### Test plan (Phase 1 only)

- `python -m pytest pipeline/tests/` — full suite must pass. Registry test (`test_registry.py`) cross-checks that every YAML `cube_slice:` resolves to a registered slicer and every `prompt_template:` file exists; both invariants hold after deletion.
- Manual smoke: load the app, confirm the Quick Analysis grid no longer renders the Portfolio Summary button, confirm the other modes still run end-to-end.

### Phases 2–5 follow

- **Phase 2** — split each scope prompt into base + length directives (`_length_full.md`, `_length_executive.md`, `_length_distillation.md`); composition helper concatenates `base + "\n\n---\n\n" + directive`.
- **Phase 3** — add request-level `length` field to `/upload` payload; validate against `("full", "executive", "distillation")`; default `"full"`; update `load_prompt(mode, parameters, length="full")`.
- **Phase 4** — UI length toggle with three buttons ("Full Report", "Executive Summary", "Snapshot"); follow-ups read live `activeLength`.
- **Phase 5** — verify all tests pass + run 9 scope×length combos confirming data payload identical across lengths within scope, narrative differs in length.

---

## Round 19 — Phase 2 of Scope × Length refactor: split scope prompts, add length directives, compose helper

Commit: _see `git log` on branch `kronos`_

Phase 2 of five. Each active scope prompt is now split into a scope-bound `*_base.md` file plus three orthogonal length directives (`_length_full.md`, `_length_executive.md`, `_length_distillation.md`) that get concatenated onto the base by `compose_prompt(base, length)`. The directives are functionally distinct framings, not longer/shorter copies of each other:

- **Full Report** — comprehensive write-up; every Portfolio Data section with material content earns coverage; sections covered in materiality order, not source order.
- **Executive Summary** — signal density over completeness; lead + 2-3 paragraphs that name only items materially changing the read on the book; explicitly omit the unremarkable.
- **Snapshot (distillation)** — 2-3 sentences total; one material observation + one driver; no preamble, no hedging, no second findings; "say nothing remarkable and stop" if the book is genuinely unremarkable.

Existing behaviour is preserved end-to-end: `load_prompt(mode, parameters)` defaults `length="full"` so callers (server.py, agent.py) that don't yet pass a length get the same composed prompt they would get from the pre-refactor `firm_level.md`. Phase 3 will wire the request-level `length` field through the upload payload.

- [ ] `config/prompts/_length_full.md` — **NEW.** Full Report directive (comprehensive coverage in materiality order; every claim names a where, who, or how much).
- [ ] `config/prompts/_length_executive.md` — **NEW.** Executive Summary directive (signal density; omit the unremarkable; explicit INCLUDE/OMIT examples; ~half a Full Report length).
- [ ] `config/prompts/_length_distillation.md` — **NEW.** Snapshot directive (2-3 sentences total; no preamble; no hedging; explicit "if you can't name a finding, say so and stop").
- [ ] `config/prompts/firm_level_base.md` — **NEW.** Scope-bound firm-level base. Carries the role line ("firm-level portfolio snapshot" preserved verbatim), the Portfolio Data section enumeration, the Guardrails (with the Distressed-as-subset / Defaulted-as-terminal / Non-Rated-as-data-quality / horizontals-as-overlay framings relocated INTO Guardrails as universal rules — they apply across all three lengths), the exits/new-entries semantics, and the Claims emission protocol with the full source_field examples list. The structural "Write a concise narrative (4-6 paragraphs)" body section was REMOVED — that content is now expressed by the length directives.
- [ ] `config/prompts/industry_portfolio_level_base.md` — **NEW.** Same split treatment for the industry-portfolio scope. Adds an explicit Guardrails framing that {{portfolio}} is a partition element (not an overlay), so the LLM never confuses it with a horizontal.
- [ ] `config/prompts/horizontal_portfolio_level_base.md` — **NEW.** Same split treatment for the horizontal-portfolio scope. Adds an explicit Guardrails framing that {{portfolio}} is an overlay (not a partition) and overlaps with industry portfolios and other horizontals by design.
- [ ] `config/prompts/firm_level.md` — **DELETED.** Superseded by `firm_level_base.md` + length directive composition.
- [ ] `config/prompts/industry_portfolio_level.md` — **DELETED.** Superseded by `industry_portfolio_level_base.md`.
- [ ] `config/prompts/horizontal_portfolio_level.md` — **DELETED.** Superseded by `horizontal_portfolio_level_base.md`.
- [ ] `config/modes.yaml` — three `prompt_template:` references updated (`firm_level.md` → `firm_level_base.md`, etc.). Reserved `lengths: []` comment block extended with an explicit one-paragraph clarification that the YAML reservation is for synthesis-template length specs and is **orthogonal** to the per-/upload request-level `length` field this refactor introduces.
- [ ] `pipeline/registry.py` — added `compose_prompt(base, length="full")` helper that concatenates `base + "\n\n---\n\n" + directive`. Updated `load_prompt(mode, parameters, length="full")` signature with the new keyword arg defaulting to `"full"` so existing callers (`pipeline/agent.py::_build_message_sequence`) keep working unchanged. **Strict validation:** unknown `length` values raise a new `LengthError(ValueError)` exception (not silent fallback) — server.py turns these into 400s in Phase 3 so a typo from the UI surfaces to the caller instead of being silently normalised to "full". Missing length-directive files on disk are caught at app startup by a new `_validate_length_directives()` call inside `load_registry()` (raises `RegistryError` listing each missing file with its absolute path) — this is treated as a deployment bug, not a runtime condition to recover from. Default-fallback path (no mode / placeholder mode → `default.md`) intentionally skips length composition since length variation isn't meaningful for the no-mode case. Module docstring + public-API banner updated to document both new functions and the YAML-vs-request-level orthogonality.
- [ ] `pipeline/tests/test_registry.py` — added a `TestLengthComposition` class with six tests: default composition picks Full Report and includes the literal `---` separator; explicit `length="executive"` and `length="distillation"` pick the right directives and exclude the others; unknown length raises `LengthError` from both `compose_prompt` and `load_prompt`; default `load_prompt(fl)` produces a system prompt that contains both the preserved base substring ("firm-level portfolio snapshot") and the composed directive marker ("FULL REPORT").

### Behaviour change

- No user-facing behaviour change in this round. `load_prompt(mode, parameters)` keeps composing a Full Report directive onto the base prompt by default, which produces a system prompt slightly different in *layout* (base + `---` + directive) but equivalent in *content* to the pre-refactor monolithic prompts. The LLM's narrative will be near-identical to before; minor wording shifts are possible because the directive's "lead with the most material observation" framing is now syntactically separate from the base.
- Phase 3 wires the request-level `length` field through `/upload`; only then does flipping to "Executive Summary" or "Snapshot" actually change the narration shape.

### Compatibility

- All existing `load_prompt` call sites (server.py, agent.py, tests) work unchanged because `length` is a keyword arg with a default.
- Existing tests still pass — `test_active_mode_prompt_loads` still asserts "firm-level portfolio snapshot" appears in the loaded firm-level prompt (preserved verbatim in `firm_level_base.md`); `test_parameter_substitution` still verifies `{{portfolio}}` substitution against `industry_portfolio_level_base.md`.
- Full pytest suite: 74/74 passing in 0.98s (six new `TestLengthComposition` tests added alongside the existing 68).

### Files for the Domino sync

When you pull this round into Domino:
- Add the six new prompt files (`_length_*.md` × 3 and `*_base.md` × 3) to `config/prompts/`.
- Delete the three superseded prompt files (`firm_level.md`, `industry_portfolio_level.md`, `horizontal_portfolio_level.md`).
- Replace `config/modes.yaml` and `pipeline/registry.py`.

### Phases 3–5 follow

- **Phase 3** — add request-level `length` field to `/upload` payload (server.py); validate against `("full", "executive", "distillation")`; default `"full"`; thread through `pipeline/agent.py::_build_message_sequence` into `load_prompt(mode, parameters, length=...)`. Follow-up requests inherit the original turn's length unless overridden.
- **Phase 4** — UI length toggle with three buttons ("Full Report", "Executive Summary", "Snapshot"); default "Full Report"; follow-ups read live `activeLength`.
- **Phase 5** — verify all tests pass + run 9 scope×length combos confirming data payload identical across lengths within scope, narrative differs in length.

---

## Round 19 — Phase 3 of Scope × Length refactor: wire `length` end-to-end through /upload

Commit: _see `git log` on branch `kronos`_

Phase 3 of five. Threads the request-level `length` field through the full request path: `/upload` payload → `validate_length()` → `ask_agent(length=...)` → `State.length` → `_build_message_sequence` → `load_prompt(mode, parameters, length)` → `compose_prompt(base, length)`. Both transports (`application/json` and `multipart/form-data`) accept the field with identical semantics: empty / missing falls through to the default (`"full"`); any value not in `("full", "executive", "distillation")` returns 400 with the valid-values list in the message body. **No UI change in this round** — the field is opt-in for callers; the existing UI keeps sending no `length` and continues to receive Full Report narratives. Phase 4 adds the toggle.

- [ ] `pipeline/registry.py` — added `validate_length(length)` public helper for request-time validation. Returns the cleaned length string ("full" by default for None / empty input), trims whitespace, is case-sensitive on purpose, and raises the existing `LengthError` (added in Phase 2) with the full sorted list of valid keys in the message. Caller pattern documented in the docstring matches the existing `validate_parameters` → `ParameterError` → 400 shape so server.py can wire it identically.
- [ ] `pipeline/agent.py` — added `length: str = "full"` field to `State` (alongside `prior_narrative`); added `length: str = "full"` kwarg to `ask_agent()` (positioned after `prior_narrative` so existing positional-arg call sites continue to work); threaded `length` into the `state_in` dict; updated `_build_message_sequence()` to call `load_prompt(mode_def, state.parameters, state.length)` so the composed prompt picks up the requested directive. `LangGraphResponsesAgent.predict_stream` also reads `length` from `request.custom_inputs` (default `"full"`) so a future MLflow-served deployment behaves the same way.
- [ ] `server.py` — imported `LengthError` and `validate_length` from `pipeline.registry`. Both transports now extract `length_raw` from the payload (JSON: `payload.get('length')`; multipart: `request.form.get('length')`). Validation runs as a new "Step 3b" right after parameter pre-validation, before the MLflow run wrapper opens — so an invalid length never opens an MLflow run, never reaches `analyze()`, and never bills LLM time. `LengthError` is mapped to a 400 response with the message from `validate_length` (which already includes the offending value and the sorted valid-values list) and emits a new `length_validation_failed` event into the JSONL error log via `pipeline/error_log.py`. The validated `length` is then passed as a kwarg to the `ask_agent(...)` call inside the LLM-narration step.
- [ ] `pipeline/tests/test_upload_length.py` — **NEW.** 10 integration tests across two `unittest.TestCase` classes (`TestUploadLengthJson` × 7, `TestUploadLengthMultipart` × 3). Pattern: Flask test client + `unittest.mock.patch.object(server, "analyze", ...)` + `patch.object(server, "ask_agent", side_effect=spy)` so the test exercises the request boundary without real workbook parsing or Azure OpenAI calls; the spy captures `ask_agent`'s kwargs so each test asserts what `length` server forwarded. Coverage: (1) valid lengths reach `ask_agent` verbatim — `full`, `executive`, `distillation` for JSON; `executive` for multipart; (2) invalid lengths return 400 with the message containing the offending value AND every valid key (`'short'` for JSON; `'bogus'` for multipart) AND `ask_agent` is never called; (3) omitted / empty `length` defaults to `"full"` (both transports); (4) case-sensitivity pin — `"Full"` returns 400, locking out future silent-lowercasing refactors. Module-level `pytest.importorskip("mlflow") + pytest.importorskip("langgraph")` plus an explicit probe for `mlflow.types.responses.ResponsesAgentRequest` (mlflow 3.x only) so the suite skips cleanly on local Python 3.9 dev environments and runs on Domino's Python 3.10+ environment.

### Behaviour change

- New `/upload` accepted field: `length` (string, optional, one of `"full" | "executive" | "distillation"`, default `"full"`). Backward-compatible — clients that omit the field receive the same Full Report behaviour as before.
- New 400 response shape for typos: `{"error": "Unknown length 'short'. Valid values: ['distillation', 'executive', 'full']."}`. Mirrors the existing `parameter_validation_failed` 400 shape so frontend error handling can be uniform.
- New JSONL error event-type slug: `length_validation_failed`. Captured fields: `mode`, `length` (the raw value the user sent), session id, error class + message. Bounded: no narrative text, no file contents.

### Compatibility

- `ask_agent()` signature gained `length="full"` as the last kwarg — purely additive. Existing callers (server.py upgraded in this round; tests previously calling `ask_agent` keep working) are unaffected.
- `State.length` defaults to `"full"`, so any code path that builds `State` without specifying length (legacy callers, tests) gets the same prompt composition as before.
- `LangGraphResponsesAgent.predict_stream` now also reads `length` from `custom_inputs` — additive; missing key defaults to `"full"`.
- Full pytest suite: **74 passing + 1 skipped** locally (the new test_upload_length.py module skips on Python 3.9 because mlflow 3.x requires 3.10+; on Domino it should run all 10 tests). Expected post-pull suite total: **84 passing** on Python 3.10+.

### Files for the Domino sync

- Replace `pipeline/registry.py` (`validate_length` helper + module imports unchanged from Phase 2 otherwise).
- Replace `pipeline/agent.py` (State + ask_agent + _build_message_sequence + predict_stream).
- Replace `server.py` (imports + length extract + Step 3b validation + ask_agent kwarg).
- Add `pipeline/tests/test_upload_length.py`.

### Manual smoke checks (Domino, after pull)

1. `POST /upload` with current UI payload → 200, narrative unchanged. (Default length path.)
2. `curl -X POST .../upload -F file=@... -F prompt="..." -F mode=firm-level -F length=executive` → 200, shorter narrative than (1).
3. Same curl with `-F length=bogus` → 400 with `Unknown length 'bogus'. Valid values: ['distillation', 'executive', 'full'].`
4. Repeat (2) and (3) via JSON transport (`Content-Type: application/json`, `length: "executive"` / `length: "bogus"` in body) — same outcomes.
5. Tail `logs/kronos-errors.jsonl` after step 3/4 → one record with `event_type: length_validation_failed`.

### Phase 4 follows

UI length toggle (`Full Report` / `Executive Summary` / `Snapshot`) above mode-selection; default `Full Report`; follow-ups read live `activeLength`. The backend is now ready — Phase 4 only touches `static/main.js`, `static/index.html`, `static/styles.css`.

---

## Round 20 — Per-slice verification-variability diagnostic script

Commit: _see `git log` on branch `kronos`_

Diagnostic-only round. Adds a single stdlib-only Python script that captures everything needed to confirm or refute the H3 + H5 hypothesis (recent prompt rewrite + loose claim contract) for the variable verification rates seen on `industry-portfolio-level`. No code or config changes — pure investigation tooling. Run on Domino, paste output back for Phase C analysis.

- [ ] `scripts/diag_perslice.py` — **NEW.** Stdlib-only (urllib + json + base64). Three consecutive `industry-portfolio-level` × Information-Technology × `length=full` runs against the running server, plus one `horizontal-portfolio-level` run for shared-layer isolation. For each run captures: timings, context_sent + narrative sha256[:12], full claims array, full `verification.claim_results` (status, reason, expected). Cross-run comparison hashes context_sent and narrative; diffs source_field sets to surface labels cited in only some runs. Failure-pattern classifier bins every non-verified claim into 11 named patterns (the H3 smoking gun is `spurious_committed_suffix`). Imports the slicer in-process (`pipeline.cube.lending.compute_lending_cube` + the registered slicer) to dump the canonical `verifiable_values` catalog the LLM should have cited from. Auto-picks the alphabetical-first industry and horizontal from `POST /cube/parameter-options`. Defaults to `pipeline/tests/fixtures/smoke_lending.xlsx`; override via `KRONOS_FIXTURE` env var.

### How to run on Domino

1. Make sure `server.py` is running (port 5000).
2. From repo root:
   ```bash
   python3 scripts/diag_perslice.py > diag_perslice.out 2>&1
   ```
   Optional env overrides:
   - `KRONOS_URL=http://...` (default `http://localhost:5000`)
   - `KRONOS_FIXTURE=/path/to/your.xlsx` (default `pipeline/tests/fixtures/smoke_lending.xlsx`)
   - `KRONOS_INDUSTRY="Information Technology"` (default: alphabetical first)
   - `KRONOS_HORIZONTAL="Leveraged Finance"` (default: alphabetical first)
3. Paste `diag_perslice.out` back.

### Exit codes

- 0 — success (all six steps ran; non-200 LLM responses are surfaced inline, not exit-coded).
- 2 — fixture missing or `/cube/parameter-options` unreachable / non-200.
- 3 — no industry options resolved from the fixture.
- 4 — in-process industry slicer dump raised (registry / classifier / cube failure).

### Behaviour change

- None. Script reads but does not modify anything. Running it three times in a row makes three `/upload` requests + their LLM calls.

### Cleanup

- Delete `scripts/diag_perslice.py` once the investigation lands a fix and the script is no longer useful, OR keep it as a generic per-slice variability probe (small, self-contained, no server dependency beyond the existing endpoints).

---

## Round 21 — Sync per-slice prompt examples to slicer's actual label conventions

Commit: _see `git log` on branch `kronos`_

Phase C of the verification-variability investigation (Rounds 19/20). The Round 20 diagnostic confirmed the H3 hypothesis: the unstable claims on `industry-portfolio-level` were all facility-level WAPD drivers where the LLM improvised an em-dash + parent-name convention (e.g. `F004 Term Loan — parent Delta Co — implied PD`) that doesn't match the slicer's actual parenthesised label format (e.g. `F004 Term Loan (implied PD)`). The fix is structural: prompt examples now match the slicer's published `verifiable_values` keys verbatim.

- [ ] `config/prompts/industry_portfolio_level_base.md` — rewrote the Claims block with three explicit label conventions:
  1. Slice-level KRIs / rating buckets — bare metric name (`<prefix> — Committed Exposure`).
  2. Parent contributors — parent name alone, NO metric suffix (`<prefix> — Acme Corp` resolves to committed; share has its own `(% of slice commitment)` variant).
  3. Facility-level WAPD drivers — facility name with metric in parentheses (`<prefix> — F100 Term Loan (committed)`, `(WAPD numerator)`, `(share of slice WAPD numerator)`, `(implied PD)`).
  Added a CORRECT facility-WAPD-driver example, plus three INCORRECT examples each with a one-line "# wrong because" explanation: multi-figure packing (single claim with comma-separated values), the `— parent <name>` insertion the LLM was making (display text bleeding into source_field), and the old `— Acme Corp — Committed` pattern (regression guard). Also dropped the `— Committed` suffix from the CORRECT parent example so the prompt example matches what the slicer publishes.
- [ ] `config/prompts/horizontal_portfolio_level_base.md` — applied the **same restructure** the industry prompt got this morning (the morning rewrite never reached this file). Same three conventions, same CORRECT and INCORRECT examples, with `Horizontal Portfolio:` prefix and the parens-on-facility convention. Added a horizontal-specific INCORRECT example for the multi-figure-packing failure the diagnostic surfaced (`<prefix> — Distinct ultimate parents, Distinct facilities` cited as `'2, 2'`).
- [ ] `firm_level_base.md` — **not touched.** Different label families (`Industry: <name> — Committed`, `Top Parent: <name>`, `WAPD Driver: <name>`, `MoM: <metric>`, etc., per Round 18). Out of scope for this diagnostic-driven fix.

### Diagnostic-driven evidence

From the Round 20 diagnostic (`diag_perslice.py` × 3 runs):

| Run | Claims | Verified | Failed |
|---|---|---|---|
| 1 | 21 | 17 | 4 (all facility WAPD with `— parent <name>`) |
| 2 | 23 | 17 | 6 (4 same as above + 2 with extra `— numerator` / `— implied PD` em-dash suffixes) |
| 3 | 21 | 17 | 4 (same as run 1) |

`context_sent` was byte-identical across all three runs (sha256 collision) — H1 (slicer non-determinism) ruled out. `verified_count` rock-solid at 17 — the verifying claims line up with the slicer's actual labels. The four-to-six unstable failures were all facility-level cite-format improvisations.

Horizontal × 1 run: 6 claims, 4 verified, 2 failed — both failures were multi-figure packing (e.g. `'Horizontal Portfolio: GRM — Distinct ultimate parents, Distinct facilities'` cited as `'2, 2'`). Horizontal hadn't received this morning's industry prompt rewrite; this round catches it up.

### Behaviour change

- LLM should now stop inserting `— parent <Parent name>` into facility-level `source_field` values. Verification rate on facility-WAPD-driver claims should jump from ~0% to high (~80%+; some Azure-OpenAI-temp-0.0 jitter on which exact metrics get cited per call is expected and is H5 — minor and unrelated).
- Horizontal prompt should stop emitting comma-separated multi-figure `source_field` values.
- No code, slicer, cube, or registry change. No data semantics change. Existing verified claims continue to verify (the slice-level KRI labels in the new prompt are identical to what the LLM was already citing correctly).

### Verification protocol

- Re-run `scripts/diag_perslice.py > diag_perslice.out 2>&1` after the pull.
- Expect: industry × 3 runs each show 0 (or near-0) `field_not_found` failures with `— parent` suffixes in the source_field. Verified count should rise from 17/21–23 to 21–23/21–23 (all or nearly-all verified).
- Expect: horizontal × 1 run shows 0 multi-figure-packed `source_field` values; verified rate on the small horizontal claim set rises to all-clear.

### Files for the Domino sync

- Replace `config/prompts/industry_portfolio_level_base.md`.
- Replace `config/prompts/horizontal_portfolio_level_base.md`.
- (Optional) leave `scripts/diag_perslice.py` in place to re-run for confirmation; can delete after verification.
