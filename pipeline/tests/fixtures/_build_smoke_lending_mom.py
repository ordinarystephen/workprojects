# ── KRONOS · pipeline/tests/fixtures/_build_smoke_lending_mom.py ──
# Generator for smoke_lending_mom.xlsx — the lifecycle/MoM smoke fixture.
#
# This workbook is the focused regression for the bucket-lifecycle work:
# the `_grouping_history` fix that pins `current` to the cube's latest
# period (so an exited bucket reports $0 instead of inflating section
# sums with prior-period totals), plus the four-slicer "(exited)" /
# "(new this period)" rendering layer.
#
# Distinct from smoke_lending.xlsx (the broad-coverage fixture) — this
# one isolates the lifecycle behaviors and is small enough that every
# expected total can be hand-arithmeticked from the FACILITIES list
# below in a few lines.
#
# Run from the repo root:
#   python -m pipeline.tests.fixtures._build_smoke_lending_mom
#
# Lifecycle scenarios exercised (P1 → P2):
#   - Industry "Crypto"     present P1 only  → EXITED
#   - Industry "AI Lending" present P2 only  → NEW
#   - Industry "Energy"     present both     → active (no marker)
#   - Horizontal "Leveraged Finance"          present P1 only → EXITED
#   - Horizontal "Global Recovery Management" present P2 only → NEW
#   - Rating bucket "Defaulted" (CDF)         present P1 only → EXITED
#   - Rating bucket "Non-Rated" (TBR)         present P2 only → NEW
#   - IG, NIG buckets present both periods   → active (no marker)
# Plus the bug-fix invariant:
#   - by_industry section sum in P2 must reconcile to firm P2 committed
#     (the old code over-stated section sum by $50M from the exited
#     Crypto bucket's pinned-to-prior-period current.totals.committed)
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from pathlib import Path

import pandas as pd

P1 = "2025-01-31"
P2 = "2025-02-28"

OUTPUT = Path(__file__).parent / "smoke_lending_mom.xlsx"


