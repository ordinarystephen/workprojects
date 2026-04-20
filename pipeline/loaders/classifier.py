# ── KRONOS · pipeline/loaders/classifier.py ───────────────────
# Workbook classifier.
#
# Reads every sheet in an uploaded workbook and matches each one to
# a registered Template by signature. A workbook can contain 1 or 2
# matched sheets (Lending, Traded Products) — anything else is
# silently skipped (probably a metadata / readme / notes tab).
#
# Returns:
#   {
#     "classified": { "<template_name>": validated_df, ... },
#     "metadata":   {
#         "sheets_seen":      [{name, columns_seen, matched_template}],
#         "warnings":         [ValidationWarning, ...],
#     }
#   }
#
# Raises ValueError if:
#   - No sheets match any template.
#   - A sheet matches more than one template (signatures collide).
#   - A matched sheet fails template.validate() (missing required col).
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import io
from dataclasses import asdict
from typing import Iterable

import pandas as pd

from pipeline.templates.base import Template, ValidationWarning
from pipeline.templates.lending import LendingTemplate


# Registered templates the classifier can recognize.
# Append future templates (TradedProductsTemplate, etc.) here.
TEMPLATES: list[type[Template]] = [
    LendingTemplate,
]


def classify(file_obj) -> dict:
    """
    Read the uploaded workbook, classify each sheet, and return
    validated DataFrames keyed by template name.

    Args:
        file_obj: file-like with .read() (Flask file object or _Base64File).

    Returns:
        dict with keys "classified" (template_name → DataFrame) and
        "metadata" (sheets_seen list + warnings list).
    """
    file_bytes = file_obj.read()

    # sheet_name=None → dict[sheet_name, DataFrame]
    sheets = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, engine="openpyxl")

    classified: dict[str, pd.DataFrame] = {}
    sheets_seen: list[dict] = []
    warnings: list[ValidationWarning] = []

    for sheet_name, df in sheets.items():
        cols = set(df.columns)
        matches = [T for T in TEMPLATES if T.SIGNATURE.issubset(cols)]

        entry = {
            "name": sheet_name,
            "row_count": len(df),
            "columns_seen": len(cols),
            "matched_template": None,
        }

        if len(matches) == 0:
            sheets_seen.append(entry)
            continue

        if len(matches) > 1:
            raise ValueError(
                f"Sheet '{sheet_name}' matches multiple templates: "
                f"{[T.NAME for T in matches]}. Signatures collide."
            )

        T = matches[0]
        if T.NAME in classified:
            # MVP: assume users won't duplicate-template a workbook.
            # If they do, fail loudly rather than silently merging.
            raise ValueError(
                f"Multiple sheets matched the '{T.NAME}' template "
                f"(latest: '{sheet_name}'). Combine them upstream and re-upload."
            )

        validated, sheet_warnings = T.validate(df)
        classified[T.NAME] = validated
        warnings.extend(sheet_warnings)

        entry["matched_template"] = T.NAME
        entry["row_count"] = len(validated)
        sheets_seen.append(entry)

    if not classified:
        recognized = ", ".join(T.NAME for T in TEMPLATES)
        raise ValueError(
            "No recognized templates found in workbook. "
            f"Looked for: {recognized}. "
            f"Sheets seen: {[s['name'] for s in sheets_seen]}."
        )

    return {
        "classified": classified,
        "metadata": {
            "sheets_seen": sheets_seen,
            "warnings":    [asdict(w) for w in warnings],
        },
    }
