# ── KRONOS · pipeline/tests/fixtures/smoke_lending_expected.py ──
# Hand-computed expected values for the smoke fixture.
#
# Every number below is derived from the FACILITIES list in
# _build_smoke_lending.py — NOT from running the cube. Re-running
# the cube and copy-pasting its output would just assert the cube
# agrees with itself (circular). When the fixture changes, hand-recompute
# every affected value in this file before regenerating the .xlsx.
#
# ── Per-facility cheat sheet ──────────────────────────────────
# (Committed in $; PD value used for WAPD numerator = PD × Committed;
#  outstanding = 80% of committed; LGD numerator = 0.45 × committed
#  for every row, every period.)
#
# F001  Acme Corp        Health Care            CB / NY  100M  C03/Pass         both    no changes
# F002  Bravo Inc        Financial Services     CB / Lo   50M  C10/SM           both    NIG, no changes
# F003  Charlie LLC      Information Tech.      IB / NY   30M  C09/Pass→C13/SS  both    PD downgrade + reg downgrade
# F004  Delta Co         Energy                 IB / Lo   20M  CDF/Loss         P2 only NEW ORIGINATION, Defaulted
# F005  Echo Ltd         Health Care            CB / NY   40M  TBR/(blank)      both    Non-Rated (TBR token)
# F006  Foxtrot Bank     Information Tech.      IB / Sg   10M  (blank)/(blank)  P1 only EXIT, Non-Rated (blank token)
# F007  Gamma Group      (blank → Unclassified) WM / NY   80M  C03/Pass         both    Unclassified industry
# F008  Hotel Trust      Energy                 IB / Lo   60M  C06/Pass         both    Leveraged Finance horizontal
# F009  India Holdings   Financial Services     CB / Sg   25M  C10/SS           both    GRM (Directly Managed) + WATCHLIST
# F010  Juliet Partners  Health Care            WM / NY   45M  C03/Pass         both    LF + GRM overlap
#
# Period labels: P1 = 2025-01-31, P2 = 2025-02-28.
# Latest period = P2; counts/sums below are P2 unless noted.
#
# ── Derived totals (hand-arithmetic) ──────────────────────────
# P2 facilities = {F001, F002, F003, F004, F005, F007, F008, F009, F010} = 9
# Committed P2  = 100 + 50 + 30 + 20 + 40 + 80 + 60 + 25 + 45 = 450M
# Outstanding   = 0.80 × 450M = 360M  (matches per-facility 80M+40M+24M+16M+32M+64M+48M+20M+36M)
# Approved Lim. = 1.50 × 450M = 675M
# CC P2         = SM + SS + Dbt + Loss = 50 + (30+25) + 0 + 20 = 125M
#
# WAPD num P2 = 0.0012·100 + 0.06·50 + 0.27·30 + 1.0·20 + 0.013·40
#             + 0.0012·80 + 0.008·60 + 0.06·25 + 0.0012·45  (all ×1M)
#             = (0.12 + 3.0 + 8.1 + 20.0 + 0.52 + 0.096 + 0.48 + 1.5 + 0.054) M
#             = 33.87 M
# WAPD raw    = 33.87 / 450 = 0.07526666...  →  C-scale: > C10(0.06) ≤ C11(0.10)  →  "C11"
#
# WALGD num P2 = 0.45 × 450M = 202.5M
# WALGD raw    = 0.45 → "45.00%"
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from datetime import date


# Per-facility raw committed (used by reconciliation tests, in $).
COMMITTED = {
    "F001": 100_000_000, "F002": 50_000_000, "F003": 30_000_000,
    "F004": 20_000_000,  "F005": 40_000_000, "F006": 10_000_000,
    "F007": 80_000_000,  "F008": 60_000_000, "F009": 25_000_000,
    "F010": 45_000_000,
}