# ── Per-facility records ──────────────────────────────────────
#
# Exits (P1 only): F101 carries the Crypto industry exit AND keeps NIG
# alive in P1 only-via-this-row; F102 is the lone Defaulted (CDF) row,
# so its absence in P2 means the entire Defaulted bucket exits; F103
# is the lone LF=Y facility, so its absence in P2 means the LF
# horizontal exits.
#
# Active (both): F201 anchors Energy + IG across both periods so those
# buckets stay "active" (no lifecycle marker) and aren't false-positives
# in is_exited / is_new tests.
#
# News (P2 only): F301 introduces AI Lending; F302 introduces the only
# Non-Rated row (TBR), so the Non-Rated bucket appears for the first
# time in P2; F303 introduces the only GRM=Directly Managed row, so the
# GRM horizontal appears for the first time in P2; F304 keeps NIG
# alive in P2 (otherwise NIG would exit, which would shadow the
# Defaulted/Non-Rated lifecycle signals).
FACILITIES = [
    # ── EXITS (P1 only) ───────────────────────────────────────
    # F101 — Crypto industry, NIG (C10), only P1 → industry EXIT
    {
        "facility_id": "F101", "facility_name": "F101 Term Loan",
        "parent_code": "P101", "parent_name": "CryptoCo",
        "industry": "Crypto", "segment": "Investment Banking", "branch": "Singapore",
        "lf": "N", "grm": "N", "watch": "N",
        "committed": 50_000_000,
        "p1": {"pd_rating": "C10", "pd_value": 0.06, "reg": "Special Mention"},
        "p2": None,
    },
    # F102 — Defaulted (CDF), Energy industry, only P1 → Defaulted bucket EXIT
    {
        "facility_id": "F102", "facility_name": "F102 Workout",
        "parent_code": "P102", "parent_name": "DefaultCo",
        "industry": "Energy", "segment": "Investment Banking", "branch": "London",
        "lf": "N", "grm": "N", "watch": "N",
        "committed": 20_000_000,
        "p1": {"pd_rating": "CDF", "pd_value": 1.0, "reg": "Loss"},
        "p2": None,
    },
    # F103 — LF=Y, IG (C03), only P1 → LF horizontal EXIT
    {
        "facility_id": "F103", "facility_name": "F103 Acquisition Loan",
        "parent_code": "P103", "parent_name": "LF Holdings",
        "industry": "Energy", "segment": "Investment Banking", "branch": "London",
        "lf": "Y", "grm": "N", "watch": "N",
        "committed": 60_000_000,
        "p1": {"pd_rating": "C03", "pd_value": 0.0012, "reg": "Pass"},
        "p2": None,
    },

    # ── ACTIVE (both periods) ─────────────────────────────────
    # F201 — IG (C03), Energy, no flags, no changes → anchors active state
    {
        "facility_id": "F201", "facility_name": "F201 Term Loan",
        "parent_code": "P201", "parent_name": "Acme Energy",
        "industry": "Energy", "segment": "Corporate Banking", "branch": "New York",
        "lf": "N", "grm": "N", "watch": "N",
        "committed": 100_000_000,
        "p1": {"pd_rating": "C03", "pd_value": 0.0012, "reg": "Pass"},
        "p2": {"pd_rating": "C03", "pd_value": 0.0012, "reg": "Pass"},
    },

    # ── NEWS (P2 only) ────────────────────────────────────────
    # F301 — AI Lending industry, IG (C03), only P2 → industry NEW
    {
        "facility_id": "F301", "facility_name": "F301 Term Loan",
        "parent_code": "P301", "parent_name": "AILend",
        "industry": "AI Lending", "segment": "Corporate Banking", "branch": "New York",
        "lf": "N", "grm": "N", "watch": "N",
        "committed": 40_000_000,
        "p1": None,
        "p2": {"pd_rating": "C03", "pd_value": 0.0012, "reg": "Pass"},
    },
    # F302 — Non-Rated (TBR), Energy, only P2 → Non-Rated bucket NEW
    {
        "facility_id": "F302", "facility_name": "F302 Standby LC",
        "parent_code": "P302", "parent_name": "NonRatedCo",
        "industry": "Energy", "segment": "Corporate Banking", "branch": "New York",
        "lf": "N", "grm": "N", "watch": "N",
        "committed": 30_000_000,
        "p1": None,
        "p2": {"pd_rating": "TBR", "pd_value": 0.013, "reg": ""},
    },
    # F303 — GRM=Directly Managed, IG (C03), only P2 → GRM horizontal NEW
    {
        "facility_id": "F303", "facility_name": "F303 Workout",
        "parent_code": "P303", "parent_name": "GRMCo",
        "industry": "Energy", "segment": "Corporate Banking", "branch": "Singapore",
        "lf": "N", "grm": "Directly Managed", "watch": "N",
        "committed": 25_000_000,
        "p1": None,
        "p2": {"pd_rating": "C03", "pd_value": 0.0012, "reg": "Pass"},
    },
    # F304 — NIG (C10), Energy, only P2 → keeps NIG alive in P2
    # (without F304, NIG would exit alongside Crypto — would mask the
    # Defaulted/Non-Rated lifecycle as the central test signal).
    {
        "facility_id": "F304", "facility_name": "F304 Revolver",
        "parent_code": "P304", "parent_name": "BravoNIG",
        "industry": "Energy", "segment": "Corporate Banking", "branch": "London",
        "lf": "N", "grm": "N", "watch": "N",
        "committed": 50_000_000,
        "p1": None,
        "p2": {"pd_rating": "C10", "pd_value": 0.06, "reg": "Special Mention"},
    },
]


# ── Reg-rating → exposure-bucket mapping ──────────────────────
# Cube reads the per-row dollar columns directly; full committed lands
# in the column matching Regulatory Rating. Empty Reg → No Regulatory.
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
        "Partner Code":         fac["parent_code"],
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

        # Reg-rating breakdown — full committed in matching bucket
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
