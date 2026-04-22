# ── KRONOS · pipeline/processors/lending/portfolio_summary.py ─
# Portfolio Summary view processor (Lending cube slicer).
#
# Executive-level health overview. Differs from firm_level by
# emphasizing trend, concentration, and health signals over the
# exhaustive snapshot of every rating bucket.
#
# Slices used:
#   - firm_level.current.totals  → headline scale + C&C health
#   - by_ig_status               → IG / NIG share of commitment
#   - by_industry                → top-5 industry concentrations
#   - top_contributors           → top-5 parents by committed
#   - watchlist                  → firm-level watchlist signal
#   - month_over_month           → originations, exits, downgrades
#                                  (only when ≥ 2 periods uploaded)
#
# Contract (matches MODE_MAP convention in pipeline/analyze.py):
#   slice(cube) -> { "context": str, "metrics": dict }
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from pipeline.cube.models import LendingCube


_TOP_N = 5


def slice_portfolio_summary(cube: LendingCube) -> dict:
    """
    Build the portfolio-summary view: an executive health overview.

    Args:
        cube: a fully-computed LendingCube.

    Returns:
        dict with keys "context" (str) and "metrics" (dict).
    """
    current = cube.firm_level.current
    totals  = current.totals
    counts  = current.counts
    as_of   = cube.metadata.as_of.isoformat()

    # ── Headline figures the LLM will cite ────────────────────
    context_lines = [
        f"Portfolio summary — as of {as_of}.",
        "",
        "Scale:",
        f"- Total Committed Exposure: ${totals.committed:,.2f}",
        f"- Total Outstanding Exposure: ${totals.outstanding:,.2f}",
        f"- Distinct ultimate parents: {counts.parents:,}",
        f"- Distinct facilities: {counts.facilities:,}",
        f"- Distinct industries: {counts.industries:,}",
        "",
        "Credit quality (firm-level):",
        f"- Criticized & Classified exposure (SM + SS + Dbt + L): ${totals.criticized_classified:,.2f}",
    ]
    if totals.cc_pct_of_commitment is not None:
        context_lines.append(
            f"- C&C as % of commitment: {totals.cc_pct_of_commitment * 100:.2f}%"
        )
    if current.wapd.display:
        context_lines.append(f"- Weighted Average PD: {current.wapd.display}")
    if current.walgd.display:
        context_lines.append(f"- Weighted Average LGD: {current.walgd.display}")

    # ── IG / NIG mix ──────────────────────────────────────────
    if cube.by_ig_status:
        context_lines += ["", "Investment-grade mix (committed exposure):"]
        ig_total = sum(
            h.current.totals.committed for h in cube.by_ig_status.values()
        )
        for label, hist in cube.by_ig_status.items():
            committed = hist.current.totals.committed
            share = (committed / ig_total * 100) if ig_total > 0 else 0.0
            context_lines.append(
                f"- {label}: ${committed:,.2f} ({share:.2f}% of rated commitment)"
            )

    # ── Top industry concentrations ───────────────────────────
    top_industries = _top_groupings(cube.by_industry, _TOP_N)
    if top_industries:
        context_lines += [
            "",
            f"Top {len(top_industries)} industries by committed exposure:",
        ]
        for name, committed in top_industries:
            share = (
                (committed / totals.committed * 100)
                if totals.committed > 0
                else 0.0
            )
            context_lines.append(
                f"- {name}: ${committed:,.2f} ({share:.2f}% of total commitment)"
            )

    # ── Top parent contributors ───────────────────────────────
    top_parents = cube.top_contributors.by_committed[:_TOP_N]
    if top_parents:
        context_lines += [
            "",
            f"Top {len(top_parents)} parents by committed exposure:",
        ]
        for c in top_parents:
            label = c.entity_name or c.entity_id
            share = (
                (c.committed / totals.committed * 100)
                if totals.committed > 0
                else 0.0
            )
            context_lines.append(
                f"- {label}: ${c.committed:,.2f} ({share:.2f}% of total commitment)"
            )

    # ── Watchlist signal ──────────────────────────────────────
    if cube.watchlist.facility_count > 0 or cube.watchlist.committed > 0:
        context_lines += [
            "",
            "Watchlist:",
            f"- Watchlist facility count: {cube.watchlist.facility_count:,}",
            f"- Watchlist committed exposure: ${cube.watchlist.committed:,.2f}",
        ]

    # ── Period-over-period movement (only when ≥ 2 periods) ───
    if cube.month_over_month is not None:
        mom = cube.month_over_month
        downgrades_pd  = sum(1 for r in mom.pd_rating_changes  if r.direction == "downgrade")
        upgrades_pd    = sum(1 for r in mom.pd_rating_changes  if r.direction == "upgrade")
        downgrades_reg = sum(1 for r in mom.reg_rating_changes if r.direction == "downgrade")
        upgrades_reg   = sum(1 for r in mom.reg_rating_changes if r.direction == "upgrade")
        context_lines += [
            "",
            f"Period-over-period ({mom.prior_period.isoformat()} → {mom.current_period.isoformat()}):",
            f"- New originations: {len(mom.new_originations):,} facilities",
            f"- Exits: {len(mom.exits):,} facilities",
            f"- New parent relationships: {len(mom.parent_entrants):,}",
            f"- Parent relationships exited: {len(mom.parent_exits):,}",
            f"- PD rating downgrades: {downgrades_pd:,}",
            f"- PD rating upgrades: {upgrades_pd:,}",
            f"- Regulatory rating downgrades: {downgrades_reg:,}",
            f"- Regulatory rating upgrades: {upgrades_reg:,}",
        ]
    elif len(cube.metadata.periods) > 1:
        context_lines += [
            "",
            "Period-over-period changes were not computed for this upload.",
        ]

    # ── Validation footnote ───────────────────────────────────
    if not totals.validation_ok:
        context_lines += [
            "",
            f"Note: rated-exposure components do not reconcile to total committed "
            f"(diff ${totals.validation_diff:,.2f}). Source data may have a quality issue.",
        ]

    context = "\n".join(context_lines)

    # ── Metrics for the Data Snapshot tiles ───────────────────
    metrics: dict = {
        f"Headline · As of {as_of}": [
            {"label": "Total Commitment",    "value": _money(totals.committed),     "sentiment": "neutral"},
            {"label": "Total Outstanding",   "value": _money(totals.outstanding),   "sentiment": "neutral"},
            {"label": "Parents",             "value": f"{counts.parents:,}",        "sentiment": "neutral"},
            {"label": "Facilities",          "value": f"{counts.facilities:,}",     "sentiment": "neutral"},
            {"label": "Industries",          "value": f"{counts.industries:,}",     "sentiment": "neutral"},
            {"label": "C&C Exposure",
             "value": _money(totals.criticized_classified),
             "sentiment": _cc_sentiment(totals.cc_pct_of_commitment)},
            {"label": "C&C % of Commitment",
             "value": (f"{totals.cc_pct_of_commitment * 100:.2f}%"
                       if totals.cc_pct_of_commitment is not None else "n/a"),
             "sentiment": _cc_sentiment(totals.cc_pct_of_commitment)},
            {"label": "Weighted Average PD",
             "value": (current.wapd.display or "n/a"),
             "sentiment": "neutral"},
        ],
    }

    if cube.by_ig_status:
        ig_total = sum(h.current.totals.committed for h in cube.by_ig_status.values())
        metrics["Investment-Grade Mix"] = [
            {"label": label,
             "value": (f"{(hist.current.totals.committed / ig_total * 100):.1f}%"
                       if ig_total > 0 else "n/a"),
             "sentiment": "neutral"}
            for label, hist in cube.by_ig_status.items()
        ]

    if top_industries:
        metrics[f"Top {len(top_industries)} Industries by Commitment"] = [
            {"label": name, "value": _money(committed), "sentiment": "neutral"}
            for name, committed in top_industries
        ]

    if top_parents:
        metrics[f"Top {len(top_parents)} Parents by Commitment"] = [
            {"label": (c.entity_name or c.entity_id),
             "value": _money(c.committed),
             "sentiment": "neutral"}
            for c in top_parents
        ]

    if cube.watchlist.facility_count > 0:
        metrics["Watchlist"] = [
            {"label": "Facility Count",
             "value": f"{cube.watchlist.facility_count:,}",
             "sentiment": "warning"},
            {"label": "Committed Exposure",
             "value": _money(cube.watchlist.committed),
             "sentiment": "warning"},
        ]

    if cube.month_over_month is not None:
        mom = cube.month_over_month
        downgrades_reg = sum(1 for r in mom.reg_rating_changes if r.direction == "downgrade")
        metrics[f"Period Movement · {mom.prior_period.isoformat()} → {mom.current_period.isoformat()}"] = [
            {"label": "New Originations", "value": f"{len(mom.new_originations):,}", "sentiment": "neutral"},
            {"label": "Exits",            "value": f"{len(mom.exits):,}",            "sentiment": "neutral"},
            {"label": "Reg Rating Downgrades",
             "value": f"{downgrades_reg:,}",
             "sentiment": "warning" if downgrades_reg > 0 else "neutral"},
        ]

    return {"context": context, "metrics": metrics}


# ── helpers ───────────────────────────────────────────────────

def _top_groupings(
    groupings: dict[str, "GroupingHistory"], n: int
) -> list[tuple[str, float]]:
    """Return the top-n (name, committed) pairs from a by_X grouping dict."""
    pairs = [
        (name, hist.current.totals.committed)
        for name, hist in groupings.items()
    ]
    pairs.sort(key=lambda kv: kv[1], reverse=True)
    return pairs[:n]


def _money(amount: float) -> str:
    """Format dollars as $X.XM for tile display (millions, 1 decimal)."""
    return f"${amount / 1_000_000:,.1f}M"


def _cc_sentiment(cc_pct):
    """Heuristic colorization for C&C tiles. None → neutral."""
    if cc_pct is None:
        return "neutral"
    if cc_pct > 0.05:
        return "warning"
    return "neutral"
