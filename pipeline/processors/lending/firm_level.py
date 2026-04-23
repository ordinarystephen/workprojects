# ── KRONOS · pipeline/processors/lending/firm_level.py ────────
# Firm-level view processor (Lending cube slicer).
#
# Takes a fully computed LendingCube and produces the
# { context, metrics, verifiable_values } payload that server.py
# returns to the frontend. The cube is the single source of truth —
# this file only picks WHICH parts of it to send to the LLM and which
# to render as Data Snapshot tiles.
#
# Layout (Round 18 — depth-first context, lean tile panel):
#   • CONTEXT — eight sections in fixed order: vitals, rating
#     composition, industries (ALL, sorted by committed desc),
#     horizontals (ALL), top-10 parent borrowers, top-10 facility-
#     level WAPD drivers, watchlist, MoM (only when ≥ 2 periods).
#   • METRICS — exactly 8 tiles in a fixed order. The tile panel is
#     intentionally narrow: anything analytical lives in context.
#   • VERIFIABLE_VALUES — every figure cited in context, keyed with
#     scope-disambiguating prefixes:
#         Industry: <name> — <metric>
#         Horizontal: <name> — <metric>
#         Top Parent: <name> — <metric>
#         WAPD Driver: <name> — <metric>
#         MoM: <metric>
#         MoM PD Change: <facility> — <metric>
#     Firm-level vitals stay plain (Committed Exposure, etc.).
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from typing import Optional

from pipeline.cube.models import (
    Contributor,
    ExposureMover,
    FacilityContributor,
    KriBlock,
    LendingCube,
    RatingChange,
)
from pipeline.processors.lending._bucket_status import (
    decorate,
    sort_key,
)
from pipeline.registry import register_slicer
from pipeline.scales import pd_scale


@register_slicer("firm_level")
def slice_firm_level(cube: LendingCube) -> dict:
    """Build the firm-level payload (context, metrics, verifiable_values)."""
    verifiable_values: dict = {}
    sections: list[str] = []

    s, vv = _section_vitals(cube)
    sections.append(s)
    verifiable_values.update(vv)

    s, vv = _section_rating_composition(cube)
    if s:
        sections.append(s)
    verifiable_values.update(vv)

    s, vv = _section_industries(cube)
    if s:
        sections.append(s)
        verifiable_values.update(vv)

    s, vv = _section_horizontals(cube)
    if s:
        sections.append(s)
        verifiable_values.update(vv)

    s, vv = _section_top_parents(cube)
    if s:
        sections.append(s)
        verifiable_values.update(vv)

    s, vv = _section_wapd_drivers(cube)
    if s:
        sections.append(s)
        verifiable_values.update(vv)

    s, vv = _section_watchlist(cube)
    if s:
        sections.append(s)
        verifiable_values.update(vv)

    s, vv = _section_mom(cube)
    if s:
        sections.append(s)
        verifiable_values.update(vv)

    # Period coverage / validation tail.
    if len(cube.metadata.periods) > 1:
        period_strs = ", ".join(p.isoformat() for p in cube.metadata.periods)
        sections.append(
            f"File covers {len(cube.metadata.periods)} periods: {period_strs}. "
            f"Headline figures above are for the latest period only."
        )
    if not cube.firm_level.current.totals.validation_ok:
        diff = cube.firm_level.current.totals.validation_diff
        sections.append(
            f"Note: rated-exposure components do not reconcile to total committed "
            f"(diff ${diff:,.2f}). Source data may have a quality issue."
        )

    context = "\n\n".join(sections)
    metrics = _build_tiles(cube)

    return {
        "context": context,
        "metrics": metrics,
        "verifiable_values": verifiable_values,
    }


# ── Section 1: Firm-level vitals ──────────────────────────────

