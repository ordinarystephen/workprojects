# ── KRONOS · pipeline/processors/lending/firm_level.py ────────
# Firm-level view processor (Lending cube slicer).
#
# Takes a fully computed LendingCube and produces the
# { context, metrics } payload that server.py returns to the
# frontend. The cube is the single source of truth — this file
# only picks WHICH parts of it to send to the LLM and which to
# render as Data Snapshot tiles.
#
# Contract (matches MODE_MAP convention in pipeline/analyze.py):
#   slice(cube) -> {
#     "context": str,               # prose the LLM sees
#     "metrics": dict,              # Data Snapshot tiles
#     "verifiable_values": dict,    # label → { value, type } that backs the verifier
#   }
#
# The verifiable_values keys are the English labels that appear verbatim
# in the context. The LLM is prompted to cite those labels in claim
# source_field fields; pipeline/validate.py resolves each claim against
# this dict. If a figure is computed inline (sums of multiple fields),
# it is intentionally omitted — the LLM should mark those as "calculated".
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from pipeline.cube.models import LendingCube
from pipeline.registry import register_slicer


@register_slicer("firm_level")
def slice_firm_level(cube: LendingCube) -> dict:
    """
    Build the firm-level view: prose context for the LLM plus tile
    data for the Data Snapshot panel.

    Args:
        cube: a fully-computed LendingCube.

    Returns:
        dict with keys "context" (str), "metrics" (dict), and
        "verifiable_values" (dict).
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

    # Rating-category composition (IG / NIG / Defaulted / Non-Rated,
    # plus the Distressed sub-stat within NIG). Each top-level bucket
    # appears only when it has any rows in the latest period.
    rating_section = _rating_category_section(cube)
    if rating_section:
        context_lines += ["", "Rating-category composition (committed exposure):"]
        context_lines += rating_section

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

    rating_tiles = _rating_category_tiles(cube)
    if rating_tiles:
        metrics["Rating Category Composition"] = rating_tiles

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

    # ── verifiable_values ─────────────────────────────────────
    # Labels must exactly match the English labels that appear in the
    # context above. The LLM cites these labels back via claim.source_field
    # and pipeline/validate.verify_claims resolves them here.
    verifiable_values: dict = {
        "Distinct ultimate parents":           {"value": counts.parents,    "type": "count"},
        "Distinct partners":                   {"value": counts.partners,   "type": "count"},
        "Distinct facilities":                 {"value": counts.facilities, "type": "count"},
        "Distinct risk assessment industries": {"value": counts.industries, "type": "count"},

        "Committed Exposure":    {"value": totals.committed,       "type": "currency"},
        "Outstanding Exposure":  {"value": totals.outstanding,     "type": "currency"},
        "Take & Hold Exposure":  {"value": totals.take_and_hold,   "type": "currency"},
        "Temporary Exposure":    {"value": totals.temporary,       "type": "currency"},
        "Approved Limit":        {"value": totals.approved_limit,  "type": "currency"},

        "Pass":                  {"value": totals.pass_rated,           "type": "currency"},
        "Special Mention":       {"value": totals.special_mention,      "type": "currency"},
        "Substandard":           {"value": totals.substandard,          "type": "currency"},
        "Doubtful":              {"value": totals.doubtful,             "type": "currency"},
        "Loss":                  {"value": totals.loss,                 "type": "currency"},
        "No Regulatory Rating":  {"value": totals.no_regulatory_rating, "type": "currency"},
        "Criticized & Classified (SM + SS + Dbt + L)": {
            "value": totals.criticized_classified, "type": "currency"
        },
    }
    if totals.cc_pct_of_commitment is not None:
        verifiable_values["C&C as % of commitment"] = {
            "value": totals.cc_pct_of_commitment, "type": "percentage",
        }
    if wapd.display:
        verifiable_values["Weighted Average PD"] = {
            "value": wapd.display, "type": "string",
        }
    if walgd.display:
        verifiable_values["Weighted Average LGD"] = {
            "value": walgd.display, "type": "string",
        }

    # Rating-category buckets (IG/NIG/Defaulted/Non-Rated). Each label
    # matches the English label used in the context section so the
    # verifier in pipeline/validate.verify_claims can resolve citations.
    for label, hist in cube.by_ig_status.items():
        verifiable_values[label] = {
            "value": hist.current.totals.committed, "type": "currency",
        }
    for label, hist in cube.by_defaulted.items():
        verifiable_values[label] = {
            "value": hist.current.totals.committed, "type": "currency",
        }
    for label, hist in cube.by_non_rated.items():
        verifiable_values[label] = {
            "value": hist.current.totals.committed, "type": "currency",
        }
    # Distressed sub-stat (subset of NIG, reported as a separate figure).
    if cube.nig_distressed_substats is not None:
        ds = cube.nig_distressed_substats
        verifiable_values["Distressed (of which)"] = {
            "value": ds.committed, "type": "currency",
        }
        verifiable_values["Distressed facility count"] = {
            "value": ds.facility_count, "type": "count",
        }

    # Horizontal portfolios — committed figure keyed by portfolio name.
    for name, hist in cube.by_horizontal.items():
        verifiable_values[name] = {
            "value": hist.current.totals.committed, "type": "currency",
        }

    # Watchlist firm-level aggregate.
    if cube.watchlist.facility_count > 0 or cube.watchlist.committed > 0:
        verifiable_values["Watchlist facility count"] = {
            "value": cube.watchlist.facility_count, "type": "count",
        }
        verifiable_values["Watchlist committed exposure"] = {
            "value": cube.watchlist.committed, "type": "currency",
        }

    # Period metadata.
    verifiable_values["as of"] = {"value": as_of, "type": "date"}

    return {
        "context": context,
        "metrics": metrics,
        "verifiable_values": verifiable_values,
    }


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


def _rating_category_section(cube: "LendingCube") -> list[str]:
    """Prose lines for the rating-category composition block.

    Distressed is rendered as an indented sub-line under NIG so the
    LLM (and a human reader of the context) understand it's a subset,
    not a peer bucket. Defaulted and Non-Rated are top-level peers.
    """
    lines: list[str] = []
    if "Investment Grade" in cube.by_ig_status:
        hist = cube.by_ig_status["Investment Grade"]
        lines.append(
            f"- Investment Grade: ${hist.current.totals.committed:,.2f}, "
            f"{hist.current.counts.facilities:,} facilities"
        )
    if "Non-Investment Grade" in cube.by_ig_status:
        hist = cube.by_ig_status["Non-Investment Grade"]
        lines.append(
            f"- Non-Investment Grade: ${hist.current.totals.committed:,.2f}, "
            f"{hist.current.counts.facilities:,} facilities"
        )
        if cube.nig_distressed_substats is not None:
            ds = cube.nig_distressed_substats
            lines.append(
                f"    Of which, Distressed (C13): ${ds.committed:,.2f}, "
                f"{ds.facility_count:,} facilities"
            )
    for label, hist in cube.by_defaulted.items():
        lines.append(
            f"- {label}: ${hist.current.totals.committed:,.2f}, "
            f"{hist.current.counts.facilities:,} facilities"
        )
    for label, hist in cube.by_non_rated.items():
        lines.append(
            f"- {label}: ${hist.current.totals.committed:,.2f}, "
            f"{hist.current.counts.facilities:,} facilities"
        )
    return lines


def _rating_category_tiles(cube: "LendingCube") -> list[dict]:
    """Data Snapshot tiles for the rating-category composition."""
    tiles: list[dict] = []
    if "Investment Grade" in cube.by_ig_status:
        hist = cube.by_ig_status["Investment Grade"]
        tiles.append({
            "label": "Investment Grade",
            "value": _money(hist.current.totals.committed),
            "sentiment": "neutral",
        })
    if "Non-Investment Grade" in cube.by_ig_status:
        hist = cube.by_ig_status["Non-Investment Grade"]
        tiles.append({
            "label": "Non-Investment Grade",
            "value": _money(hist.current.totals.committed),
            "sentiment": "neutral",
        })
        if cube.nig_distressed_substats is not None:
            tiles.append({
                "label": "  of which Distressed",
                "value": _money(cube.nig_distressed_substats.committed),
                "sentiment": "warning",
            })
    for label, hist in cube.by_defaulted.items():
        tiles.append({
            "label": label,
            "value": _money(hist.current.totals.committed),
            "sentiment": "negative",
        })
    for label, hist in cube.by_non_rated.items():
        tiles.append({
            "label": label,
            "value": _money(hist.current.totals.committed),
            "sentiment": "neutral",
        })
    return tiles
