# ── KRONOS · pipeline/tests/fixtures/_build_smoke_lending.py ──
# Generator for smoke_lending.xlsx.
#
# This script is the source of truth for the fixture workbook used by
# pipeline/tests/test_smoke_lending.py. The .xlsx file produced from
# this script is checked into the repo so the test suite has no
# generation step at run time. Re-run this script whenever the fixture
# definition below changes; the test will pick up the new workbook.
#
# Run from the repo root:
#   python -m pipeline.tests.fixtures._build_smoke_lending
#
# Fixture covers (intentionally — see the docstring at the top of
# smoke_lending_expected.py for the per-facility cheat sheet):
#   - all five rating-category buckets (IG / NIG / Distressed /
#     Defaulted / Non-Rated)
#   - both Non-Rated tokens (TBR + blank cell)
#   - blank Risk Assessment Industry → Unclassified bucket
#   - both horizontal portfolios (LF + GRM) and one overlap facility
#   - watchlist
#   - one new origination, one exit, one PD-rating change, one
#     reg-rating change between periods
#   - multiple industries, segments, branches so dim reconciliation
#     is exercised
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from pathlib import Path

import pandas as pd

P1 = "2025-01-31"
P2 = "2025-02-28"

OUTPUT = Path(__file__).parent / "smoke_lending.xlsx"


# ── Per-facility records ──────────────────────────────────────
#
# Each entry produces 1 or 2 workbook rows (one per period present).
# `pd_rating_p1` / `pd_rating_p2`: the literal cell value for PD Rating.
#   For Non-Rated we use the raw token ("TBR" or "" for blank) so the
#   classifier exercises pipeline/scales/pd_scale.NON_RATED_TOKENS.
# `pd_value_p1` / `pd_value_p2`: the decimal PD used to derive the
#   per-row Weighted Average PD Numerator. For Non-Rated rows we set
#   this to 0.013 (C07-equivalent), the upstream data-contract assumption
#   the cube documents but cannot enforce.
# `committed`: dollars, period-invariant (kept simple so totals are
#   easy to hand-compute).
# `outstanding`: 80% of committed, also period-invariant.
# `reg_*`: regulatory rating tokens. Empty string for Non-Rated rows
#   so they roll into "No Regulatory Rating Exposure" rather than
#   contributing to Pass/SM/SS/Dbt/Loss.