def _section_vitals(cube: LendingCube) -> tuple[str, dict]:
    current = cube.firm_level.current
    totals = current.totals
    counts = current.counts
    wapd = current.wapd
    walgd = current.walgd
    as_of = cube.metadata.as_of.isoformat()

    branch_count = len(cube.by_branch)
    segment_count = len(cube.by_segment)

    lines = [
        f"Firm-level lending portfolio snapshot — as of {as_of}.",
        "",
        "Vitals:",
        f"- Committed Exposure: {_money_full(totals.committed)}",
        f"- Outstanding Exposure: {_money_full(totals.outstanding)}",
        f"- Weighted Average PD: {wapd.display or 'n/a'}",
        f"- Weighted Average LGD: {walgd.display or 'n/a'}",
        f"- Approved Limit: {_money_full(totals.approved_limit)}",
        f"- Take & Hold Exposure: {_money_full(totals.take_and_hold)}",
        f"- Temporary Exposure: {_money_full(totals.temporary)}",
        f"- Distinct facilities: {counts.facilities:,}",
        f"- Distinct ultimate parents: {counts.parents:,}",
        f"- Distinct partners: {counts.partners:,}",
        f"- Distinct risk assessment industries: {counts.industries:,}",
        f"- Distinct branches: {branch_count:,}",
        f"- Distinct segments: {segment_count:,}",
        "",
        "Regulatory rating breakdown (USD):",
        f"- Pass: {_money_full(totals.pass_rated)}",
        f"- Special Mention: {_money_full(totals.special_mention)}",
        f"- Substandard: {_money_full(totals.substandard)}",
        f"- Doubtful: {_money_full(totals.doubtful)}",
        f"- Loss: {_money_full(totals.loss)}",
        f"- No Regulatory Rating: {_money_full(totals.no_regulatory_rating)}",
        f"- Criticized & Classified (SM + SS + Dbt + L): {_money_full(totals.criticized_classified)}",
    ]
    if totals.cc_pct_of_commitment is not None:
        lines.append(f"- C&C as % of commitment: {_pct(totals.cc_pct_of_commitment)}")

    vv: dict = {
        "Committed Exposure":    {"value": totals.committed,       "type": "currency"},
        "Outstanding Exposure":  {"value": totals.outstanding,     "type": "currency"},
        "Take & Hold Exposure":  {"value": totals.take_and_hold,   "type": "currency"},
        "Temporary Exposure":    {"value": totals.temporary,       "type": "currency"},
        "Approved Limit":        {"value": totals.approved_limit,  "type": "currency"},

        "Distinct ultimate parents":           {"value": counts.parents,    "type": "count"},
        "Distinct partners":                   {"value": counts.partners,   "type": "count"},
        "Distinct facilities":                 {"value": counts.facilities, "type": "count"},
        "Distinct risk assessment industries": {"value": counts.industries, "type": "count"},
        "Distinct branches":                   {"value": branch_count,      "type": "count"},
        "Distinct segments":                   {"value": segment_count,     "type": "count"},

        "Pass":                  {"value": totals.pass_rated,           "type": "currency"},
        "Special Mention":       {"value": totals.special_mention,      "type": "currency"},
        "Substandard":           {"value": totals.substandard,          "type": "currency"},
        "Doubtful":              {"value": totals.doubtful,             "type": "currency"},
        "Loss":                  {"value": totals.loss,                 "type": "currency"},
        "No Regulatory Rating":  {"value": totals.no_regulatory_rating, "type": "currency"},
        "Criticized & Classified (SM + SS + Dbt + L)": {
            "value": totals.criticized_classified, "type": "currency",
        },
        "as of": {"value": as_of, "type": "date"},
    }
    if totals.cc_pct_of_commitment is not None:
        vv["C&C as % of commitment"] = {
            "value": totals.cc_pct_of_commitment, "type": "percentage",
        }
    if wapd.display:
        vv["Weighted Average PD"] = {"value": wapd.display, "type": "string"}
    if walgd.display:
        vv["Weighted Average LGD"] = {"value": walgd.display, "type": "string"}

    return "\n".join(lines), vv


# ── Section 2: Rating-category composition ────────────────────

