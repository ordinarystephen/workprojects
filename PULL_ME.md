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
