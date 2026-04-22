# ── KRONOS · pipeline/analyze.py ──────────────────────────────
# The analysis dispatcher. server.py calls analyze(file, mode, parameters).
#
# Pipeline (per upload):
#   1. classifier.classify(file) reads every sheet, matches each to
#      a registered Template (Lending, etc.), validates, and returns
#      typed DataFrames keyed by template name.
#   2. For each matched template, we compute its cube once. Cubes
#      are the deterministic JSON the slicers consume.
#   3. The mode + cube + validated parameters drive routing to a
#      slicer/processor that returns { context, metrics, verifiable_values }.
#
# Mode resolution lives in pipeline/registry.py (YAML-driven).
# Adding a new mode means editing config/modes.yaml and writing a
# slicer decorated with @register_slicer — no changes here.
#
# Falls back to placeholder_processor() for:
#   - mode == ""               (custom question, no canned button)
#   - mode is unknown          (slug not in registry)
#   - mode is a placeholder    (button visible in UI but slicer not wired yet)
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import io

import pandas as pd

from pipeline.cube.lending import compute_lending_cube
from pipeline.loaders.classifier import classify
from pipeline.registry import (
    get_mode,
    get_slicer,
    validate_parameters,
)


# ══════════════════════════════════════════════════════════════
# ── DISPATCHER ────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

class ModeNotImplementedError(RuntimeError):
    """Raised when a placeholder mode is invoked. server.py converts
    this into a 501 with the slug attached."""

    def __init__(self, slug: str):
        super().__init__(f"Mode '{slug}' is registered but not yet implemented.")
        self.slug = slug


def analyze(file_obj, mode: str, parameters: dict | None = None) -> dict:
    """
    Routes the uploaded file through the appropriate pipeline.

    Args:
        file_obj   : file-like object (Flask request.files['file'] or _Base64File)
        mode       : slug string (e.g. "firm-level"). Empty string for custom prompts.
        parameters : dict of mode-specific parameters (validated against the
                     mode's parameter schema in the registry). May be None /
                     empty for parameterless modes.

    Returns:
        dict with keys "context" (str), "metrics" (dict), and
        "verifiable_values" (dict — may be empty for placeholder path).

    Raises:
        ModeNotImplementedError : mode exists in registry but status != "active"
        ParameterError          : caller-supplied parameters don't validate
        ValueError              : workbook missing the required template sheet
    """
    parameters = parameters or {}

    # ── Step 1: Resolve mode ───────────────────────────────────
    # Empty mode + unknown slug both fall through to the placeholder
    # processor. Known-but-not-active modes raise ModeNotImplementedError
    # so the UI can surface a clear "coming soon" message instead of
    # silently swallowing the request into the placeholder summary.
    if not mode:
        return placeholder_processor(file_obj)

    mode_def = get_mode(mode)
    if mode_def is None:
        return placeholder_processor(file_obj)
    if mode_def.status != "active":
        raise ModeNotImplementedError(mode)

    slicer_entry = get_slicer(mode_def.cube_slice)
    if slicer_entry is None:
        # Should be impossible — registry startup validation enforces this.
        raise RuntimeError(
            f"Mode '{mode}' points at slicer '{mode_def.cube_slice}' "
            "which is not registered. (Registry validation should have caught this.)"
        )

    # ── Step 2: Classify ──────────────────────────────────────
    # Raises ValueError on missing required columns or unrecognized
    # sheets — server.py catches and surfaces as a 500.
    classified = classify(file_obj)

    # The classifier-template-name comes from the slicer module's
    # location in the pipeline.processors.<template> tree. For now
    # all slicers are lending; when traded-products lands we'll
    # surface the template name on the slicer registration itself.
    template_name = "lending"
    if template_name not in classified["classified"]:
        seen = [s["name"] for s in classified["metadata"]["sheets_seen"]]
        raise ValueError(
            f"Mode '{mode}' requires a {template_name} sheet, but the workbook "
            f"didn't include one. Sheets seen: {seen}."
        )

    # ── Step 3: Compute cube ──────────────────────────────────
    df = classified["classified"][template_name]
    cube = compute_lending_cube(df)

    # ── Step 4: Validate parameters against the cube ──────────
    # Re-validates here (server.py also validates pre-classify, but
    # without the cube). The cube-aware pass enforces enum membership
    # for `source: cube.<field>` parameters.
    cleaned_params = validate_parameters(mode_def, parameters, cube=cube)

    # ── Step 5: Slice ─────────────────────────────────────────
    slicer_fn = slicer_entry["fn"]
    if cleaned_params:
        result = slicer_fn(cube, **cleaned_params)
    else:
        result = slicer_fn(cube)

    # Defensive default — slicers that predate the verifiable_values
    # contract still return without the key.
    result.setdefault("verifiable_values", {})
    return result


# ══════════════════════════════════════════════════════════════
# ── PLACEHOLDER PROCESSOR ─────────────────────────────────────
# ══════════════════════════════════════════════════════════════
#
# Fallback that reads any Excel/CSV and returns a basic summary.
# Used when the user asks a custom question (mode == "") or selects
# a slug that isn't in the registry.

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

    return {"context": context, "metrics": metrics, "verifiable_values": {}}