def _section_rating_composition(cube: LendingCube) -> tuple[str, dict]:
    """IG / NIG / Distressed (sub-stat) / Defaulted / Non-Rated.

    Distressed renders as an indented "of which" sub-line under NIG so
    a reader of the context understands it is a subset, not a peer.
    Bucket labels carry "(exited)" / "(new this period)" markers via
    decorate(). Verifiable_values keys stay plain (no markers) — that
    invariant is enforced by test_firm_level_verifiable_values_keys_are_plain.
    """
    periods = cube.metadata.periods
    lines: list[str] = []
    vv: dict = {}

    if "Investment Grade" in cube.by_ig_status:
        hist = cube.by_ig_status["Investment Grade"]
        label = decorate("Investment Grade", hist, periods)
        lines.append(
            f"- {label}: {_money_full(hist.current.totals.committed)}, "
            f"{hist.current.counts.facilities:,} facilities"
        )
        vv["Investment Grade"] = {
            "value": hist.current.totals.committed, "type": "currency",
        }
    if "Non-Investment Grade" in cube.by_ig_status:
        hist = cube.by_ig_status["Non-Investment Grade"]
        label = decorate("Non-Investment Grade", hist, periods)
        lines.append(
            f"- {label}: {_money_full(hist.current.totals.committed)}, "
            f"{hist.current.counts.facilities:,} facilities"
        )
        vv["Non-Investment Grade"] = {
            "value": hist.current.totals.committed, "type": "currency",
        }
        if cube.nig_distressed_substats is not None:
            ds = cube.nig_distressed_substats
            lines.append(
                f"    Of which, Distressed (C13): {_money_full(ds.committed)}, "
                f"{ds.facility_count:,} facilities"
            )
            vv["Distressed (of which)"] = {"value": ds.committed, "type": "currency"}
            vv["Distressed facility count"] = {
                "value": ds.facility_count, "type": "count",
            }
    for name, hist in cube.by_defaulted.items():
        label = decorate(name, hist, periods)
        lines.append(
            f"- {label}: {_money_full(hist.current.totals.committed)}, "
            f"{hist.current.counts.facilities:,} facilities"
        )
        vv[name] = {"value": hist.current.totals.committed, "type": "currency"}
    for name, hist in cube.by_non_rated.items():
        label = decorate(name, hist, periods)
        lines.append(
            f"- {label}: {_money_full(hist.current.totals.committed)}, "
            f"{hist.current.counts.facilities:,} facilities"
        )
        vv[name] = {"value": hist.current.totals.committed, "type": "currency"}

    if not lines:
        return "", vv
    return "Rating-category composition (committed exposure):\n" + "\n".join(lines), vv


# ── Section 3: Industry breakdown (ALL industries) ────────────

def _section_industries(cube: LendingCube) -> tuple[str, dict]:
    if not cube.by_industry:
        return "", {}

    firm_committed = cube.firm_level.current.totals.committed
    periods = cube.metadata.periods

    ranked = sorted(
        cube.by_industry.items(),
        key=lambda kv: sort_key(kv[0], kv[1]),
    )

    lines: list[str] = ["Industry breakdown (ranked by committed; exited buckets at the bottom):"]
    vv: dict = {}
    for name, hist in ranked:
        committed = hist.current.totals.committed
        outstanding = hist.current.totals.outstanding
        facilities = hist.current.counts.facilities
        wapd_display = hist.current.wapd.display or "n/a"
        share = (committed / firm_committed) if firm_committed > 0 else None
        share_str = _pct(share) if share is not None else "n/a"
        display = decorate(name, hist, periods)
        lines.append(
            f"- {display}: committed {_money_full(committed)} ({share_str} of firm), "
            f"outstanding {_money_full(outstanding)}, WAPD {wapd_display}, "
            f"{facilities:,} facilities"
        )

        prefix = f"Industry: {name}"
        vv[f"{prefix} — Committed"] = {"value": committed, "type": "currency"}
        vv[f"{prefix} — Outstanding"] = {"value": outstanding, "type": "currency"}
        vv[f"{prefix} — Facility count"] = {"value": facilities, "type": "count"}
        if share is not None:
            vv[f"{prefix} — % of firm committed"] = {
                "value": share, "type": "percentage",
            }
        if hist.current.wapd.display:
            vv[f"{prefix} — Weighted Average PD"] = {
                "value": hist.current.wapd.display, "type": "string",
            }

    return "\n".join(lines), vv


# ── Section 4: Horizontal portfolio breakdown ─────────────────

