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
