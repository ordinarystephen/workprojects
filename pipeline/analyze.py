# ── KRONOS · pipeline/analyze.py ──────────────────────────────
# The analysis dispatcher. server.py calls analyze(file, mode) here.
#
# This file has two jobs:
#   1. ROUTE — map the mode slug to the correct processor script
#   2. PLACEHOLDER — provide a working default processor that reads
#      any Excel/CSV and returns a pandas summary so the app runs
#      end-to-end before your real deterministic scripts are wired in
#
# ── When you integrate your real processor scripts ────────────
# Your existing processor.py is the real version of this file.
# When you're ready to merge, you have two options:
#
#   Option A (recommended): Keep this file as the thin dispatcher.
#   Add imports from your real scripts at the top of SCRIPT_MAP,
#   and register each mode. The placeholder_processor() below can
#   be removed once all modes are covered.
#
#   Option B: Replace this file entirely with your processor.py
#   and update the import in server.py:
#       from pipeline.analyze import analyze
#
# ── SCRIPT_MAP keys must match prompts.json "mode" slugs ──────
# Current slugs: portfolio-summary, concentration-risk,
#                delinquency-trends, risk-segments,
#                exec-briefing, stress-outlook
# ──────────────────────────────────────────────────────────────

import io
import json
import pandas as pd


# ══════════════════════════════════════════════════════════════
# ── SCRIPT MAP ────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# Maps mode slugs → processor functions.
# Each value must be a callable: fn(file_obj) -> dict
# with return shape: { "context": str, "metrics": dict }
#
# ┌─────────────────────────────────────────────────────────────┐
# │  TODO: Uncomment and fill in each entry as you port your    │
# │  real processor scripts into this project.                  │
# │                                                             │
# │  Example once your scripts are in place:                    │
# │    from pipeline.scripts import portfolio_summary           │
# │    SCRIPT_MAP = {                                           │
# │        "portfolio-summary": portfolio_summary.run,          │
# │        ...                                                  │
# │    }                                                        │
# │                                                             │
# │  Or if your processor.py handles all modes with internal    │
# │  branching, import it once and map all slugs to it:         │
# │    from pipeline import processor                           │
# │    SCRIPT_MAP = {                                           │
# │        "portfolio-summary": processor.run,                  │
# │        "concentration-risk": processor.run,                 │
# │        ...                                                  │
# │    }                                                        │
# └─────────────────────────────────────────────────────────────┘
#
SCRIPT_MAP: dict = {
    # "portfolio-summary":   portfolio_summary.run,   # TODO
    # "concentration-risk":  concentration_risk.run,  # TODO
    # "delinquency-trends":  delinquency_trends.run,  # TODO
    # "risk-segments":       risk_segments.run,        # TODO
    # "exec-briefing":       exec_briefing.run,        # TODO
    # "stress-outlook":      stress_outlook.run,       # TODO
}