def _section_horizontals(cube: LendingCube) -> tuple[str, dict]:
    if not cube.by_horizontal:
        return "", {}

    firm_committed = cube.firm_level.current.totals.committed
    periods = cube.metadata.periods

    ranked = sorted(
        cube.by_horizontal.items(),
        key=lambda kv: sort_key(kv[0], kv[1]),
    )

    lines: list[str] = ["Horizontal portfolios (overlays on the book — not partitions):"]
    vv: dict = {}
    for name, hist in ranked:
        committed = hist.current.totals.committed
        outstanding = hist.current.totals.outstanding
        facilities = hist.current.counts.facilities
        wapd_display = hist.current.wapd.display or "n/a"
        share = (committed / firm_committed) if firm_committed > 0 else None
        share_str = _pct(share) if share is not None else "n/a"
        display = decorate(name, hist, periods)
        lines.append(
            f"- {display}: committed {_money_full(committed)} ({share_str} of firm), "
            f"outstanding {_money_full(outstanding)}, WAPD {wapd_display}, "
            f"{facilities:,} facilities"
        )

        # Plain-name key kept for backward compatibility (existing
        # smoke tests assert "Leveraged Finance" / "Global Recovery
        # Management" resolve at firm-level scope). Prefixed keys are
        # the canonical citation surface for new prompts.
        vv[name] = {"value": committed, "type": "currency"}

        prefix = f"Horizontal: {name}"
        vv[f"{prefix} — Committed"] = {"value": committed, "type": "currency"}
        vv[f"{prefix} — Outstanding"] = {"value": outstanding, "type": "currency"}
        vv[f"{prefix} — Facility count"] = {"value": facilities, "type": "count"}
        if share is not None:
            vv[f"{prefix} — % of firm committed"] = {
                "value": share, "type": "percentage",
            }
        if hist.current.wapd.display:
            vv[f"{prefix} — Weighted Average PD"] = {
                "value": hist.current.wapd.display, "type": "string",
            }

    return "\n".join(lines), vv


# ── Section 5: Top-10 parent borrowers ────────────────────────

def _section_top_parents(cube: LendingCube) -> tuple[str, dict]:
    parents = cube.top_contributors.by_committed[:10]
    if not parents:
        return "", {}

    firm_committed = cube.firm_level.current.totals.committed
    lines = [f"Top-{len(parents)} parent borrowers (ranked by committed exposure):"]
    vv: dict = {}
    for c in parents:
        name = _entity_label(c)
        share = (c.committed / firm_committed) if firm_committed > 0 else None
        share_str = _pct(share) if share is not None else "n/a"
        # PD rating is not stored on Contributor — derive from numerator/committed.
        # Documented as derived (not a stored field) so a reader understands
        # the cube didn't pre-compute it.
        implied_pd = (c.wapd_numerator / c.committed) if c.committed > 0 else None
        pd_code = pd_scale.code_for_pd(implied_pd) if implied_pd is not None else None
        pd_str = pd_code or "n/a"
        lines.append(
            f"- {name}: committed {_money_full(c.committed)} ({share_str} of firm), "
            f"outstanding {_money_full(c.outstanding)}, "
            f"implied PD rating {pd_str}"
        )

        prefix = f"Top Parent: {name}"
        vv[f"{prefix} — Committed"] = {"value": c.committed, "type": "currency"}
        vv[f"{prefix} — Outstanding"] = {"value": c.outstanding, "type": "currency"}
        if share is not None:
            vv[f"{prefix} — % of firm committed"] = {
                "value": share, "type": "percentage",
            }
        if pd_code:
            vv[f"{prefix} — Implied PD rating"] = {"value": pd_code, "type": "string"}

    return "\n".join(lines), vv


# ── Section 6: Top-10 facility-level WAPD drivers ─────────────

