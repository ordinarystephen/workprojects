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
