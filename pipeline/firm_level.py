# ── KRONOS · pipeline/firm_level.py ───────────────────────────
# Firm-level view processor.
#
# Reads an uploaded Excel workbook and produces a high-level
# portfolio snapshot: parent/industry counts, total commitment,
# total outstanding, and criticized & classified exposure.
#
# Contract (matches the SCRIPT_MAP convention in analyze.py):
#   run(file_obj) -> { "context": str, "metrics": dict }
#
# - context: prose-style string handed to the LLM (the
#   deterministic_narrative_payload slot).
# - metrics: tile data for the Data Snapshot panel.
#
# Required columns in the workbook:
#   Ultimate Parent Code
#   Risk Assessment Industry
#   Committed Exposure
#   Outstanding Exposure
#   Special Mention Rated Exposure
#   Substandard Rated Exposure
#   Doubtful Rated Exposure
#   Loss Rated Exposure
# ──────────────────────────────────────────────────────────────

import io
import pandas as pd


REQUIRED_COLUMNS = [
    "Ultimate Parent Code",
    "Risk Assessment Industry",
    "Committed Exposure",
    "Outstanding Exposure",
    "Special Mention Rated Exposure",
    "Substandard Rated Exposure",
    "Doubtful Rated Exposure",
    "Loss Rated Exposure",
]

NUMERIC_COLUMNS = [
    "Committed Exposure",
    "Outstanding Exposure",
    "Special Mention Rated Exposure",
    "Substandard Rated Exposure",
    "Doubtful Rated Exposure",
    "Loss Rated Exposure",
]


def run(file_obj) -> dict:
    """
    Firm-level portfolio summary.

    Args:
        file_obj: file-like object from Flask request.files['file']

    Returns:
        dict with keys "context" (str) and "metrics" (dict).
    """

    # Read the upload into memory so pandas can parse it.
    file_bytes = file_obj.read()
    df = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl")

    # Fail early with a clear message if the workbook isn't shaped
    # like a firm-level file. server.py turns this into a 500 with
    # the message, which the frontend surfaces to the user.
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            "Firm-level analysis requires these columns: "
            + ", ".join(missing)
        )

    # Coerce numerics so stray strings don't poison the sums.
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # ── Calculations (from the reference script) ──────────────
    distinct_parents = int(df["Ultimate Parent Code"].nunique())
    distinct_industries = int(df["Risk Assessment Industry"].nunique())

    total_commitment_mm = df["Committed Exposure"].sum() / 1_000_000
    total_outstanding_mm = df["Outstanding Exposure"].sum() / 1_000_000

    cc_exposure_mm = (
        df["Special Mention Rated Exposure"]
        + df["Substandard Rated Exposure"]
        + df["Doubtful Rated Exposure"]
        + df["Loss Rated Exposure"]
    ).sum() / 1_000_000

    cc_ratio = (
        cc_exposure_mm / total_commitment_mm
        if total_commitment_mm != 0
        else 0
    )

    total_commitment_mm = round(total_commitment_mm, 2)
    total_outstanding_mm = round(total_outstanding_mm, 2)
    cc_exposure_mm = round(cc_exposure_mm, 2)
    cc_pct = round(cc_ratio * 100, 2)

    # ── Context for the LLM ───────────────────────────────────
    # Prose-ish format narrates better than raw JSON. Every value
    # the LLM might cite appears verbatim here so the number
    # cross-check in validate.py can verify it.
    context = (
        "Firm-level portfolio summary (from uploaded workbook):\n"
        f"- Distinct ultimate parents: {distinct_parents:,}\n"
        f"- Distinct risk assessment industries: {distinct_industries:,}\n"
        f"- Total committed exposure: ${total_commitment_mm:,.2f} million\n"
        f"- Total outstanding exposure: ${total_outstanding_mm:,.2f} million\n"
        f"- Total criticized & classified exposure: "
        f"${cc_exposure_mm:,.2f} million "
        f"(Special Mention + Substandard + Doubtful + Loss)\n"
        f"- Criticized & classified as % of commitment: {cc_pct:.2f}%\n"
    )

    # ── Metrics for the Data Snapshot tiles ───────────────────
    # Shape: { "Section": [{ label, value, sentiment }] }
    metrics = {
        "Firm-Level Overview": [
            {
                "label": "Distinct Parents",
                "value": f"{distinct_parents:,}",
                "sentiment": "neutral",
            },
            {
                "label": "Distinct Industries",
                "value": f"{distinct_industries:,}",
                "sentiment": "neutral",
            },
            {
                "label": "Total Commitment",
                "value": f"${total_commitment_mm:,.1f}M",
                "sentiment": "neutral",
            },
            {
                "label": "Total Outstanding",
                "value": f"${total_outstanding_mm:,.1f}M",
                "sentiment": "neutral",
            },
            {
                "label": "Criticized & Classified",
                "value": f"${cc_exposure_mm:,.1f}M",
                "sentiment": "neutral",
            },
            {
                "label": "C&C % of Commitment",
                "value": f"{cc_pct:.2f}%",
                "sentiment": "neutral",
            },
        ],
    }

    return {
        "context": context,
        "metrics": metrics,
    }