def _section_wapd_drivers(cube: LendingCube) -> tuple[str, dict]:
    drivers = cube.top_wapd_facility_contributors[:10]
    if not drivers:
        return "", {}

    lines = [f"Top-{len(drivers)} facility-level WAPD drivers (ranked by PD × Committed):"]
    vv: dict = {}
    for f in drivers:
        name = _facility_label(f)
        parent = f.parent_name or "n/a"
        share_str = _pct(f.share_of_numerator) if f.share_of_numerator is not None else "n/a"
        implied_str = _pct(f.implied_pd) if f.implied_pd is not None else "n/a"
        pd_rating = f.pd_rating or "n/a"
        lines.append(
            f"- {name} (parent {parent}): committed {_money_full(f.committed)}, "
            f"WAPD numerator {_money_full(f.wapd_numerator)} "
            f"({share_str} of firm WAPD numerator), "
            f"implied PD {implied_str} ({pd_rating})"
        )

        prefix = f"WAPD Driver: {name}"
        vv[f"{prefix} — Committed"] = {"value": f.committed, "type": "currency"}
        vv[f"{prefix} — WAPD numerator"] = {
            "value": f.wapd_numerator, "type": "currency",
        }
        if f.share_of_numerator is not None:
            vv[f"{prefix} — Share of firm WAPD numerator"] = {
                "value": f.share_of_numerator, "type": "percentage",
            }
        if f.implied_pd is not None:
            vv[f"{prefix} — Implied PD"] = {
                "value": f.implied_pd, "type": "percentage",
            }
        if f.pd_rating:
            vv[f"{prefix} — PD rating"] = {
                "value": f.pd_rating, "type": "string",
            }

    return "\n".join(lines), vv


# ── Section 7: Watchlist firm-level aggregate ─────────────────

def _section_watchlist(cube: LendingCube) -> tuple[str, dict]:
    if cube.watchlist.facility_count == 0 and cube.watchlist.committed == 0:
        return "", {}

    lines = [
        "Watchlist (firm-level aggregate, Credit Watch List Flag = Y):",
        f"- Watchlist facility count: {cube.watchlist.facility_count:,}",
        f"- Watchlist committed exposure: {_money_full(cube.watchlist.committed)}",
    ]
    vv = {
        "Watchlist facility count": {
            "value": cube.watchlist.facility_count, "type": "count",
        },
        "Watchlist committed exposure": {
            "value": cube.watchlist.committed, "type": "currency",
        },
    }
    return "\n".join(lines), vv


# ── Section 8: Month-over-period changes ──────────────────────

