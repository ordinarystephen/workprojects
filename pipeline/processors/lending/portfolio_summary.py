# ── KRONOS · pipeline/processors/lending/portfolio_summary.py ─
# Portfolio Summary view processor (Lending cube slicer).
#
# Executive-level health overview. Differs from firm_level by
# emphasizing trend, concentration, and health signals over the
# exhaustive snapshot of every rating bucket.
#
# Slices used:
#   - firm_level.current.totals  → headline scale + C&C health
#   - by_ig_status               → IG / NIG share of rated commitment
#   - by_defaulted / by_non_rated → top-level peers to IG/NIG
#   - nig_distressed_substats    → C13 subset within NIG (sub-line)
#   - by_industry                → top-5 industry concentrations
#   - top_contributors           → top-5 parents by committed
#   - watchlist                  → firm-level watchlist signal
#   - month_over_month           → originations, exits, downgrades
#                                  (only when ≥ 2 periods uploaded)
#
# Contract (matches MODE_MAP convention in pipeline/analyze.py):
#   slice(cube) -> {
#     "context": str,               # prose the LLM sees
#     "metrics": dict,              # Data Snapshot tiles
#     "verifiable_values": dict,    # label → { value, type } for the verifier
#   }
#
# verifiable_values keys are the English labels that appear in the
# context; pipeline/validate.verify_claims resolves claim.source_field
# against these. Labels that would collide across sections (e.g. two
# "committed" figures) are disambiguated with a suffix like
# "(industry)" / "(parent)".
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from pipeline.cube.models import LendingCube
from pipeline.registry import register_slicer


_TOP_N = 5


