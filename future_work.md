# KRONOS — Future Work Tracker

Running list of deferred architectural and implementation items. Each entry captures what the item is, why it's deferred, what triggers picking it up, and any design notes that would otherwise get lost.

Keep this file updated as decisions are made. When an item is picked up, move it to a "Completed" section at the bottom with the date it landed.

---

## Active deferrals

### 1. Per-session slicer cache

**What:** Cache cube slices and `verifiable_values` per session, keyed on `(file_hash, mode, parameters)`. On follow-ups, check the cache before re-running the slicer. Saves ~200-400ms per follow-up.

**Why deferred:** No evidence the recompute cost matters in practice. Caching introduces invalidation, memory, and lifecycle concerns that require real design work. Correctness-first wins while the system is young.

**Design notes when picked up:**
- Key on a content hash of the uploaded file bytes, not the filename. Filename-keyed caches produce false hits (different file, same name) and false misses (same file, different name).
- Cache must persist `verifiable_values` alongside the cube slice. A cache hit returning only the cube leaves the verifier with nothing to check claims against.
- Scope: per session, not global. Cross-session caching introduces multi-tenant concerns that aren't worth solving yet.
- Eviction: LRU with a modest max size (e.g., 10 entries per session) is probably enough. Sessions are short-lived.

**Triggered by:** Sustained user complaints about follow-up latency, or monitoring showing follow-up p95 latency creeping above a threshold worth worrying about. Don't build until you have data.

**Related code:** Follow-up slicer invocation path in `server.py` / `pipeline/agent.py`. A breadcrumb comment should already exist there pointing to this entry.

---

### 2. Multi-turn conversational context with LangGraph checkpointer + summarization

**What:** Proper multi-turn follow-up support. Currently each follow-up receives only the most recent narrative as conversational context. Beyond a handful of turns, this either loses earlier context (if we keep only the most recent) or blows out token budgets and degrades coherence (if we concatenate everything).

The target design:
- Use LangGraph's checkpointer to persist conversation state across turns.
- Keep the last N turns verbatim in context (probably N=2).
- Summarize older turns into a compressed "conversation so far" block.
- Re-summarize periodically as the conversation grows.

**Why deferred:** Current single-follow-up pattern works. Multi-turn is speculative until users actually hold long conversations with the tool. Building it now designs for imagined usage.

**Design notes when picked up:**
- Summarization is its own LLM call. It should be cheap (small model, low token budget) and run async where possible.
- The summary should preserve the deterministic context thread: which modes were run, which parameters, which numbers were cited. Losing that makes the verifier's job harder on later turns.
- Decide whether the summarizer sees the structured claims array or just the narrative prose. Claims may be easier to compress faithfully because they're already structured.
- The verifier contract has to continue working across summarized turns. This means verifiable_values from earlier turns may need to persist in some form, or the summarizer must only retain claims that can be re-verified against the current cube.
- Consider whether re-slicing follow-ups (see item 3) change what "conversation state" means. If each turn can change the active slicer, conversation state needs to track slice provenance per turn.

**Triggered by:** Users actually having conversations longer than 2-3 turns, and feedback that later turns lose context or contradict earlier ones.

**Related code:** `pipeline/agent.py` LangGraph definition, follow-up routing in `server.py`.

---

### 3. Re-slicing follow-ups (plan / synthesis architecture)

**What:** Currently follow-ups inherit the original mode and parameters — they re-narrate the same cube slice with a new framing. Re-slicing follow-ups would let a follow-up question trigger a *different* slicer than the original (e.g., firm-level → specific industry portfolio → comparison → WAPD driver analysis).

This is effectively the synthesis/plan architecture: each turn of conversation produces a new plan of `(mode, parameters)` tuples against the same underlying cube, and the planner decides whether the new question needs new slices.

**Why deferred:** Significant architectural work. Depends on the planner/classifier concept, which itself depends on having real usage data to tune against. Also blocked by item 2 (multi-turn state) because re-slicing only makes sense in a multi-turn conversation model.

**Design notes when picked up:**
- This is where the LLM gets more latitude, so it's where the deterministic-numbers guarantee is most at risk. The planner picks *which slicer*, not which numbers — numbers stay deterministic.
- Each turn's plan is independent but the cube is shared. The optimization hook from item 1 (cache) becomes much more valuable here.
- UI affordance: users need visibility into when re-slicing happens ("I'm now analyzing Health Care specifically, not firm-level"). Silent re-slicing erodes trust.
- The planner should have an escape hatch: "I don't know which mode answers this question" → surface the question back to the user with a mode picker, rather than guessing.

**Triggered by:** Clear user demand for conversation flows that span multiple analytical perspectives. Watch for users who hit "New Analysis" repeatedly with small parameter changes — they're doing manually what re-slicing would do automatically.

**Related code:** Would add a new planning node to the LangGraph, new endpoint or routing logic for plan execution, likely registry changes to support `syntheses` and `lengths` sections (already reserved in the YAML).

---

### 4. Synthesis templates and length directives

**What:** Compose multiple slicer outputs into a single coherent document, with a user-selectable length (full report, executive briefing, quick update) and purpose (risk assessment, comparison, status update).

Already reserved as empty sections in `config/modes.yaml`:
```yaml
syntheses: []
lengths: []
```

**Why deferred:** Requires real usage data to know which syntheses are actually useful. Building speculative synthesis templates produces prompts optimized for imagined use cases.