FACILITIES = [
    # F001 — IG (C03), present both periods, no changes
    {
        "facility_id": "F001", "facility_name": "F001 Term Loan",
        "parent_code": "P001", "parent_name": "Acme Corp",
        "industry": "Health Care", "segment": "Corporate Banking", "branch": "New York",
        "lf": "N", "grm": "N", "watch": "N",
        "committed": 100_000_000,
        "p1": {"pd_rating": "C03", "pd_value": 0.0012, "reg": "Pass"},
        "p2": {"pd_rating": "C03", "pd_value": 0.0012, "reg": "Pass"},
    },
    # F002 — NIG (C10), both periods, no changes
    {
        "facility_id": "F002", "facility_name": "F002 Revolver",
        "parent_code": "P002", "parent_name": "Bravo Inc",
        "industry": "Financial Services", "segment": "Corporate Banking", "branch": "London",
        "lf": "N", "grm": "N", "watch": "N",
        "committed": 50_000_000,
        "p1": {"pd_rating": "C10", "pd_value": 0.06, "reg": "Special Mention"},
        "p2": {"pd_rating": "C10", "pd_value": 0.06, "reg": "Special Mention"},
    },
    # F003 — both periods, downgrade C09 → C13 (lands in Distressed)
    # AND reg-rating change Pass → Substandard. Drives MoM change asserts.
    {
        "facility_id": "F003", "facility_name": "F003 Term Loan",
        "parent_code": "P003", "parent_name": "Charlie LLC",
        "industry": "Information Technology", "segment": "Investment Banking", "branch": "New York",
        "lf": "N", "grm": "N", "watch": "N",
        "committed": 30_000_000,
        "p1": {"pd_rating": "C09", "pd_value": 0.035, "reg": "Pass"},
        "p2": {"pd_rating": "C13", "pd_value": 0.27,  "reg": "Substandard"},
    },
    # F004 — Defaulted (CDF), only P2 (new origination)
    {
        "facility_id": "F004", "facility_name": "F004 Term Loan",
        "parent_code": "P004", "parent_name": "Delta Co",
        "industry": "Energy", "segment": "Investment Banking", "branch": "London",
        "lf": "N", "grm": "N", "watch": "N",
        "committed": 20_000_000,
        "p1": None,
        "p2": {"pd_rating": "CDF", "pd_value": 1.0, "reg": "Loss"},
    },
    # F005 — Non-Rated via TBR token, both periods
    {
        "facility_id": "F005", "facility_name": "F005 Standby LC",
        "parent_code": "P005", "parent_name": "Echo Ltd",
        "industry": "Health Care", "segment": "Corporate Banking", "branch": "New York",
        "lf": "N", "grm": "N", "watch": "N",
        "committed": 40_000_000,
        "p1": {"pd_rating": "TBR", "pd_value": 0.013, "reg": ""},
        "p2": {"pd_rating": "TBR", "pd_value": 0.013, "reg": ""},
    },
    # F006 — Non-Rated via BLANK PD Rating cell, only P1 (exit)
    {
        "facility_id": "F006", "facility_name": "F006 Bridge Loan",
        "parent_code": "P006", "parent_name": "Foxtrot Bank",
        "industry": "Information Technology", "segment": "Investment Banking", "branch": "Singapore",
        "lf": "N", "grm": "N", "watch": "N",
        "committed": 10_000_000,
        "p1": {"pd_rating": "", "pd_value": 0.013, "reg": ""},
        "p2": None,
    },
    # F007 — IG (C03), BLANK Risk Assessment Industry (Unclassified bucket)
    {
        "facility_id": "F007", "facility_name": "F007 Term Loan",
        "parent_code": "P007", "parent_name": "Gamma Group",
        "industry": "", "segment": "Wealth Management", "branch": "New York",
        "lf": "N", "grm": "N", "watch": "N",
        "committed": 80_000_000,
        "p1": {"pd_rating": "C03", "pd_value": 0.0012, "reg": "Pass"},
        "p2": {"pd_rating": "C03", "pd_value": 0.0012, "reg": "Pass"},
    },
    # F008 — Leveraged Finance, IG (C06), both periods
    {
        "facility_id": "F008", "facility_name": "F008 Acquisition Loan",
        "parent_code": "P008", "parent_name": "Hotel Trust",
        "industry": "Energy", "segment": "Investment Banking", "branch": "London",
        "lf": "Y", "grm": "N", "watch": "N",
        "committed": 60_000_000,
        "p1": {"pd_rating": "C06", "pd_value": 0.008, "reg": "Pass"},
        "p2": {"pd_rating": "C06", "pd_value": 0.008, "reg": "Pass"},
    },
    # F009 — GRM (Directly Managed), NIG (C10), watchlist Y, both periods
    {
        "facility_id": "F009", "facility_name": "F009 Workout Facility",
        "parent_code": "P009", "parent_name": "India Holdings",
        "industry": "Financial Services", "segment": "Corporate Banking", "branch": "Singapore",
        "lf": "N", "grm": "Directly Managed", "watch": "Y",
        "committed": 25_000_000,
        "p1": {"pd_rating": "C10", "pd_value": 0.06, "reg": "Substandard"},
        "p2": {"pd_rating": "C10", "pd_value": 0.06, "reg": "Substandard"},
    },
    # F010 — LF AND GRM overlap, IG (C03), both periods
    {
        "facility_id": "F010", "facility_name": "F010 Term Loan",
        "parent_code": "P010", "parent_name": "Juliet Partners",
        "industry": "Health Care", "segment": "Wealth Management", "branch": "New York",
        "lf": "Y", "grm": "Directly Managed", "watch": "N",
        "committed": 45_000_000,
        "p1": {"pd_rating": "C03", "pd_value": 0.0012, "reg": "Pass"},
        "p2": {"pd_rating": "C03", "pd_value": 0.0012, "reg": "Pass"},
    },
]


