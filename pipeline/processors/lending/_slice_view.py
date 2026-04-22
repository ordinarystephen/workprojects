# ── KRONOS · pipeline/processors/lending/_slice_view.py ───────
# Shared renderer for PortfolioSlice — used by both
# industry_portfolio_level and horizontal_portfolio_level slicers.
#
# Industry portfolios partition the book by Risk Assessment Industry
# (every facility lands in exactly one); horizontal portfolios are
# boolean overlays (a facility can be in zero, one, or several).
# The rendered shape is identical — same KRIs, same rating buckets,
# same contributor lists — what differs is the framing prompt and
# the parameter source. So this renderer factors out the deterministic
# rendering; the slicers themselves stay thin.
#
# `kind` controls the label prefix on every section heading and on
# every verifiable-value key, which (a) keeps the LLM's narrative
# scoped ("Industry Portfolio: Energy — Top Parents") and (b) avoids
# verifiable-value collisions with the firm-level slicer.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from pipeline.cube.models import PortfolioSlice


_TOP_N = 5


def render_slice(
    *,
    slice_: PortfolioSlice,
    kind: str,                 # "Industry Portfolio" or "Horizontal Portfolio"
    firm_committed: float,     # firm-level commitment for share-of-firm calcs
    as_of: str,                # iso date string
) -> dict:
    """Render a PortfolioSlice as {context, metrics, verifiable_values}.

    Args:
        slice_:         the cube's per-slice composite (industry_details[X]
                        or horizontal_details[X]).
        kind:           label prefix used in section headings AND in
                        verifiable_values keys. Two values supported:
                        "Industry Portfolio" / "Horizontal Portfolio".
        firm_committed: firm-level total committed exposure — used to
                        compute "share of firm commitment" tiles. None
                        of the slice's own math depends on this.
        as_of:          latest period as ISO date (passed through from
                        the cube's metadata).

    Returns the standard slicer dict: context (str), metrics (dict
    keyed by section heading), verifiable_values (dict keyed by
    label).
    """
    name      = slice_.name
    grouping  = slice_.grouping
    current   = grouping.current
    totals    = current.totals
    counts    = current.counts
    rating    = slice_.rating_composition
    watchlist = slice_.watchlist

    # Disambiguating prefix: every label that could collide with the
    # firm-level slicer's labels gets it. The verifier resolves
    # claim.source_field by exact match — collisions silently take
    # the wrong value, so the prefix is load-bearing.
    prefix = f"{kind}: {name}"

    share_of_firm = (
        (totals.committed / firm_committed * 100) if firm_committed > 0 else None
    )

    # ── Context (LLM input) ───────────────────────────────────
    lines = [
        f"{kind} — {name} as of {as_of}.",
        "",
        f"{prefix} — Scale:",
        f"- Committed Exposure: ${totals.committed:,.2f}",
        f"- Outstanding Exposure: ${totals.outstanding:,.2f}",
        f"- Distinct ultimate parents: {counts.parents:,}",
        f"- Distinct facilities: {counts.facilities:,}",
    ]
    if share_of_firm is not None:
        lines.append(f"- Share of firm committed exposure: {share_of_firm:.2f}%")

    lines += [
        "",
        f"{prefix} — Credit quality:",
        f"- Criticized & Classified exposure (SM + SS + Dbt + L): "
        f"${totals.criticized_classified:,.2f}",
    ]
    if totals.cc_pct_of_commitment is not None:
        lines.append(
            f"- C&C as % of commitment: {totals.cc_pct_of_commitment * 100:.2f}%"
        )
    if current.wapd.display:
        lines.append(f"- Weighted Average PD: {current.wapd.display}")
    if current.walgd.display:
        lines.append(f"- Weighted Average LGD: {current.walgd.display}")

    # ── Rating-category composition (Part 1 structure) ────────
    # IG/NIG share denominator is "rated commitment" (IG + NIG only),
    # matching the firm-level convention. Defaulted and Non-Rated are
    # shares of slice committed (the natural denominator at this
    # scope). Distressed is a sub-line under NIG with NIG as its
    # denominator.
    rated_total = 0.0
    if rating.investment_grade is not None:
        rated_total += rating.investment_grade.current.totals.committed
    nig_committed = (
        rating.non_investment_grade.current.totals.committed
        if rating.non_investment_grade is not None else 0.0
    )
    rated_total += nig_committed

    if any([
        rating.investment_grade,
        rating.non_investment_grade,
        rating.defaulted,
        rating.non_rated,
    ]):
        lines += ["", f"{prefix} — Rating-category composition (committed exposure):"]

        if rating.investment_grade is not None:
            ig_committed = rating.investment_grade.current.totals.committed
            ig_share = (ig_committed / rated_total * 100) if rated_total > 0 else 0.0
            lines.append(
                f"- Investment Grade: ${ig_committed:,.2f} "
                f"({ig_share:.2f}% of rated commitment)"
            )

        if rating.non_investment_grade is not None:
            nig_share = (nig_committed / rated_total * 100) if rated_total > 0 else 0.0
            lines.append(
                f"- Non-Investment Grade: ${nig_committed:,.2f} "
                f"({nig_share:.2f}% of rated commitment)"
            )
            if rating.distressed_substats is not None:
                ds = rating.distressed_substats
                ds_share = (ds.committed / nig_committed * 100) if nig_committed > 0 else 0.0
                lines.append(
                    f"    Of which, Distressed (C13): ${ds.committed:,.2f} "
                    f"({ds_share:.2f}% of NIG), {ds.facility_count:,} facilities"
                )

        if rating.defaulted is not None:
            d_committed = rating.defaulted.current.totals.committed
            d_share = (d_committed / totals.committed * 100) if totals.committed > 0 else 0.0
            lines.append(
                f"- Defaulted: ${d_committed:,.2f} "
                f"({d_share:.2f}% of slice commitment)"
            )

        if rating.non_rated is not None:
            nr_committed = rating.non_rated.current.totals.committed
            nr_share = (nr_committed / totals.committed * 100) if totals.committed > 0 else 0.0
            lines.append(
                f"- Non-Rated: ${nr_committed:,.2f} "
                f"({nr_share:.2f}% of slice commitment)"
            )

    # ── Top parent contributors within slice ──────────────────
    top_parents = slice_.top_contributors.by_committed[:_TOP_N]
    if top_parents:
        lines += [
            "",
            f"{prefix} — Top {len(top_parents)} parents by committed exposure:",
        ]
        for c in top_parents:
            label = c.entity_name or c.entity_id
            share = (
                (c.committed / totals.committed * 100) if totals.committed > 0 else 0.0
            )
            lines.append(
                f"- {label}: ${c.committed:,.2f} ({share:.2f}% of slice commitment)"
            )

    # ── Top facility-level WAPD drivers within slice ──────────
    top_wapd = slice_.top_wapd_facilities[:_TOP_N]
    if top_wapd:
        lines += [
            "",
            f"{prefix} — Top {len(top_wapd)} facility-level contributors to WAPD "
            f"(numerator = PD × Committed; share is within the slice):",
        ]
        for f in top_wapd:
            label  = f.facility_name or f.facility_id
            parent = f" — parent {f.parent_name}" if f.parent_name else ""
            share  = (
                f"{f.share_of_numerator * 100:.2f}% of slice WAPD numerator"
                if f.share_of_numerator is not None else "n/a"
            )
            implied = (
                f"implied PD {f.implied_pd * 100:.2f}%"
                if f.implied_pd is not None else "implied PD n/a"
            )
            rating_str = f"PD rating {f.pd_rating}" if f.pd_rating else "PD rating n/a"
            lines.append(
                f"- {label}{parent}: ${f.committed:,.2f} committed, "
                f"numerator {f.wapd_numerator:,.2f} ({share}), {implied}, {rating_str}"
            )

    # ── Watchlist within slice ────────────────────────────────
    if watchlist.facility_count > 0 or watchlist.committed > 0:
        lines += [
            "",
            f"{prefix} — Watchlist:",
            f"- Watchlist facility count: {watchlist.facility_count:,}",
            f"- Watchlist committed exposure: ${watchlist.committed:,.2f}",
        ]

    if not totals.validation_ok:
        lines += [
            "",
            f"Note: rated-exposure components within {name} do not reconcile to "
            f"slice committed (diff ${totals.validation_diff:,.2f}). Source data "
            f"may have a quality issue.",
        ]

    context = "\n".join(lines)

    # ── Metrics for the Data Snapshot tiles ───────────────────
    metrics: dict = {
        f"{prefix} · As of {as_of}": [
            {"label": "Committed",         "value": _money(totals.committed),    "sentiment": "neutral"},
            {"label": "Outstanding",       "value": _money(totals.outstanding),  "sentiment": "neutral"},
            {"label": "Parents",           "value": f"{counts.parents:,}",       "sentiment": "neutral"},
            {"label": "Facilities",        "value": f"{counts.facilities:,}",    "sentiment": "neutral"},
            {"label": "Share of Firm",
             "value": (f"{share_of_firm:.1f}%" if share_of_firm is not None else "n/a"),
             "sentiment": "neutral"},
            {"label": "C&C Exposure",
             "value": _money(totals.criticized_classified),
             "sentiment": _cc_sentiment(totals.cc_pct_of_commitment)},
            {"label": "C&C %",
             "value": (f"{totals.cc_pct_of_commitment * 100:.2f}%"
                       if totals.cc_pct_of_commitment is not None else "n/a"),
             "sentiment": _cc_sentiment(totals.cc_pct_of_commitment)},
            {"label": "Weighted Average PD",
             "value": (current.wapd.display or "n/a"),
             "sentiment": "neutral"},
        ],
    }

    rating_tiles: list[dict] = []
    if rating.investment_grade is not None:
        rating_tiles.append({
            "label": "Investment Grade",
            "value": _money(rating.investment_grade.current.totals.committed),
            "sentiment": "neutral",
        })
    if rating.non_investment_grade is not None:
        rating_tiles.append({
            "label": "Non-Investment Grade",
            "value": _money(rating.non_investment_grade.current.totals.committed),
            "sentiment": "neutral",
        })
        if rating.distressed_substats is not None:
            rating_tiles.append({
                "label": "  of which Distressed",
                "value": _money(rating.distressed_substats.committed),
                "sentiment": "warning",
            })
    if rating.defaulted is not None:
        rating_tiles.append({
            "label": "Defaulted",
            "value": _money(rating.defaulted.current.totals.committed),
            "sentiment": "negative",
        })
    if rating.non_rated is not None:
        rating_tiles.append({
            "label": "Non-Rated",
            "value": _money(rating.non_rated.current.totals.committed),
            "sentiment": "neutral",
        })
    if rating_tiles:
        metrics[f"{prefix} · Rating Composition"] = rating_tiles

    if top_parents:
        metrics[f"{prefix} · Top {len(top_parents)} Parents"] = [
            {"label": (c.entity_name or c.entity_id),
             "value": _money(c.committed),
             "sentiment": "neutral"}
            for c in top_parents
        ]

    if top_wapd:
        metrics[f"{prefix} · Top {len(top_wapd)} WAPD Drivers"] = [
            {"label": (f.facility_name or f.facility_id),
             "value": (f"{f.share_of_numerator * 100:.1f}% · {_money(f.committed)}"
                       if f.share_of_numerator is not None
                       else _money(f.committed)),
             "sentiment": "warning"}
            for f in top_wapd
        ]

    if watchlist.facility_count > 0:
        metrics[f"{prefix} · Watchlist"] = [
            {"label": "Facility Count",
             "value": f"{watchlist.facility_count:,}",
             "sentiment": "warning"},
            {"label": "Committed Exposure",
             "value": _money(watchlist.committed),
             "sentiment": "warning"},
        ]

    # ── verifiable_values ─────────────────────────────────────
    # Every label here is the prefix-disambiguated form so it cannot
    # collide with firm-level or other-slice labels. The verifier
    # matches claim.source_field exactly — no fuzzy resolution.
    vv: dict = {
        f"{prefix} — Committed Exposure":   {"value": totals.committed,    "type": "currency"},
        f"{prefix} — Outstanding Exposure": {"value": totals.outstanding,  "type": "currency"},
        f"{prefix} — Distinct ultimate parents": {"value": counts.parents,    "type": "count"},
        f"{prefix} — Distinct facilities":  {"value": counts.facilities,   "type": "count"},
        f"{prefix} — Criticized & Classified exposure (SM + SS + Dbt + L)": {
            "value": totals.criticized_classified, "type": "currency",
        },
        f"{prefix} — As of": {"value": as_of, "type": "date"},
    }
    if share_of_firm is not None:
        vv[f"{prefix} — Share of firm committed exposure"] = {
            "value": share_of_firm / 100, "type": "percentage",
        }
    if totals.cc_pct_of_commitment is not None:
        vv[f"{prefix} — C&C as % of commitment"] = {
            "value": totals.cc_pct_of_commitment, "type": "percentage",
        }
    if current.wapd.display:
        vv[f"{prefix} — Weighted Average PD"] = {
            "value": current.wapd.display, "type": "string",
        }
    if current.walgd.display:
        vv[f"{prefix} — Weighted Average LGD"] = {
            "value": current.walgd.display, "type": "string",
        }

    # Rating composition.
    if rating.investment_grade is not None:
        ig_committed = rating.investment_grade.current.totals.committed
        vv[f"{prefix} — Investment Grade"] = {"value": ig_committed, "type": "currency"}
        if rated_total > 0:
            vv[f"{prefix} — Investment Grade (% of rated commitment)"] = {
                "value": ig_committed / rated_total, "type": "percentage",
            }
    if rating.non_investment_grade is not None:
        vv[f"{prefix} — Non-Investment Grade"] = {"value": nig_committed, "type": "currency"}
        if rated_total > 0:
            vv[f"{prefix} — Non-Investment Grade (% of rated commitment)"] = {
                "value": nig_committed / rated_total, "type": "percentage",
            }
    if rating.defaulted is not None:
        d_committed = rating.defaulted.current.totals.committed
        vv[f"{prefix} — Defaulted"] = {"value": d_committed, "type": "currency"}
        if totals.committed > 0:
            vv[f"{prefix} — Defaulted (% of slice commitment)"] = {
                "value": d_committed / totals.committed, "type": "percentage",
            }
    if rating.non_rated is not None:
        nr_committed = rating.non_rated.current.totals.committed
        vv[f"{prefix} — Non-Rated"] = {"value": nr_committed, "type": "currency"}
        if totals.committed > 0:
            vv[f"{prefix} — Non-Rated (% of slice commitment)"] = {
                "value": nr_committed / totals.committed, "type": "percentage",
            }
    if rating.distressed_substats is not None:
        ds = rating.distressed_substats
        vv[f"{prefix} — Distressed (of which)"] = {"value": ds.committed, "type": "currency"}
        vv[f"{prefix} — Distressed facility count"] = {
            "value": ds.facility_count, "type": "count",
        }
        if nig_committed > 0:
            vv[f"{prefix} — Distressed (% of NIG)"] = {
                "value": ds.committed / nig_committed, "type": "percentage",
            }

    # Top parents.
    for c in top_parents:
        label = c.entity_name or c.entity_id
        vv[f"{prefix} — {label}"] = {"value": c.committed, "type": "currency"}
        if totals.committed > 0:
            vv[f"{prefix} — {label} (% of slice commitment)"] = {
                "value": c.committed / totals.committed, "type": "percentage",
            }

    # Facility-level WAPD drivers.
    for f in top_wapd:
        label = f.facility_name or f.facility_id
        vv[f"{prefix} — {label} (committed)"] = {
            "value": f.committed, "type": "currency",
        }
        vv[f"{prefix} — {label} (WAPD numerator)"] = {
            "value": f.wapd_numerator, "type": "currency",
        }
        if f.share_of_numerator is not None:
            vv[f"{prefix} — {label} (share of slice WAPD numerator)"] = {
                "value": f.share_of_numerator, "type": "percentage",
            }
        if f.implied_pd is not None:
            vv[f"{prefix} — {label} (implied PD)"] = {
                "value": f.implied_pd, "type": "percentage",
            }

    if watchlist.facility_count > 0 or watchlist.committed > 0:
        vv[f"{prefix} — Watchlist facility count"] = {
            "value": watchlist.facility_count, "type": "count",
        }
        vv[f"{prefix} — Watchlist committed exposure"] = {
            "value": watchlist.committed, "type": "currency",
        }

    return {
        "context": context,
        "metrics": metrics,
        "verifiable_values": vv,
    }


# ── helpers ───────────────────────────────────────────────────

def _money(amount: float) -> str:
    return f"${amount / 1_000_000:,.1f}M"


def _cc_sentiment(cc_pct):
    if cc_pct is None:
        return "neutral"
    if cc_pct > 0.05:
        return "warning"
    return "neutral"