**Design notes when picked up:**
- Skip individual mode narration in the multi-mode synthesis path. Feed structured cube slices directly into the synthesis prompt. Narrating then re-narrating loses information and invites drift.
- Length directives are just prompt modifications — don't create N × M × L prompt files. One synthesis prompt + one length directive appended.
- Verification: a synthesis over N slices needs verifiable_values from all N. Make sure the verifier handles a union of verifiable_values across the plan's components.

**Triggered by:** User requests like "write a risk assessment covering X + Y + Z" that can't be handled by a single existing mode.

**Related code:** New sections in `config/modes.yaml` (already reserved), new synthesis executor, new prompt templates in `config/prompts/syntheses/`.

---

### 5. Free-form question routing (pick-a-mode classifier)

**What:** A small LLM classifier that takes a free-form user question and picks which mode + parameters to run. Preserves the deterministic-numbers guarantee because the classifier picks from a known menu; numbers stay Python-computed.

**Why deferred:** The registry refactor enables cheaper alternatives first — improved mode descriptions, example questions on the empty state, better UI affordances. These should be tried before adding an LLM call to the routing path.

**Design notes when picked up:**
- Cheaper variant to try first: keyword or embedding-similarity match against mode descriptions. No LLM call.
- When a classifier is added, always show the user which mode was picked before running, with an escape hatch ("that's not what I meant").
- The classifier sees the question and the mode catalog (slug, display name, description). It does NOT see the cube.
- Handle "unknown" gracefully: return a response listing available modes rather than forcing a best-guess pick.

**Triggered by:** Real usage showing users struggle to map their questions to the mode buttons, even with improved descriptions and examples.

**Related code:** Would add a classifier node to the LangGraph or a separate endpoint. Consumes the registry's mode list directly.

---

### 6. File-hash-based upload identity

**What:** On upload, compute and store a content hash of the file bytes. Use the hash as the canonical identifier for "which file is loaded" rather than the filename or a session-scoped reference.

**Why deferred:** No immediate need. Becomes necessary as a prerequisite for the slicer cache (item 1) and useful for the error logging if we ever want to correlate errors across sessions on the same file.

**Design notes when picked up:**
- SHA-256 is fine. Don't overthink.
- Compute once on upload, store in session state, use everywhere a file reference is needed.
- Don't log the hash in a way that could be used to fingerprint sensitive files — internal use only.

**Triggered by:** Building item 1, or any feature that needs to answer "is this the same file as before?"

---

### 7. Domino-persistent error log path

**What:** The `KRONOS_ERROR_LOG_DIR` env var currently defaults to `logs/`, which may not persist across workspace restarts in Domino Data Lab. A persistent volume path needs to be identified and configured.

**Why deferred:** Waiting on confirmation from the Domino environment owner about where persistent storage lives.

**Design notes when picked up:**
- No code change needed — just update the env var default or set it explicitly in the deploy config.
- A TODO comment should already exist in `pipeline/error_log.py` flagging this.

**Triggered by:** Conversation with Domino environment owner, or the first time you realize an error log you wanted to inspect was wiped.

---

### 8. User feedback loop (thumbs up/down per analysis)

**What:** Add a simple feedback mechanism to the results screen — thumbs up / thumbs down on the narrative, optionally with a free-text "what was wrong?" field. Feedback gets logged alongside the run for later analysis.

**Why deferred:** Not in any current spec. Flagging because it's almost impossible to reconstruct this signal later — if you ship without feedback capture, you lose months of potential quality data.

**Design notes when picked up:**
- Store feedback alongside the MLflow run or in a dedicated feedback log.
- Include: run ID, mode, parameters, rating, optional comment, timestamp, session ID.
- UI: unobtrusive, at the bottom of the results. Don't prompt; don't nag.
- Consider whether mismatches between verifier output and user feedback are interesting (e.g., verifier says green, user says thumbs-down → narrative may be factually correct but unhelpful).

**Triggered by:** Wanting to actually measure whether the tool is useful, or debugging specific user complaints about output quality.

---

### 9. Horizontals as editable config

**What:** Move `HORIZONTAL_DEFINITIONS` from Python into a config file (YAML or similar) so non-engineers can propose new horizontals by editing config rather than code.

**Why deferred:** Two horizontals exist today (Leveraged Finance, Impaired Assets). The Python dict is simpler until the list grows or non-engineers want to self-serve.

**Design notes when picked up:**
- YAML can't easily encode arbitrary Python predicates. Two approaches:
  - (a) Limit config to simple field = value or field in [values] rules. Covers most cases.
  - (b) Allow small expression language (Jinja-like filter expressions). More flexible, more complex.
- Start with (a).

**Triggered by:** Addition of a third horizontal, or a non-engineer asking to propose one.

---

### 10. Alerting thresholds for observability metrics

**What:** The observability doc captures what to track but deliberately doesn't set alert thresholds. Those need to come from baseline data after a few weeks of real usage.

**Why deferred:** Picking thresholds without baselines produces either false alarms or missed signals.

**Design notes when picked up:**
- Start with the high-signal metrics: `mismatch_ratio`, `empty_claims_count`, error rate.
- Prefer ratio-based thresholds over absolute ones (e.g., "mismatch_ratio > 5% over the last hour" not "more than 3 mismatches per day").
- Coordinate with whatever monitoring infra Databricks/MLflow provides.

**Triggered by:** Having 2-4 weeks of production metric data to establish baselines.

---

## How to use this file

- When a deferred item gets discussed and the decision is made to build it, move it to a "Completed" section at the bottom with the date and a one-line note.
- When a new deferral is made during design conversation, add it here immediately. Don't trust chat history to preserve it.
- Review this file at the start of each major iteration to see if any item's trigger has fired.

---

## Completed

_(empty)_