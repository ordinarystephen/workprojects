# ── KRONOS · pipeline/tests/fixtures/smoke_lending_mom_expected.py ──
# Hand-computed expected values for the lifecycle/MoM smoke fixture.
#
# Every value below is derived from the FACILITIES list in
# _build_smoke_lending_mom.py — NEVER from running the cube. Running
# the cube and copying its output would assert the cube agrees with
# itself (circular). Re-derive every value from the FACILITIES list
# by hand whenever the fixture changes.
#
# ── Per-facility cheat sheet (committed in $M) ────────────────
# F101 CryptoCo      Crypto       NIG  C10/SM    50  P1 only  → industry EXIT
# F102 DefaultCo     Energy       Dft  CDF/Loss  20  P1 only  → Defaulted bucket EXIT
# F103 LF Holdings   Energy       IG   C03/Pass  60  P1 only  → LF horizontal EXIT
# F201 Acme Energy   Energy       IG   C03/Pass 100  both     → active anchor
# F301 AILend        AI Lending   IG   C03/Pass  40  P2 only  → industry NEW
# F302 NonRatedCo    Energy       NR   TBR/-     30  P2 only  → Non-Rated bucket NEW
# F303 GRMCo         Energy       IG   C03/Pass  25  P2 only  → GRM horizontal NEW (Directly Managed)
# F304 BravoNIG      Energy       NIG  C10/SM    50  P2 only  → keeps NIG alive in P2
#
# Period labels: P1 = 2025-01-31, P2 = 2025-02-28. Latest = P2.
# All values below are P2 unless explicitly noted.
#
# ── P1 vs P2 totals (hand arithmetic) ─────────────────────────
# P1 facilities: F101, F102, F103, F201            = 4
# P2 facilities: F201, F301, F302, F303, F304       = 5
#
# P1 committed = 50 + 20 + 60 + 100                = 230 M
# P2 committed = 100 + 40 + 30 + 25 + 50           = 245 M
#
# ── P2 industry section reconciliation (the central bug-fix test) ──
# Energy P2     = F201 + F302 + F303 + F304 = 100+30+25+50 = 205 M
# AI Lending P2 = F301                                       =  40 M
# Crypto P2     = (none — F101 was P1 only)                 =   0 M  ← MUST be 0
# Section sum   = 205 + 40 + 0                              = 245 M  ← MUST equal firm
#
# Pre-fix bug: Crypto.current would have been pinned to history[-1]
# which was P1's $50M, inflating section_sum to $295M while firm = $245M.
# Post-fix: Crypto.current is pinned to P2 with empty totals → $0.
#
# ── P2 rating-category coverage (pre-fix would have looked similar
# but Defaulted would have shown $20M from P1 inflating the sum) ──
# IG P2         = F201 + F301 + F303     = 100+40+25         = 165 M
# NIG P2        = F304                                          =  50 M
# Defaulted P2  = (none — F102 was P1 only)                    =   0 M  ← MUST be 0
# Non-Rated P2  = F302                                          =  30 M
# Coverage sum  = 165 + 50 + 0 + 30                            = 245 M  ← reconciles to firm
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from datetime import date


# Per-facility raw committed (in $).
COMMITTED = {
    "F101": 50_000_000, "F102": 20_000_000, "F103": 60_000_000,
    "F201": 100_000_000,
    "F301": 40_000_000, "F302": 30_000_000, "F303": 25_000_000, "F304": 50_000_000,
}


EXPECTED = {
    # ── Periods ───────────────────────────────────────────────
    "periods":        [date(2025, 1, 31), date(2025, 2, 28)],
    "current_period": date(2025, 2, 28),
    "prior_period":   date(2025, 1, 31),

    # ── Firm-level totals (latest period = P2) ────────────────
    "firm_committed_p2": 245_000_000.0,
    "firm_committed_p1": 230_000_000.0,
    "firm_facilities_p2": 5,
    "firm_facilities_p1": 4,

    # ── Industry reconciliation (the central bug-fix test) ────
    "by_industry_committed_p2": {
        "Energy":     205_000_000.0,
        "AI Lending":  40_000_000.0,
        "Crypto":             0.0,   # exited — current pinned to empty P2 block
    },
    "industry_section_sum_p2":   245_000_000.0,  # equals firm exactly

    # ── Industry lifecycle status ─────────────────────────────
    "exited_industries":  ["Crypto"],
    "new_industries":     ["AI Lending"],
    "active_industries":  ["Energy"],

    # ── Horizontal lifecycle status ───────────────────────────
    # by_horizontal contains entries for any horizontal that had ≥1
    # row anywhere in the upload — so both LF (P1 only via F103) and
    # GRM (P2 only via F303) appear in the dict.
    "by_horizontal_committed_p2": {
        "Leveraged Finance":            0.0,   # exited
        "Global Recovery Management":  25_000_000.0,  # new
    },
    "exited_horizontals": ["Leveraged Finance"],
    "new_horizontals":    ["Global Recovery Management"],

    # ── Rating-category lifecycle status ──────────────────────
    "by_ig_status_committed_p2": {
        "Investment Grade":     165_000_000.0,
        "Non-Investment Grade":  50_000_000.0,
    },
    "by_defaulted_committed_p2": {
        "Defaulted": 0.0,   # exited — F102 was P1 only
    },
    "by_non_rated_committed_p2": {
        "Non-Rated": 30_000_000.0,   # new — F302 only in P2
    },
    "exited_rating_buckets": ["Defaulted"],
    "new_rating_buckets":    ["Non-Rated"],
    "active_rating_buckets": ["Investment Grade", "Non-Investment Grade"],

    # ── Rating coverage invariant (P2) ────────────────────────
    # IG (165) + NIG (50) + Defaulted (0) + Non-Rated (30) = 245 = firm
    "rating_coverage_committed_p2": 245_000_000.0,

    # ── Distressed sub-stat (no C13 facilities anywhere) ──────
    "nig_distressed_substats_p2": None,

    # ── Month-over-period ─────────────────────────────────────
    # P1 facilities: F101, F102, F103, F201
    # P2 facilities: F201, F301, F302, F303, F304
    # New (in P2 not P1): F301, F302, F303, F304     = 4
    # Exits (in P1 not P2): F101, F102, F103         = 3
    # PD changes (intersection): only F201 (no change)  = 0
    # Reg changes (intersection): only F201 (no change) = 0
    "month_over_month": {
        "new_originations_count":   4,
        "exits_count":              3,
        "pd_rating_changes_count":  0,
        "reg_rating_changes_count": 0,
        "new_origination_facilities": {"F301", "F302", "F303", "F304"},
        "exit_facilities":            {"F101", "F102", "F103"},
    },
}