@register_slicer("portfolio_summary")
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

    # ── Rating-category composition ───────────────────────────
    # IG / NIG are reported as shares of "rated commitment" (IG + NIG
    # only — legacy semantic preserved now that Defaulted sits outside
    # NIG). Defaulted and Non-Rated are top-level peers, each shown as
    # a share of total commitment. Distressed is a sub-line under NIG
    # using NIG as its natural denominator.
    rated_total = sum(
        h.current.totals.committed for h in cube.by_ig_status.values()
    )
    nig_committed = (
        cube.by_ig_status["Non-Investment Grade"].current.totals.committed
        if "Non-Investment Grade" in cube.by_ig_status else 0.0
    )
    if cube.by_ig_status or cube.by_defaulted or cube.by_non_rated:
        context_lines += ["", "Rating-category composition (committed exposure):"]
        for label in ("Investment Grade", "Non-Investment Grade"):
            if label not in cube.by_ig_status:
                continue
            committed = cube.by_ig_status[label].current.totals.committed
            share = (committed / rated_total * 100) if rated_total > 0 else 0.0
            context_lines.append(
                f"- {label}: ${committed:,.2f} ({share:.2f}% of rated commitment)"
            )
            if label == "Non-Investment Grade" and cube.nig_distressed_substats is not None:
                ds = cube.nig_distressed_substats
                ds_share = (ds.committed / nig_committed * 100) if nig_committed > 0 else 0.0
                context_lines.append(
                    f"    Of which, Distressed (C13): ${ds.committed:,.2f} "
                    f"({ds_share:.2f}% of NIG), {ds.facility_count:,} facilities"
                )
        for label, hist in cube.by_defaulted.items():
            committed = hist.current.totals.committed
            share = (committed / totals.committed * 100) if totals.committed > 0 else 0.0
            context_lines.append(
                f"- {label}: ${committed:,.2f} ({share:.2f}% of total commitment)"
            )
        for label, hist in cube.by_non_rated.items():
            committed = hist.current.totals.committed
            share = (committed / totals.committed * 100) if totals.committed > 0 else 0.0
            context_lines.append(
                f"- {label}: ${committed:,.2f} ({share:.2f}% of total commitment)"
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

    # ── Top facility-level WAPD drivers ───────────────────────
    if cube.top_wapd_facility_contributors:
        context_lines += [
            "",
            f"Top {len(cube.top_wapd_facility_contributors)} facility-level "
            f"contributors to Weighted Average PD (numerator = PD × Committed):",
        ]
        for f in cube.top_wapd_facility_contributors:
            label = f.facility_name or f.facility_id
            parent = f" — parent {f.parent_name}" if f.parent_name else ""
            share = (
                f"{f.share_of_numerator * 100:.2f}% of firm WAPD numerator"
                if f.share_of_numerator is not None else "n/a"
            )
            implied = (
                f"implied PD {f.implied_pd * 100:.2f}%"
                if f.implied_pd is not None else "implied PD n/a"
            )
            rating = f"PD rating {f.pd_rating}" if f.pd_rating else "PD rating n/a"
            context_lines.append(
                f"- {label}{parent}: ${f.committed:,.2f} committed, "
                f"numerator {f.wapd_numerator:,.2f} ({share}), {implied}, {rating}"
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

    if cube.by_ig_status or cube.by_defaulted or cube.by_non_rated:
        rating_tiles: list[dict] = []
        for label in ("Investment Grade", "Non-Investment Grade"):
            if label not in cube.by_ig_status:
                continue
            committed = cube.by_ig_status[label].current.totals.committed
            share_pct = (committed / rated_total * 100) if rated_total > 0 else None
            rating_tiles.append({
                "label": label,
                "value": (f"{share_pct:.1f}%" if share_pct is not None else "n/a"),
                "sentiment": "neutral",
            })
            if label == "Non-Investment Grade" and cube.nig_distressed_substats is not None:
                rating_tiles.append({
                    "label": "  of which Distressed",
                    "value": _money(cube.nig_distressed_substats.committed),
                    "sentiment": "warning",
                })
        for label, hist in cube.by_defaulted.items():
            rating_tiles.append({
                "label": label,
                "value": _money(hist.current.totals.committed),
                "sentiment": "negative",
            })
        for label, hist in cube.by_non_rated.items():
            rating_tiles.append({
                "label": label,
                "value": _money(hist.current.totals.committed),
                "sentiment": "neutral",
            })
        if rating_tiles:
            metrics["Rating Category Composition"] = rating_tiles

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

    top_wapd_for_tiles = cube.top_wapd_facility_contributors[:5]
    if top_wapd_for_tiles:
        metrics[f"Top {len(top_wapd_for_tiles)} WAPD Drivers (Facility)"] = [
            {"label": (f.facility_name or f.facility_id),
             "value": (f"{f.share_of_numerator * 100:.1f}% · {_money(f.committed)}"
                       if f.share_of_numerator is not None
                       else _money(f.committed)),
             "sentiment": "warning"}
            for f in top_wapd_for_tiles
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

    # ── verifiable_values ─────────────────────────────────────
    # Labels match the English labels the LLM sees in the context. The
    # verifier (pipeline/validate.py) resolves claim.source_field here.
    verifiable_values: dict = {
        "Total Committed Exposure":    {"value": totals.committed,   "type": "currency"},
        "Total Outstanding Exposure":  {"value": totals.outstanding, "type": "currency"},
        "Distinct ultimate parents":   {"value": counts.parents,     "type": "count"},
        "Distinct facilities":         {"value": counts.facilities,  "type": "count"},
        "Distinct industries":         {"value": counts.industries,  "type": "count"},
        "Criticized & Classified exposure (SM + SS + Dbt + L)": {
            "value": totals.criticized_classified, "type": "currency"
        },
        "as of": {"value": as_of, "type": "date"},
    }
    if totals.cc_pct_of_commitment is not None:
        verifiable_values["C&C as % of commitment"] = {
            "value": totals.cc_pct_of_commitment, "type": "percentage",
        }
    if current.wapd.display:
        verifiable_values["Weighted Average PD"] = {
            "value": current.wapd.display, "type": "string",
        }
    if current.walgd.display:
        verifiable_values["Weighted Average LGD"] = {
            "value": current.walgd.display, "type": "string",
        }

    # Rating-category buckets — publish committed figures and the
    # relevant share for each. IG/NIG share is against "rated
    # commitment" (IG + NIG). Defaulted and Non-Rated shares are
    # against total commitment. Distressed is exposed as both a
    # committed figure and a share of NIG.
    for label, hist in cube.by_ig_status.items():
        committed = hist.current.totals.committed
        verifiable_values[label] = {"value": committed, "type": "currency"}
        if rated_total > 0:
            verifiable_values[f"{label} (% of rated commitment)"] = {
                "value": committed / rated_total, "type": "percentage",
            }
    for label, hist in cube.by_defaulted.items():
        committed = hist.current.totals.committed
        verifiable_values[label] = {"value": committed, "type": "currency"}
        if totals.committed > 0:
            verifiable_values[f"{label} (% of total commitment)"] = {
                "value": committed / totals.committed, "type": "percentage",
            }
    for label, hist in cube.by_non_rated.items():
        committed = hist.current.totals.committed
        verifiable_values[label] = {"value": committed, "type": "currency"}
        if totals.committed > 0:
            verifiable_values[f"{label} (% of total commitment)"] = {
                "value": committed / totals.committed, "type": "percentage",
            }
    if cube.nig_distressed_substats is not None:
        ds = cube.nig_distressed_substats
        verifiable_values["Distressed (of which)"] = {
            "value": ds.committed, "type": "currency",
        }
        verifiable_values["Distressed facility count"] = {
            "value": ds.facility_count, "type": "count",
        }
        if nig_committed > 0:
            verifiable_values["Distressed (% of NIG)"] = {
                "value": ds.committed / nig_committed, "type": "percentage",
            }

    # Top industries — name → committed dollars.
    for name, committed in top_industries:
        verifiable_values[name] = {"value": committed, "type": "currency"}
        if totals.committed > 0:
            verifiable_values[f"{name} (% of total commitment)"] = {
                "value": committed / totals.committed, "type": "percentage",
            }

    # Top parents.
    for c in top_parents:
        label = c.entity_name or c.entity_id
        verifiable_values[label] = {"value": c.committed, "type": "currency"}
        if totals.committed > 0:
            verifiable_values[f"{label} (% of total commitment)"] = {
                "value": c.committed / totals.committed, "type": "percentage",
            }

    # Facility-level WAPD drivers.
    for f in cube.top_wapd_facility_contributors:
        label = f.facility_name or f.facility_id
        verifiable_values[f"{label} (committed)"] = {
            "value": f.committed, "type": "currency",
        }
        verifiable_values[f"{label} (WAPD numerator)"] = {
            "value": f.wapd_numerator, "type": "currency",
        }
        if f.share_of_numerator is not None:
            verifiable_values[f"{label} (share of firm WAPD numerator)"] = {
                "value": f.share_of_numerator, "type": "percentage",
            }
        if f.implied_pd is not None:
            verifiable_values[f"{label} (implied PD)"] = {
                "value": f.implied_pd, "type": "percentage",
            }

    # Watchlist aggregate.
    if cube.watchlist.facility_count > 0 or cube.watchlist.committed > 0:
        verifiable_values["Watchlist facility count"] = {
            "value": cube.watchlist.facility_count, "type": "count",
        }
        verifiable_values["Watchlist committed exposure"] = {
            "value": cube.watchlist.committed, "type": "currency",
        }

    # Period-over-period.
    if cube.month_over_month is not None:
        mom = cube.month_over_month
        verifiable_values.update({
            "New originations":              {"value": len(mom.new_originations), "type": "count"},
            "Exits":                         {"value": len(mom.exits),            "type": "count"},
            "New parent relationships":      {"value": len(mom.parent_entrants),  "type": "count"},
            "Parent relationships exited":   {"value": len(mom.parent_exits),     "type": "count"},
            "PD rating downgrades": {
                "value": sum(1 for r in mom.pd_rating_changes if r.direction == "downgrade"),
                "type":  "count",
            },
            "PD rating upgrades": {
                "value": sum(1 for r in mom.pd_rating_changes if r.direction == "upgrade"),
                "type":  "count",
            },
            "Regulatory rating downgrades": {
                "value": sum(1 for r in mom.reg_rating_changes if r.direction == "downgrade"),
                "type":  "count",
            },
            "Regulatory rating upgrades": {
                "value": sum(1 for r in mom.reg_rating_changes if r.direction == "upgrade"),
                "type":  "count",
            },
        })

    return {
        "context": context,
        "metrics": metrics,
        "verifiable_values": verifiable_values,
    }


# ── helpers ───────────────────────────────────────────────────

def _top_groupings(
    groupings: dict[str, "GroupingHistory"], n: int
) -> list[tuple[str, float]]:
    """Return the top-n (name, committed) pairs from a by_X grouping dict."""
    pairs = [
        (name, hist.current.totals.committed)
        for name, hist in groupings.items()
    ]
    # Tiebreaker on grouping name (ascending) so re-runs produce identical
    # ordering when two groupings tie on committed exposure.
    pairs.sort(key=lambda kv: (-kv[1], kv[0]))
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
