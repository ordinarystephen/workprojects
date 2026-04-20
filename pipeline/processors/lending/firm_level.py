# ── KRONOS · pipeline/processors/lending/firm_level.py ────────
# Firm-level view processor (Lending cube slicer).
#
# Takes a fully computed LendingCube and produces the
# { context, metrics } payload that server.py returns to the
# frontend. The cube is the single source of truth — this file
# only picks WHICH parts of it to send to the LLM and which to
# render as Data Snapshot tiles.
#
# Contract (matches SCRIPT_MAP convention in pipeline/analyze.py):
#   slice(cube) -> { "context": str, "metrics": dict }
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from pipeline.cube.models import LendingCube


def slice_firm_level(cube: LendingCube) -> dict:
    """
    Build the firm-level view: prose context for the LLM plus tile
    data for the Data Snapshot panel.

    Args:
        cube: a fully-computed LendingCube.

    Returns:
        dict with keys "context" (str) and "metrics" (dict).
    """
    current = cube.firm_level.current
    totals  = current.totals
    counts  = current.counts
    wapd    = cube.firm_level.current.wapd
    walgd   = cube.firm_level.current.walgd
    as_of   = cube.metadata.as_of.isoformat()

    # ── Context for the LLM ───────────────────────────────────
    # Prose-style text. Every value the LLM might cite appears here
    # verbatim so the cross-check in pipeline/validate.py can verify it.
    context_lines = [
        f"Firm-level lending portfolio snapshot — as of {as_of}.",
        "",
        "Counts:",
        f"- Distinct ultimate parents: {counts.parents:,}",
        f"- Distinct partners: {counts.partners:,}",
        f"- Distinct facilities: {counts.facilities:,}",
        f"- Distinct risk assessment industries: {counts.industries:,}",
        "",
        "Total exposures (USD):",
        f"- Committed Exposure: ${totals.committed:,.2f}",
        f"- Outstanding Exposure: ${totals.outstanding:,.2f}",
        f"- Take & Hold Exposure: ${totals.take_and_hold:,.2f}",
        f"- Temporary Exposure: ${totals.temporary:,.2f}",
        f"- Approved Limit: ${totals.approved_limit:,.2f}",
        "",
        "Regulatory rating breakdown (USD):",
        f"- Pass: ${totals.pass_rated:,.2f}",
        f"- Special Mention: ${totals.special_mention:,.2f}",
        f"- Substandard: ${totals.substandard:,.2f}",
        f"- Doubtful: ${totals.doubtful:,.2f}",
        f"- Loss: ${totals.loss:,.2f}",
        f"- No Regulatory Rating: ${totals.no_regulatory_rating:,.2f}",
        f"- Criticized & Classified (SM + SS + Dbt + L): ${totals.criticized_classified:,.2f}",
    ]
    if totals.cc_pct_of_commitment is not None:
        context_lines.append(
            f"- C&C as % of commitment: {totals.cc_pct_of_commitment * 100:.2f}%"
        )

    context_lines += [
        "",
        "Weighted averages (committed-exposure weighted):",
        f"- Weighted Average PD: {wapd.display or 'n/a'}"
        + (f" (raw {wapd.raw:.6f})" if wapd.raw is not None else ""),
        f"- Weighted Average LGD: {walgd.display or 'n/a'}"
        + (f" (raw {walgd.raw:.4f})" if walgd.raw is not None else ""),
    ]

    # IG / NIG split, if computable.
    if cube.by_ig_status:
        context_lines += ["", "Investment-grade split (committed exposure):"]
        for label, hist in cube.by_ig_status.items():
            committed = hist.current.totals.committed
            context_lines.append(f"- {label}: ${committed:,.2f}")

    # Horizontal portfolios.
    if cube.by_horizontal:
        context_lines += ["", "Horizontal portfolios (committed exposure):"]
        for name, hist in cube.by_horizontal.items():
            committed = hist.current.totals.committed
            facilities = hist.current.counts.facilities
            context_lines.append(
                f"- {name}: ${committed:,.2f} across {facilities:,} facilities"
            )

    # Watchlist firm-level aggregate.
    if cube.watchlist.facility_count > 0 or cube.watchlist.committed > 0:
        context_lines += [
            "",
            "Watchlist (firm-level aggregate):",
            f"- Watchlist facility count: {cube.watchlist.facility_count:,}",
            f"- Watchlist committed exposure: ${cube.watchlist.committed:,.2f}",
        ]

    # Period coverage.
    if len(cube.metadata.periods) > 1:
        period_strs = ", ".join(p.isoformat() for p in cube.metadata.periods)
        context_lines += [
            "",
            f"File covers {len(cube.metadata.periods)} periods: {period_strs}. "
            f"Headline figures above are for the latest period only.",
        ]

    # Validation flag.
    if not totals.validation_ok:
        context_lines += [
            "",
            f"Note: rated-exposure components do not reconcile to total committed "
            f"(diff ${totals.validation_diff:,.2f}). Source data may have a quality issue.",
        ]

    context = "\n".join(context_lines)

    # ── Metrics for the Data Snapshot tiles ───────────────────
    metrics = {
        f"Firm-Level Overview · As of {as_of}": [
            {"label": "Distinct Parents",      "value": f"{counts.parents:,}",      "sentiment": "neutral"},
            {"label": "Distinct Industries",   "value": f"{counts.industries:,}",   "sentiment": "neutral"},
            {"label": "Distinct Facilities",   "value": f"{counts.facilities:,}",   "sentiment": "neutral"},
            {"label": "Total Commitment",      "value": _money(totals.committed),   "sentiment": "neutral"},
            {"label": "Total Outstanding",     "value": _money(totals.outstanding), "sentiment": "neutral"},
            {"label": "Total Take & Hold",     "value": _money(totals.take_and_hold), "sentiment": "neutral"},
            {"label": "Criticized & Classified",
             "value": _money(totals.criticized_classified),
             "sentiment": _cc_sentiment(totals.cc_pct_of_commitment)},
            {"label": "C&C % of Commitment",
             "value": (f"{totals.cc_pct_of_commitment * 100:.2f}%" if totals.cc_pct_of_commitment is not None else "n/a"),
             "sentiment": _cc_sentiment(totals.cc_pct_of_commitment)},
            {"label": "Weighted Average PD",
             "value": (wapd.display or "n/a"),
             "sentiment": "neutral"},
            {"label": "Weighted Average LGD",
             "value": (walgd.display or "n/a"),
             "sentiment": "neutral"},
        ],
    }

    if cube.by_ig_status:
        metrics["Investment-Grade Split"] = [
            {"label": label,
             "value": _money(hist.current.totals.committed),
             "sentiment": "neutral"}
            for label, hist in cube.by_ig_status.items()
        ]

    if cube.by_horizontal:
        metrics["Horizontal Portfolios"] = [
            {"label": name,
             "value": _money(hist.current.totals.committed),
             "sentiment": "warning" if name == "Global Recovery Management" else "neutral"}
            for name, hist in cube.by_horizontal.items()
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

    return {"context": context, "metrics": metrics}


# ── helpers ───────────────────────────────────────────────────

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