def _section_mom(cube: LendingCube) -> tuple[str, dict]:
    """Period-over-period narrative. Only emitted when MoM is populated.

    Sources:
      • cube.month_over_month: counts + per-facility change lists
      • cube.firm_level.history[-2] vs current: firm-level deltas

    Limitations flagged inline (and in the Round 18 summary):
      • RatingChange has no `committed` field, so the "top changes"
        in the prose are first-3 in the cube's Facility-ID-sorted
        order — not top-3 by changed exposure. A future cube extension
        could add committed to RatingChange to true the spec.
    """
    mom = cube.month_over_month
    if mom is None:
        return "", {}

    history = cube.firm_level.history
    prior_block: Optional[KriBlock] = history[-2] if len(history) >= 2 else None
    current_block = cube.firm_level.current

    lines: list[str] = [
        f"Month-over-period changes (prior {mom.prior_period.isoformat()} → "
        f"current {mom.current_period.isoformat()}):"
    ]
    vv: dict = {}

    # Firm-level deltas (computed inline from history).
    if prior_block is not None:
        committed_delta = current_block.totals.committed - prior_block.totals.committed
        outstanding_delta = current_block.totals.outstanding - prior_block.totals.outstanding
        facility_delta = current_block.counts.facilities - prior_block.counts.facilities
        committed_delta_pct: Optional[float] = (
            (committed_delta / prior_block.totals.committed)
            if prior_block.totals.committed > 0 else None
        )
        wapd_shift = (
            f"{prior_block.wapd.display or 'n/a'} → "
            f"{current_block.wapd.display or 'n/a'}"
        )

        delta_pct_str = _signed_pct(committed_delta_pct) if committed_delta_pct is not None else "n/a"
        lines.append(
            f"- Firm committed change: {_signed_money_full(committed_delta)} "
            f"({delta_pct_str})"
        )
        lines.append(f"- Firm outstanding change: {_signed_money_full(outstanding_delta)}")
        lines.append(f"- Firm WAPD shift: {wapd_shift}")
        lines.append(f"- Firm facility count change: {_signed_count(facility_delta)}")

        vv["MoM: Firm committed change"] = {"value": committed_delta, "type": "currency"}
        if committed_delta_pct is not None:
            vv["MoM: Firm committed change (%)"] = {
                "value": committed_delta_pct, "type": "percentage",
            }
        vv["MoM: Firm outstanding change"] = {
            "value": outstanding_delta, "type": "currency",
        }
        vv["MoM: Firm WAPD shift"] = {"value": wapd_shift, "type": "string"}
        vv["MoM: Firm facility count change"] = {
            "value": facility_delta, "type": "count",
        }

    # Originations / exits (totals + counts).
    new_total = sum(e.committed for e in mom.new_originations)
    exit_total = sum(e.committed for e in mom.exits)
    lines.append(
        f"- New originations: {len(mom.new_originations):,} facilities "
        f"totalling {_money_full(new_total)}"
    )
    lines.append(
        f"- Exits: {len(mom.exits):,} facilities totalling {_money_full(exit_total)}"
    )
    vv["MoM: New originations count"] = {
        "value": len(mom.new_originations), "type": "count",
    }
    vv["MoM: New originations total"] = {"value": new_total, "type": "currency"}
    vv["MoM: Exits count"] = {"value": len(mom.exits), "type": "count"}
    vv["MoM: Exits total"] = {"value": exit_total, "type": "currency"}

    # PD / reg changes — counts split by direction.
    pd_up = sum(1 for c in mom.pd_rating_changes if c.direction == "upgrade")
    pd_down = sum(1 for c in mom.pd_rating_changes if c.direction == "downgrade")
    reg_up = sum(1 for c in mom.reg_rating_changes if c.direction == "upgrade")
    reg_down = sum(1 for c in mom.reg_rating_changes if c.direction == "downgrade")
    lines.append(
        f"- PD rating changes: {len(mom.pd_rating_changes):,} "
        f"({pd_down:,} downgrades, {pd_up:,} upgrades)"
    )
    lines.append(
        f"- Regulatory rating changes: {len(mom.reg_rating_changes):,} "
        f"({reg_down:,} downgrades, {reg_up:,} upgrades)"
    )
    vv["MoM: PD rating changes count"] = {
        "value": len(mom.pd_rating_changes), "type": "count",
    }
    vv["MoM: PD downgrades count"] = {"value": pd_down, "type": "count"}
    vv["MoM: PD upgrades count"] = {"value": pd_up, "type": "count"}
    vv["MoM: Reg rating changes count"] = {
        "value": len(mom.reg_rating_changes), "type": "count",
    }
    vv["MoM: Reg downgrades count"] = {"value": reg_down, "type": "count"}
    vv["MoM: Reg upgrades count"] = {"value": reg_up, "type": "count"}

    # Top-3 PD changes (cube order — see docstring).
    pd_top = mom.pd_rating_changes[:3]
    if pd_top:
        lines.append("- Notable PD rating changes:")
        for c in pd_top:
            name = _change_label(c)
            lines.append(
                f"    • {name}: {c.prior or 'n/a'} → {c.current or 'n/a'} "
                f"({c.direction or 'n/a'})"
            )
            prefix = f"MoM PD Change: {name}"
            vv[f"{prefix} — From"] = {"value": c.prior or "", "type": "string"}
            vv[f"{prefix} — To"] = {"value": c.current or "", "type": "string"}
            vv[f"{prefix} — Direction"] = {"value": c.direction or "", "type": "string"}

    # Top-3 reg changes (cube order).
    reg_top = mom.reg_rating_changes[:3]
    if reg_top:
        lines.append("- Notable regulatory rating changes:")
        for c in reg_top:
            name = _change_label(c)
            lines.append(
                f"    • {name}: {c.prior or 'n/a'} → {c.current or 'n/a'} "
                f"({c.direction or 'n/a'})"
            )
            prefix = f"MoM Reg Change: {name}"
            vv[f"{prefix} — From"] = {"value": c.prior or "", "type": "string"}
            vv[f"{prefix} — To"] = {"value": c.current or "", "type": "string"}
            vv[f"{prefix} — Direction"] = {"value": c.direction or "", "type": "string"}

    # Top-3 exposure movers (sorted by abs(delta) in cube — keep that order).
    movers = mom.top_exposure_movers[:3]
    if movers:
        lines.append("- Top exposure movers:")
        for m in movers:
            name = _mover_label(m)
            lines.append(
                f"    • {name}: {_money_full(m.prior_committed)} → "
                f"{_money_full(m.current_committed)} "
                f"({_signed_money_full(m.delta_committed)})"
            )
            prefix = f"MoM Exposure Mover: {name}"
            vv[f"{prefix} — Prior committed"] = {
                "value": m.prior_committed, "type": "currency",
            }
            vv[f"{prefix} — Current committed"] = {
                "value": m.current_committed, "type": "currency",
            }
            vv[f"{prefix} — Delta committed"] = {
                "value": m.delta_committed, "type": "currency",
            }

    return "\n".join(lines), vv