# ── Reg-rating → exposure-bucket mapping ──────────────────────
# The cube reads per-facility dollar amounts from the Pass/SM/SS/Dbt/
# Loss/NoReg columns directly (it does NOT reparse Regulatory Rating
# back into dollars). So each row puts its full committed exposure
# into the column matching its Regulatory Rating cell. Empty Reg
# Rating → No Regulatory Rating Exposure.

_REG_TO_COL = {
    "Pass":            "Pass Rated Exposure",
    "Special Mention": "Special Mention Rated Exposure",
    "Substandard":     "Substandard Rated Exposure",
    "Doubtful":        "Doubtful Rated Exposure",
    "Loss":            "Loss Rated Exposure",
    "":                "No Regulatory Rating Exposure",
}


def _build_row(fac: dict, period: str, period_data: dict) -> dict:
    """Materialize one workbook row (one facility × one period)."""
    committed = fac["committed"]
    outstanding = int(committed * 0.8)
    approved = int(committed * 1.5)
    pd_value = period_data["pd_value"]
    reg = period_data["reg"]
    reg_col = _REG_TO_COL[reg]

    row = {
        "Month End": period,

        # Hierarchy
        "Ultimate Parent Code": fac["parent_code"],
        "Ultimate Parent Name": fac["parent_name"],
        "Partner Code":         fac["parent_code"],   # 1 partner per parent for simplicity
        "Partner Name":         fac["parent_name"],
        "Facility ID":          fac["facility_id"],
        "Facility Name":        fac["facility_name"],

        # Ratings
        "PD Rating":            period_data["pd_rating"],
        "Regulatory Rating":    reg,

        # Categorical dims
        "Risk Assessment Industry":      fac["industry"],
        "Portfolio Segment Description": fac["segment"],
        "UBS Branch Name":               fac["branch"],

        # Passthrough
        "Reporting Sector Industry Name": fac["industry"] or "Unspecified",
        "Subsector Industry Name":        fac["industry"] or "Unspecified",
        "NACE Code":                      "0000",
        "Letter of Credit Fronting Flag": "N",
        "Credit Officer":                 "Officer 1",
        "Current Approval ID":            f"AP-{fac['facility_id']}",
        "Approval Date":                  "2024-06-01",
        "Maturity Date":                  "2030-06-01",
        "Loss Given Default (LGD)":       0.45,

        # Horizontal flags
        "Credit Watch List Flag":           fac["watch"],
        "Leveraged Finance Flag":           fac["lf"],
        "Global Recovery Management Flag":  fac["grm"],

        # Stocks
        "Approved Limit":       approved,
        "Committed Exposure":   committed,
        "Outstanding Exposure": outstanding,
        "Temporary Exposure":   0,
        "Take & Hold Exposure": 0,

        # Reg-rating breakdown — full committed lands in the matching bucket
        "Pass Rated Exposure":            0,
        "Special Mention Rated Exposure": 0,
        "Substandard Rated Exposure":     0,
        "Doubtful Rated Exposure":        0,
        "Loss Rated Exposure":            0,
        "No Regulatory Rating Exposure":  0,

        # Weighted-average numerators
        "Weighted Average PD Numerator":  pd_value * committed,
        "Weighted Average LGD Numerator": 0.45 * committed,
    }
    row[reg_col] = committed
    return row


def build_dataframe() -> pd.DataFrame:
    rows: list[dict] = []
    for fac in FACILITIES:
        for period_label, period_data in (("p1", fac["p1"]), ("p2", fac["p2"])):
            if period_data is None:
                continue
            period = P1 if period_label == "p1" else P2
            rows.append(_build_row(fac, period, period_data))
    return pd.DataFrame(rows)


def main() -> None:
    df = build_dataframe()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(OUTPUT, sheet_name="Lending", index=False)
    print(f"Wrote {OUTPUT} ({len(df)} rows, {len(df.columns)} columns).")


if __name__ == "__main__":
    main()