# ══════════════════════════════════════════════════════════════
# ── PLACEHOLDER PROCESSOR ─────────────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# A working default that reads any Excel or CSV file and returns:
#   - context:  a readable summary string for the LLM
#   - metrics:  basic tile data (row count, columns, numeric stats)
#
# This runs when:
#   a) The mode has no entry in SCRIPT_MAP (not yet wired), OR
#   b) mode is "" (user typed a custom question, no button selected)
#
# ┌─────────────────────────────────────────────────────────────┐
# │  TODO: Replace this function with your real processor       │
# │  logic once you've ported processor.py. Until then, this    │
# │  gives the LLM real data from the uploaded file to work     │
# │  with — it just isn't the mode-specific reduction.          │
# └─────────────────────────────────────────────────────────────┘
#
def placeholder_processor(file_obj) -> dict:
    """
    Reads an Excel or CSV file and produces a basic analysis payload.
    Used as a fallback until real processor scripts are registered.

    Args:
        file_obj: file-like object from Flask request.files['file']

    Returns:
        dict:
            "context"  — formatted string summarizing the workbook data.
                         This is passed to ask_agent() as the LLM's
                         data input (the deterministic_narrative_payload slot).
            "metrics"  — dict of tile data for the Data Snapshot panel.
                         Shape: { "Section": [{ label, value, sentiment }] }
    """

    # ── Read the file bytes ────────────────────────────────────
    # Flask file objects are file-like streams. We read all bytes
    # so we can pass them to pandas via BytesIO (in-memory buffer).
    file_bytes = file_obj.read()

    # ── Parse as Excel or CSV ─────────────────────────────────
    # Try Excel first. If that fails (e.g. it's a CSV), fall back.
    try:
        df = pd.read_excel(io.BytesIO(file_bytes))
    except Exception:
        df = pd.read_csv(io.BytesIO(file_bytes))

    # ── Basic shape info ──────────────────────────────────────
    row_count = len(df)
    col_count = len(df.columns)
    column_names = list(df.columns)

    # ── Numeric column summaries ──────────────────────────────
    # Select columns with numeric data types and compute basic stats.
    # Capped at 15 columns to keep the context string manageable.
    numeric_cols = df.select_dtypes(include="number").columns.tolist()[:15]
    numeric_summaries = {}
    for col in numeric_cols:
        col_data = df[col].dropna()
        if len(col_data) == 0:
            continue
        numeric_summaries[col] = {
            "count":  int(col_data.count()),
            "mean":   round(float(col_data.mean()), 4),
            "median": round(float(col_data.median()), 4),
            "min":    round(float(col_data.min()), 4),
            "max":    round(float(col_data.max()), 4),
            "nulls":  int(df[col].isna().sum()),
        }

    # ── Non-numeric (categorical) column summaries ─────────────
    # For text/category columns, show distinct value count and
    # top 5 most common values. Capped at 10 columns.
    cat_cols = df.select_dtypes(exclude="number").columns.tolist()[:10]
    cat_summaries = {}
    for col in cat_cols:
        top_vals = df[col].value_counts().head(5).to_dict()
        cat_summaries[col] = {
            "distinct_values": int(df[col].nunique()),
            "top_5": {str(k): int(v) for k, v in top_vals.items()},
        }

    # ── Build context string for the LLM ──────────────────────
    # This is the "deterministic_narrative_payload" slot.
    # It's a readable text summary — prose + key figures —
    # rather than raw JSON (LLMs narrate better from text).
    #
    # TODO: When you port processor.py, replace this section with
    # your actual deterministic_narrative_payload construction.
    # The shape this returns (a string) stays the same.
    context_lines = [
        f"Portfolio workbook: {row_count:,} records across {col_count} columns.",
        f"Columns: {', '.join(column_names)}",
        "",
    ]

    if numeric_summaries:
        context_lines.append("Numeric field summaries:")
        for col, stats in numeric_summaries.items():
            context_lines.append(
                f"  {col}: "
                f"mean={stats['mean']:,}, "
                f"median={stats['median']:,}, "
                f"min={stats['min']:,}, "
                f"max={stats['max']:,}, "
                f"nulls={stats['nulls']}"
            )
        context_lines.append("")

    if cat_summaries:
        context_lines.append("Categorical field summaries:")
        for col, stats in cat_summaries.items():
            top = ", ".join(f"{k} ({v})" for k, v in stats["top_5"].items())
            context_lines.append(
                f"  {col}: {stats['distinct_values']} distinct values. "
                f"Top: {top}"
            )

    context = "\n".join(context_lines)

    # ── Build metrics for the Data Snapshot tiles ──────────────
    # The frontend renders whatever shape you return here.
    # Schema: { "Section Name": [{ label, value, sentiment, delta? }] }
    # sentiment: "positive" | "negative" | "warning" | "neutral"
    #
    # TODO: Replace these placeholder tiles with your real metrics
    # once your processor scripts are wired in. The tile names,
    # values, and sentiments should come from your deterministic output.
    metrics: dict = {
        "Portfolio Overview": [
            {
                "label": "Total Records",
                "value": f"{row_count:,}",
                "sentiment": "neutral",
            },
            {
                "label": "Data Columns",
                "value": str(col_count),
                "sentiment": "neutral",
            },
        ],
    }

    # ── Add a tile per numeric column (first 4 only) ───────────
    # This gives the tiles panel some real content in placeholder mode.
    # Remove this block when your real metrics are wired.
    if numeric_summaries:
        numeric_tiles = []
        for col, stats in list(numeric_summaries.items())[:4]:
            numeric_tiles.append({
                "label": col,
                "value": f"{stats['mean']:,}",
                "sentiment": "neutral",
            })
        if numeric_tiles:
            metrics["Field Averages (Placeholder)"] = numeric_tiles

    return {
        "context": context,
        "metrics": metrics,
    }


# ══════════════════════════════════════════════════════════════
# ── DISPATCHER ────────────────────────────════════════════════
# ══════════════════════════════════════════════════════════════

def analyze(file_obj, mode: str) -> dict:
    """
    Routes the uploaded file to the correct processor based on mode.

    Called from server.py's /upload route with the uploaded file
    and the mode slug from the frontend.

    Args:
        file_obj : file-like object from Flask request.files['file']
        mode     : slug string (e.g. "portfolio-summary").
                   Empty string "" if user typed a custom question
                   without selecting a canned button.

    Returns:
        dict:
            "context"  — string passed to ask_agent() as the data payload
            "metrics"  — dict of tile data for the Data Snapshot panel

    Behavior:
        - If mode is in SCRIPT_MAP: routes to that processor's run() fn
        - If mode is not in SCRIPT_MAP (including ""):
          falls back to placeholder_processor()
          (this covers both unregistered modes and custom questions)
    """

    runner = SCRIPT_MAP.get(mode)

    if runner:
        # ── Real processor found — run it ──────────────────────
        # When your real scripts are registered, they take over here.
        # Each must accept file_obj and return { context, metrics }.
        return runner(file_obj)
    else:
        # ── Fallback: placeholder processor ────────────────────
        # Runs when:
        #   - mode is "" (custom question, no button selected)
        #   - mode key exists in prompts.json but not yet in SCRIPT_MAP
        #     (i.e. the button is visible but the processor isn't wired yet)
        #
        # TODO: Once all modes are registered in SCRIPT_MAP, you may
        # want to raise an error for unknown modes instead of falling
        # back silently. For now, the fallback lets the app run fully
        # during development before all processors are ported.
        return placeholder_processor(file_obj)
