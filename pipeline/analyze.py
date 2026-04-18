# ── KRONOS · pipeline/analyze.py ──────────────────────────────
# Dispatcher layer. Receives the uploaded file and mode slug from
# server.py, routes to the correct analysis script, and returns a
# standardised dict for the agent layer to consume.
#
# ── NAMING NOTE ───────────────────────────────────────────────
# In your existing codebase this role is played by processor.py.
# When merging, you have two options:
#
#   Option A — keep this file as the thin dispatcher and import
#   processor.py logic into it:
#       from your_existing_path.processor import run_analysis
#
#   Option B — replace this file entirely with processor.py and
#   update the import in server.py to point at processor directly.
#
# Either way, update the import in server.py to match.
# The key function server.py calls is analyze(file_obj, mode) → dict.
# ──────────────────────────────────────────────────────────────
#
# ── WHAT processor.py NEEDS TO PRODUCE ───────────────────────
# Your existing processor.py already does steps 1–4 of the pipeline:
#   1. Runs deterministic analysis on the uploaded workbook
#   2. Receives the large deterministic result
#   3. Reduces it to commentary_facts (narrative-oriented subset)
#   4. Builds the analysis_result package
#
# The dispatcher's job here is to:
#   a. Route to the correct processor script based on mode
#   b. Return analysis_result in the shape KRONOS expects (see schema below)
#
# deterministic_narrative_payload maps to what KRONOS calls "context" —
# the primary string passed to the LLM. This replaces commentary_facts
# as the LLM-facing payload. It is:
#   - scoped to the user's specific ask/mode
#   - smaller than raw_results
#   - richer and more faithful than commentary_facts
#
# commentary_facts is kept on analysis_result for backward compatibility
# but is no longer the primary LLM input.
#
# analysis_result maps to "metrics" — the structured data rendered as
# tiles in the Data Snapshot panel.
# ──────────────────────────────────────────────────────────────


# ── Script registry ───────────────────────────────────────────
# Maps mode slugs (sent from the frontend formData 'mode' field)
# to the run() function of the corresponding processor script.
#
# TODO (merge): Replace commented entries with real imports from
# your processor scripts as each mode is ported over. Example:
#
#   from pipeline.scripts import lending_risk
#   SCRIPT_MAP = { 'lending-risk': lending_risk.run, ... }
#
# Or if processor.py handles all modes internally with branching:
#   from your_path.processor import run as processor_run
#   SCRIPT_MAP = { 'lending-risk': processor_run, ... }
#
# TODO (naming): Keys must match "mode" slugs in static/prompts.json exactly.

SCRIPT_MAP = {
    # 'lending-risk':        lending_risk.run,
    # 'traded-products':     traded_products.run,
    # 'portfolio-sampling':  sampling.run,
    # 'portfolio-summary':   portfolio_summary.run,
    # 'concentration-risk':  concentration_risk.run,
    # 'delinquency-trends':  delinquency_trends.run,
    # 'risk-segments':       risk_segments.run,
    # 'exec-briefing':       exec_briefing.run,
    # 'stress-outlook':      stress_outlook.run,
}


# ── Dispatcher ────────────────────────────────────────────────
def analyze(file_obj, mode: str) -> dict:
    """
    Route the uploaded file to the correct processor script based on mode.

    In your existing codebase this orchestration lives in processor.py,
    called from main.py. Here server.py takes the role of main.py and
    calls this function directly.

    Args:
        file_obj : file-like object from Flask request.files['file']
                   (replaces the Streamlit file uploader)
        mode     : slug string identifying which script to run.
                   Sent from the frontend formData 'mode' field.
                   Empty string = user typed a custom question with no
                   canned button selected.

    Returns:
        dict with two keys:
            metrics      : dict following the UI tile schema (see below)
                           Maps to the structured part of analysis_result.
            context      : string passed to the LLM prompt.
                           Maps to commentary_facts in your existing flow —
                           the reduced, narrative-oriented subset of the
                           full deterministic output.

    TODO (merge): Decide how to handle mode == '' (custom question).
    Options:
        a) Run a default/generic processor
        b) Pass raw file context to agent without deterministic analysis
        c) Raise ValueError and return an error response in server.py
    """

    runner = SCRIPT_MAP.get(mode)

    if not runner:
        # TODO (merge): Replace with real handling once scripts are registered.
        raise ValueError(
            f"Unknown or unregistered analysis mode: '{mode}'. "
            f"Add it to SCRIPT_MAP in analyze.py (or processor.py once renamed)."
        )

    return runner(file_obj)


# ── Return schema ─────────────────────────────────────────────
# Each processor script (and this dispatcher) must return this shape.
# This is what KRONOS's server.py and frontend expect.
#
# Mapping to your existing names:
#   "context"   ← commentary_facts  (truncated narrative subset)
#   "metrics"   ← analysis_result   (structured data for tile rendering)
#
# {
#     "context": "deterministic_narrative_payload — the primary LLM-facing
#                 string. Scoped to the user ask, smaller than raw_results,
#                 richer and more faithful than commentary_facts.
#                 Built in processor.py, passed to ask_workbook_agent().",
#
#     "metrics": {
#         "Section Name": [
#             {
#                 "label":     "Metric Label",   # tile display name
#                 "value":     "4.1%",           # primary value
#                 "delta":     "+0.4pp",         # optional — omit if not applicable
#                 "sentiment": "negative"        # tile color:
#                                                #   positive → green
#                                                #   negative → red
#                                                #   warning  → amber
#                                                #   neutral  → grey
#             }
#         ]
#     }
# }
#
# NOTE on the JSON output issue you mentioned:
# If your deterministic analysis produces a large JSON and you're having
# trouble getting it to the LLM cleanly, commentary_facts is the right
# chokepoint — build it to be a flat, human-readable summary string rather
# than passing raw nested JSON. The LLM will narrate better from prose-style
# facts than from a JSON dump.
