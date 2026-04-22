# ── KRONOS · pipeline/analyze.py ──────────────────────────────
# The analysis dispatcher. server.py calls analyze(file, mode) here.
#
# Pipeline (per upload):
#   1. classifier.classify(file) reads every sheet, matches each to
#      a registered Template (Lending, etc.), validates, and returns
#      typed DataFrames keyed by template name.
#   2. For each matched template, we compute its cube once. Cubes
#      are the deterministic JSON the slicers consume.
#   3. The mode + the cubes drive routing to a slicer/processor
#      that returns { context, metrics }.
#
# Falls back to placeholder_processor() for:
#   - mode == ""             (custom question, no canned button)
#   - mode not in MODE_MAP   (button visible in UI but processor not wired yet)
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import io

import pandas as pd

from pipeline.cube.lending import compute_lending_cube
from pipeline.loaders.classifier import classify
from pipeline.processors.lending import firm_level as lending_firm_level
from pipeline.processors.lending import portfolio_summary as lending_portfolio_summary


# ══════════════════════════════════════════════════════════════
# ── MODE MAP ──────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# Each entry maps a frontend mode slug to:
#   - "template": which template's cube the slicer needs.
#   - "slicer":   callable(cube) -> { context, metrics }.
#
# Add new lending modes (concentration-risk, delinquency-trends, ...)
# alongside firm-level. Add new templates by extending the classifier
# registry in pipeline/loaders/classifier.py and adding a slicer here.
#
MODE_MAP: dict[str, dict] = {
    "firm-level": {
        "template": "lending",
        "slicer":   lending_firm_level.slice_firm_level,
    },
    "portfolio-summary": {
        "template": "lending",
        "slicer":   lending_portfolio_summary.slice_portfolio_summary,
    },
    # "concentration-risk":  { "template": "lending", "slicer": ... },
    # "delinquency-trends":  { "template": "lending", "slicer": ... },
    # ...
}


# ══════════════════════════════════════════════════════════════
# ── DISPATCHER ────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

def analyze(file_obj, mode: str) -> dict:
    """
    Routes the uploaded file through the appropriate pipeline.

    Args:
        file_obj : file-like object (Flask request.files['file'] or _Base64File)
        mode     : slug string (e.g. "firm-level"). Empty string for custom prompts.

    Returns:
        dict with keys "context" (str) and "metrics" (dict).

    Behavior:
        - Mode in MODE_MAP: classify → compute cube → slice
        - Mode not in MODE_MAP: fall back to placeholder_processor (reads the
                                 raw file again and produces a basic summary).
    """
    spec = MODE_MAP.get(mode)

    if not spec:
        return placeholder_processor(file_obj)

    # Classify the workbook. Raises ValueError on missing required columns
    # or unrecognized sheets — server.py catches and surfaces as a 500.
    classified = classify(file_obj)

    template_name = spec["template"]
    if template_name not in classified["classified"]:
        seen = [s["name"] for s in classified["metadata"]["sheets_seen"]]
        raise ValueError(
            f"Mode '{mode}' requires a {template_name} sheet, but the workbook "
            f"didn't include one. Sheets seen: {seen}."
        )

    df = classified["classified"][template_name]

    if template_name == "lending":
        cube = compute_lending_cube(df)
        return spec["slicer"](cube)

    # Future templates (traded_products, ...) get their compute path here.
    raise ValueError(f"No cube computer registered for template '{template_name}'.")


# ══════════════════════════════════════════════════════════════
# ── PLACEHOLDER PROCESSOR ─────────────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# Fallback that reads any Excel/CSV and returns a basic summary.
# Used when the user asks a custom question (mode == "") or selects
# a mode that hasn't been wired into MODE_MAP yet.

def placeholder_processor(file_obj) -> dict:
    file_bytes = file_obj.read()

    try:
        df = pd.read_excel(io.BytesIO(file_bytes))
    except Exception:
        df = pd.read_csv(io.BytesIO(file_bytes))

    row_count = len(df)
    col_count = len(df.columns)
    column_names = list(df.columns)

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

    cat_cols = df.select_dtypes(exclude="number").columns.tolist()[:10]
    cat_summaries = {}
    for col in cat_cols:
        top_vals = df[col].value_counts().head(5).to_dict()
        cat_summaries[col] = {
            "distinct_values": int(df[col].nunique()),
            "top_5": {str(k): int(v) for k, v in top_vals.items()},
        }

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
                f"  {col}: {stats['distinct_values']} distinct values. Top: {top}"
            )

    context = "\n".join(context_lines)

    metrics: dict = {
        "Portfolio Overview": [
            {"label": "Total Records", "value": f"{row_count:,}", "sentiment": "neutral"},
            {"label": "Data Columns",  "value": str(col_count),    "sentiment": "neutral"},
        ],
    }

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

    return {"context": context, "metrics": metrics}