EXPECTED = {
    # ── Periods ───────────────────────────────────────────────
    "periods":          [date(2025, 1, 31), date(2025, 2, 28)],
    "current_period":   date(2025, 2, 28),
    "prior_period":     date(2025, 1, 31),

    # ── Firm-level totals (latest period) ─────────────────────
    "firm_level": {
        "totals": {
            # Σ committed = 100+50+30+20+40+80+60+25+45 = 450M
            "committed":             450_000_000.0,
            # Σ outstanding = 80+40+24+16+32+64+48+20+36 = 360M
            "outstanding":           360_000_000.0,
            # CC = SM(F002=50) + SS(F003=30 + F009=25) + Loss(F004=20) = 125M
            "criticized_classified": 125_000_000.0,
            # Approved Limit = 1.5 × committed for every row → 1.5 × 450M
            "approved_limit":        675_000_000.0,
        },
        "counts": {
            # 9 distinct parents in P2 (P001..P010 minus P006)
            "parents":    9,
            # Partner = parent in the fixture, so same count
            "partners":   9,
            # 9 distinct facilities in P2 (F001..F010 minus F006)
            "facilities": 9,
            # 5 distinct industries: Energy, Financial Services, Health Care,
            # Information Technology, Unclassified (F007's blank cell normalized)
            "industries": 5,
        },
        "wapd": {
            # 33_870_000 / 450_000_000 = 0.07526666...
            "raw":     33_870_000.0 / 450_000_000.0,
            # > C10 (0.06) and ≤ C11 (0.10) → C11
            "display": "C11",
        },
        "walgd": {
            # 202_500_000 / 450_000_000 = 0.45 exactly
            "raw":     0.45,
            "display": "45.00%",
        },
    },

    # ── IG / NIG split (latest period) ────────────────────────
    # IG (C00..C07): F001 + F007 + F008 + F010 = 100 + 80 + 60 + 45 = 285M, 4 facilities
    # NIG (C08..C13): F002 + F003 + F009     =  50 + 30 + 25      = 105M, 3 facilities
    "by_ig_status": {
        "Investment Grade":     {"committed": 285_000_000.0, "facility_count": 4},
        "Non-Investment Grade": {"committed": 105_000_000.0, "facility_count": 3},
    },

    # ── Distressed sub-stat (C13 subset of NIG, latest period) ─
    # F003 in P2 = C13 → 30M committed, 24M outstanding, 1 facility
    "by_distressed": {
        "committed":      30_000_000.0,
        "outstanding":    24_000_000.0,
        "facility_count": 1,
    },

    # ── Defaulted (CDF) — top-level peer to IG/NIG ────────────
    # F004 in P2 = CDF → 20M, 1 facility
    "by_defaulted": {
        "committed":      20_000_000.0,
        "facility_count": 1,
    },

    # ── Non-Rated — top-level peer ────────────────────────────
    # F005 in P2 = TBR → 40M, 1 facility (F006 was P1-only blank)
    "by_non_rated": {
        "committed":      40_000_000.0,
        "facility_count": 1,
    },

    # ── Rating coverage invariant ─────────────────────────────
    # IG (285) + NIG (105) + Defaulted (20) + Non-Rated (40) = 450 = firm
    # Distressed is a SUB-stat of NIG (already counted) — not added here.
    "coverage_committed_total": 450_000_000.0,

    # ── Industries ────────────────────────────────────────────
    # P2 by_industry (sorted alphabetically by available_industries):
    #   Energy:                F004 + F008 = 20 + 60 = 80M
    #   Financial Services:    F002 + F009 = 50 + 25 = 75M
    #   Health Care:           F001 + F005 + F010 = 100 + 40 + 45 = 185M
    #   Information Tech.:     F003 = 30M
    #   Unclassified:          F007 = 80M
    # Sum = 80 + 75 + 185 + 30 + 80 = 450M ✓ reconciles
    "by_industry_committed": {
        "Energy":                 80_000_000.0,
        "Financial Services":     75_000_000.0,
        "Health Care":           185_000_000.0,
        "Information Technology": 30_000_000.0,
        "Unclassified":           80_000_000.0,
    },
    "available_industries": [
        "Energy",
        "Financial Services",
        "Health Care",
        "Information Technology",
        "Unclassified",
    ],
    "unclassified_industry": {
        "committed":      80_000_000.0,
        "facility_count": 1,
    },

    # ── Segments / branches (sums must reconcile to firm) ─────
    # Segment P2:
    #   Corporate Banking:   F001 + F002 + F005 + F009 = 100 + 50 + 40 + 25 = 215M
    #   Investment Banking:  F003 + F004 + F008        =  30 + 20 + 60      = 110M
    #   Wealth Management:   F007 + F010               =  80 + 45           = 125M
    "by_segment_committed": {
        "Corporate Banking":  215_000_000.0,
        "Investment Banking": 110_000_000.0,
        "Wealth Management":  125_000_000.0,
    },
    # Branch P2:
    #   New York:  F001 + F003 + F005 + F007 + F010 = 100+30+40+80+45 = 295M
    #   London:    F002 + F004 + F008              =  50+20+60        = 130M
    #   Singapore: F009                            =  25M
    "by_branch_committed": {
        "London":    130_000_000.0,
        "New York":  295_000_000.0,
        "Singapore": 25_000_000.0,
    },

    # ── Horizontal portfolios (P2) ────────────────────────────
    # LF:  F008 + F010 = 60 + 45 = 105M, 2 facilities
    # GRM: F009 + F010 = 25 + 45 =  70M, 2 facilities
    # Note F010 appears in BOTH (overlap covered by horizontal-overlap test).
    "by_horizontal": {
        "Leveraged Finance":          {"committed": 105_000_000.0, "facility_count": 2},
        "Global Recovery Management": {"committed":  70_000_000.0, "facility_count": 2},
    },
    "available_horizontals": [
        "Global Recovery Management",
        "Leveraged Finance",
    ],
    # Facilities in each horizontal (used by overlap test)
    "horizontal_members": {
        "Leveraged Finance":          {"F008", "F010"},
        "Global Recovery Management": {"F009", "F010"},
    },
    "horizontal_overlap_facility": "F010",

    # ── Watchlist (P2) ────────────────────────────────────────
    # F009 only: 25M committed, 20M outstanding, 1 facility
    "watchlist": {
        "committed":      25_000_000.0,
        "outstanding":    20_000_000.0,
        "facility_count": 1,
    },

    # ── Bucket-membership PD Rating values (uppercased) ───────
    # Tests assert each member of the bucket has one of these PD values.
    "non_rated_pd_values": {"TBR", ""},   # F005 = "TBR", F006 was "" (P1 only)
    "distressed_pd_value": "C13",
    "defaulted_pd_value":  "CDF",

    # ── Month-over-month (P1 → P2) ────────────────────────────
    # P1 facilities: F001-F003, F005-F010                = 9
    # P2 facilities: F001-F005, F007-F010                = 9
    # New originations: P2 - P1 = {F004}                 → 1
    # Exits:            P1 - P2 = {F006}                 → 1
    # PD changes (intersection): F003 C09 → C13          → 1
    # Reg changes (intersection): F003 Pass → SS         → 1
    "month_over_month": {
        "new_originations_count":   1,
        "exits_count":              1,
        "pd_rating_changes_count":  1,
        "reg_rating_changes_count": 1,
        "new_origination_facility": "F004",
        "exit_facility":            "F006",
        "pd_change_facility":       "F003",
        "reg_change_facility":      "F003",
    },
}