# ── Tile panel (exactly 8 tiles, fixed order) ─────────────────

def _build_tiles(cube: LendingCube) -> dict:
    """Eight tiles in a fixed order. Anything analytical lives in context.

    Order:
      1. Total Limit
      2. Total Take and Hold
      3. Total Outstanding
      4. Total Temporary Exposure
      5. Weighted Average PD (display string only)
      6. Criticized & Classified ($)
      7. C&C (% of commitment)
      8. Total Leveraged Commitment (Leveraged Finance horizontal, $0 if absent)
    """
    current = cube.firm_level.current
    totals = current.totals
    wapd = current.wapd

    lf_hist = cube.by_horizontal.get("Leveraged Finance")
    lf_committed = lf_hist.current.totals.committed if lf_hist is not None else 0.0

    cc_pct_str = (
        _pct(totals.cc_pct_of_commitment)
        if totals.cc_pct_of_commitment is not None else "n/a"
    )

    return {
        f"Firm-Level Overview · As of {cube.metadata.as_of.isoformat()}": [
            {"label": "Total Limit",              "value": _money(totals.approved_limit), "sentiment": "neutral"},
            {"label": "Total Take and Hold",      "value": _money(totals.take_and_hold),  "sentiment": "neutral"},
            {"label": "Total Outstanding",        "value": _money(totals.outstanding),    "sentiment": "neutral"},
            {"label": "Total Temporary Exposure", "value": _money(totals.temporary),      "sentiment": "neutral"},
            {"label": "Weighted Average PD",      "value": (wapd.display or "n/a"),       "sentiment": "neutral"},
            {"label": "Criticized & Classified",
             "value": _money(totals.criticized_classified),
             "sentiment": _cc_sentiment(totals.cc_pct_of_commitment)},
            {"label": "C&C (% of commitment)",
             "value": cc_pct_str,
             "sentiment": _cc_sentiment(totals.cc_pct_of_commitment)},
            {"label": "Total Leveraged Commitment", "value": _money(lf_committed), "sentiment": "neutral"},
        ],
    }


# ── helpers ───────────────────────────────────────────────────

def _money(amount: float) -> str:
    """Format dollars as $X.XM for tile display (millions, 1 decimal)."""
    return f"${amount / 1_000_000:,.1f}M"


def _money_full(amount: float) -> str:
    """Format dollars as $X,XXX,XXX.XX for context prose (full precision)."""
    return f"${amount:,.2f}"


def _signed_money_full(amount: float) -> str:
    """Format a signed delta with explicit + or - sign."""
    sign = "+" if amount >= 0 else "-"
    return f"{sign}${abs(amount):,.2f}"


def _pct(decimal: Optional[float]) -> str:
    if decimal is None:
        return "n/a"
    return f"{decimal * 100:.2f}%"


def _signed_pct(decimal: Optional[float]) -> str:
    if decimal is None:
        return "n/a"
    sign = "+" if decimal >= 0 else "-"
    return f"{sign}{abs(decimal) * 100:.2f}%"


def _signed_count(n: int) -> str:
    sign = "+" if n >= 0 else "-"
    return f"{sign}{abs(n):,}"


def _cc_sentiment(cc_pct: Optional[float]) -> str:
    """Heuristic colorization for C&C tiles. None → neutral."""
    if cc_pct is None:
        return "neutral"
    if cc_pct > 0.05:
        return "warning"
    return "neutral"


def _entity_label(c: Contributor) -> str:
    return c.entity_name or c.entity_id


def _facility_label(f: FacilityContributor) -> str:
    return f.facility_name or f.facility_id


def _change_label(c: RatingChange) -> str:
    return c.facility_name or c.facility_id


def _mover_label(m: ExposureMover) -> str:
    return m.facility_name or m.facility_id
