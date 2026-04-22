# ── KRONOS · pipeline/cube/lending.py ─────────────────────────
# Lending cube computer.
#
# Takes a validated Lending DataFrame (one row per facility per
# period) and computes the full LendingCube — firm-level KRIs plus
# every grouping (by_industry, by_segment, by_branch, horizontal
# portfolios, IG/NIG split), top contributors, and month-over-month
# derivations.
#
# All math is deterministic and cache-friendly: a future caller can
# hash the input DataFrame and persist the resulting cube.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from pipeline.cube.models import (
    Contributor,
    ContributorBlock,
    Counts,
    CubeMetadata,
    DistressedSubstats,
    ExposureMover,
    ExposureTotals,
    FacilityChange,
    FacilityContributor,
    GroupingHistory,
    KriBlock,
    LendingCube,
    MomBlock,
    RatingChange,
    WatchlistAggregate,
    WeightedAverage,
)
from pipeline.parsers import regulatory_rating as reg_rating
from pipeline.scales import pd_scale
from pipeline.templates.lending import LendingTemplate

log = logging.getLogger(__name__)

# Maximum number of unique unrecognized PD Rating values to surface in
# the cube metadata warning when the rating-category classification
# leaves rows uncovered.
_UNCLASSIFIED_SAMPLE_LIMIT = 10


# ── Tunables ──────────────────────────────────────────────────
# Validation tolerance: |sum(Pass+SM+SS+Dbt+L+NoReg) − Committed| ≤ this
# (in dollars). Per spec, anything beyond $2 is a real data issue.
EXPOSURE_VALIDATION_TOLERANCE = 2.0

# Number of top contributors per metric to retain in the cube.
TOP_N_CONTRIBUTORS = 10

# Number of top exposure movers (by |Δ committed|) to retain in MoM.
TOP_N_EXPOSURE_MOVERS = 10

# Number of top facility-level WAPD contributors to retain.
TOP_N_WAPD_FACILITIES = 10


# ── Public entry point ────────────────────────────────────────

def compute_lending_cube(df: pd.DataFrame) -> LendingCube:
    """
    Compute the full LendingCube from a validated Lending DataFrame.

    Args:
        df: validated DataFrame from LendingTemplate.validate().

    Returns:
        LendingCube populated with all groupings, contributors, and
        (if periods > 1) month-over-month derivations.
    """
    period_col = LendingTemplate.period_column()
    periods = sorted(df[period_col].dropna().unique())
    if not periods:
        raise ValueError("Lending workbook contains no valid period values.")

    latest_period = periods[-1]
    latest_df = df[df[period_col] == latest_period]

    # ── Firm level ────────────────────────────────────────────
    firm_history = _grouping_history(df, periods, mask=None)

    # ── By-industry, by-segment, by-branch ────────────────────
    by_industry = _grouping_by_dim(df, periods, "Risk Assessment Industry")
    by_segment  = _grouping_by_dim(df, periods, "Portfolio Segment Description")
    by_branch   = _grouping_by_dim(df, periods, "UBS Branch Name")

    # ── Horizontal portfolios ─────────────────────────────────
    by_horizontal: dict[str, GroupingHistory] = {}
    for col, spec in LendingTemplate.FIELDS.items():
        if spec.role != "horizontal_flag":
            continue
        portfolio = spec.portfolio_name or col
        mask = df[col].astype(str).str.strip() == str(spec.trigger_value).strip()
        if not mask.any():
            continue
        by_horizontal[portfolio] = _grouping_history(df, periods, mask=mask)

    # ── Rating-category classification ────────────────────────
    # Five mutually-exclusive buckets sourced from `PD Rating`:
    #   Investment Grade     (C00..C07)
    #   Non-Investment Grade (C08..C13 — includes Distressed)
    #   Defaulted            (CDF)
    #   Non-Rated            (placeholder values per pd_scale)
    #
    # Distressed (C13) is a subset of NIG, not a peer bucket, so its
    # mask is NOT included in the coverage check (those rows are
    # already counted under NIG). See nig_distressed_substats below.
    pd_upper = df["PD Rating"].astype(str).str.strip().str.upper()
    non_rated_mask  = df["PD Rating"].isna() | pd_upper.isin(pd_scale.NON_RATED_TOKENS)
    ig_mask         = pd_upper.isin(set(pd_scale.investment_grade_codes()))     & ~non_rated_mask
    nig_mask        = pd_upper.isin(set(pd_scale.non_investment_grade_codes())) & ~non_rated_mask
    defaulted_mask  = (pd_upper == pd_scale.defaulted_code())                   & ~non_rated_mask
    distressed_mask = (pd_upper == pd_scale.distressed_code())                  & ~non_rated_mask

    by_ig_status: dict[str, GroupingHistory] = {}
    if ig_mask.any():
        by_ig_status["Investment Grade"]     = _grouping_history(df, periods, mask=ig_mask)
    if nig_mask.any():
        by_ig_status["Non-Investment Grade"] = _grouping_history(df, periods, mask=nig_mask)

    by_defaulted: dict[str, GroupingHistory] = {}
    if defaulted_mask.any():
        by_defaulted["Defaulted"] = _grouping_history(df, periods, mask=defaulted_mask)

    by_non_rated: dict[str, GroupingHistory] = {}
    if non_rated_mask.any():
        by_non_rated["Non-Rated"] = _grouping_history(df, periods, mask=non_rated_mask)

    # Distressed sub-stat within NIG (latest period only).
    nig_distressed_substats: Optional[DistressedSubstats] = None
    if nig_mask.any() and distressed_mask.any():
        latest_distressed = latest_df[
            (latest_df.index.isin(df[distressed_mask].index))
        ]
        if len(latest_distressed):
            nig_distressed_substats = DistressedSubstats(
                period=_to_date(latest_period),
                committed=float(latest_distressed["Committed Exposure"].sum()),
                outstanding=float(latest_distressed["Outstanding Exposure"].sum()),
                facility_count=int(latest_distressed["Facility ID"].nunique()),
            )

    # Coverage invariant: every row must land in exactly one of the
    # four top-level buckets. A row that doesn't indicates a PD Rating
    # value the code doesn't recognize (e.g. a new code added to the
    # bank's scale but not yet reflected in pd_scale.py). Warn but
    # don't raise — the rest of the cube is still usable.
    covered = ig_mask | nig_mask | defaulted_mask | non_rated_mask
    cube_warnings: list[dict] = []
    if (~covered).any():
        unclassified = df.loc[~covered, "PD Rating"]
        sample_vals = (
            unclassified.dropna().astype(str).str.strip().unique().tolist()
        )[:_UNCLASSIFIED_SAMPLE_LIMIT]
        unclassified_count = int((~covered).sum())
        log.warning(
            "PD Rating classification left %d row(s) unclassified; sample values=%s",
            unclassified_count,
            sample_vals,
        )
        cube_warnings.append({
            "code": "pd_rating_unclassified",
            "count": unclassified_count,
            "sample_values": sample_vals,
        })

    # Dim-bucket reconciliation invariants. After `_normalize_dim` these
    # should always pass — failure means a new code path is dropping
    # rows between firm aggregation and dim bucketing.
    firm_committed = firm_history.current.totals.committed
    for section_name, sections in (
        ("by_industry", by_industry),
        ("by_segment",  by_segment),
        ("by_branch",   by_branch),
    ):
        warn = _check_dim_reconciliation(section_name, sections, firm_committed)
        if warn is not None:
            cube_warnings.append(warn)

    # ── Watchlist firm-level aggregate ────────────────────────
    watchlist = _watchlist_aggregate(latest_df, latest_period)

    # ── Top contributors (parent-level, latest period) ────────
    top_contribs = _top_contributors(latest_df, latest_period)

    # ── Facility-level WAPD drivers (firm + per-horizontal) ───
    top_wapd_facilities = _top_wapd_facility_contributors(latest_df)
    wapd_by_horizontal: dict[str, list[FacilityContributor]] = {}
    for col, spec in LendingTemplate.FIELDS.items():
        if spec.role != "horizontal_flag":
            continue
        portfolio = spec.portfolio_name or col
        if col not in latest_df.columns:
            continue
        h_mask = (
            latest_df[col].astype(str).str.strip()
            == str(spec.trigger_value).strip()
        )
        if not h_mask.any():
            continue
        wapd_by_horizontal[portfolio] = _top_wapd_facility_contributors(
            latest_df[h_mask]
        )

    # ── Month-over-month (only if ≥ 2 periods) ────────────────
    mom: Optional[MomBlock] = None
    if len(periods) >= 2:
        prior_period = periods[-2]
        mom = _month_over_month(
            df_prior=df[df[period_col] == prior_period],
            df_current=latest_df,
            prior_period=prior_period,
            current_period=latest_period,
        )

    metadata = CubeMetadata(
        template="lending",
        as_of=_to_date(latest_period),
        periods=[_to_date(p) for p in periods],
        row_count=len(df),
        warnings=cube_warnings,
    )

    return LendingCube(
        metadata=metadata,
        firm_level=firm_history,
        by_industry=by_industry,
        by_segment=by_segment,
        by_branch=by_branch,
        by_horizontal=by_horizontal,
        by_ig_status=by_ig_status,
        by_defaulted=by_defaulted,
        by_non_rated=by_non_rated,
        nig_distressed_substats=nig_distressed_substats,
        watchlist=watchlist,
        top_contributors=top_contribs,
        top_wapd_facility_contributors=top_wapd_facilities,
        wapd_contributors_by_horizontal=wapd_by_horizontal,
        month_over_month=mom,
    )


# ── KRI helpers ───────────────────────────────────────────────

def _kri_block(group: pd.DataFrame, period) -> KriBlock:
    """Compute a KriBlock from one period × grouping slice."""
    totals = _exposure_totals(group)
    counts = _counts(group)
    wapd   = _weighted_average(
        numerator_col="Weighted Average PD Numerator",
        denominator_col="Committed Exposure",
        df=group,
        display="rating_code",
    )
    walgd  = _weighted_average(
        numerator_col="Weighted Average LGD Numerator",
        denominator_col="Committed Exposure",
        df=group,
        display="percent_2dp",
    )
    return KriBlock(
        period=_to_date(period),
        totals=totals,
        counts=counts,
        wapd=wapd,
        walgd=walgd,
    )


def _exposure_totals(group: pd.DataFrame) -> ExposureTotals:
    s = lambda c: float(group[c].sum()) if c in group.columns else 0.0

    pass_     = s("Pass Rated Exposure")
    sm        = s("Special Mention Rated Exposure")
    ss        = s("Substandard Rated Exposure")
    dbt       = s("Doubtful Rated Exposure")
    loss      = s("Loss Rated Exposure")
    no_reg    = s("No Regulatory Rating Exposure")

    cc        = sm + ss + dbt + loss
    committed = s("Committed Exposure")

    cc_pct = (cc / committed) if committed else None

    components_total = pass_ + sm + ss + dbt + loss + no_reg
    diff = components_total - committed

    return ExposureTotals(
        approved_limit       = s("Approved Limit"),
        committed            = committed,
        outstanding          = s("Outstanding Exposure"),
        temporary            = s("Temporary Exposure"),
        take_and_hold        = s("Take & Hold Exposure"),
        pass_rated           = pass_,
        special_mention      = sm,
        substandard          = ss,
        doubtful             = dbt,
        loss                 = loss,
        no_regulatory_rating = no_reg,
        criticized_classified= cc,
        cc_pct_of_commitment = cc_pct,
        validation_diff      = diff,
        validation_ok        = abs(diff) <= EXPOSURE_VALIDATION_TOLERANCE,
    )


def _counts(group: pd.DataFrame) -> Counts:
    # `industries` counts normalized values so it matches len(by_industry):
    # NaN / blank rows collapse to a single "Unclassified" bucket and
    # contribute 1 to the count when present (rather than being dropped
    # by nunique()'s default NaN exclusion).
    return Counts(
        parents    = int(group["Ultimate Parent Code"].nunique()),
        partners   = int(group["Partner Code"].nunique()),
        facilities = int(group["Facility ID"].nunique()),
        industries = int(group["Risk Assessment Industry"].apply(_normalize_dim).nunique()),
    )


def _weighted_average(
    *,
    numerator_col: str,
    denominator_col: str,
    df: pd.DataFrame,
    display: str,
) -> WeightedAverage:
    """Compute a weighted average and render it per the display rule.

    Upstream data-contract assumption (NOT verifiable in code): the
    exporter is expected to produce `Weighted Average PD Numerator =
    PD × Committed Exposure` per row, with Non-Rated facilities
    (TBR/NTR/Unrated/…) weighted as if they were C07. This substitution
    is a business rule of the Power BI export, not a cube-level
    transform. If the numerator column does not reflect it, the WAPD
    figures this function produces will silently under-weight
    Non-Rated credits.
    """
    num = float(df[numerator_col].sum()) if numerator_col in df.columns else 0.0
    den = float(df[denominator_col].sum()) if denominator_col in df.columns else 0.0
    if den == 0:
        return WeightedAverage(raw=None, display=None, numerator=num, denominator=den)
    raw = num / den
    if display == "rating_code":
        rendered = pd_scale.code_for_pd(raw)
    elif display == "percent_2dp":
        rendered = f"{raw * 100:.2f}%"
    elif display == "decimal_2dp":
        rendered = f"{raw:.2f}"
    else:
        rendered = None
    return WeightedAverage(raw=raw, display=rendered, numerator=num, denominator=den)


# ── Grouping history builders ─────────────────────────────────

def _grouping_history(
    df: pd.DataFrame,
    periods: list,
    mask: Optional[pd.Series],
) -> GroupingHistory:
    """KriBlock for the latest period plus history for prior periods."""
    period_col = LendingTemplate.period_column()
    scoped = df if mask is None else df[mask]

    history: list[KriBlock] = []
    for p in periods:
        slice_ = scoped[scoped[period_col] == p]
        if len(slice_) == 0:
            continue
        history.append(_kri_block(slice_, p))

    if not history:
        # No rows survived the mask — return an empty current block at latest.
        empty = _kri_block(scoped.iloc[0:0], periods[-1])
        return GroupingHistory(current=empty, history=[])

    return GroupingHistory(current=history[-1], history=history)


def _normalize_dim(value) -> str:
    """Normalize a dim value (industry / segment / branch).

    NaN, None, blank, whitespace-only, and the literal "nan" string all
    collapse to a single "Unclassified" bucket. This is what makes the
    Σ by_X[*].committed == firm_level.committed reconciliation hold —
    rows with a missing dim value used to be silently dropped from
    every bucket while still contributing to the firm total.
    """
    if pd.isna(value):
        return "Unclassified"
    token = str(value).strip()
    if not token or token.lower() == "nan":
        return "Unclassified"
    return token


def _grouping_by_dim(
    df: pd.DataFrame,
    periods: list,
    column: str,
) -> dict[str, GroupingHistory]:
    """Build a GroupingHistory per distinct value of `column`.

    Blank/NaN values are collapsed into a single "Unclassified" bucket
    via `_normalize_dim` so that the per-bucket sums reconcile to the
    firm total. "Unclassified" sorts alphabetically like any other
    string and is rendered as a normal bucket — its presence is the
    upstream-data-quality signal.
    """
    result: dict[str, GroupingHistory] = {}
    if column not in df.columns:
        return result
    normalized = df[column].apply(_normalize_dim)
    values = normalized.unique()
    for v in sorted(values, key=str):
        mask = normalized == v
        result[v] = _grouping_history(df, periods, mask=mask)
    return result


def _check_dim_reconciliation(
    section_name: str,
    sections: dict[str, GroupingHistory],
    firm_committed: float,
) -> Optional[dict]:
    """Verify Σ section[*].current.totals.committed ≈ firm committed.

    Returns a warning dict when the gap exceeds
    EXPOSURE_VALIDATION_TOLERANCE; None otherwise. A non-None result
    means rows are being lost between firm-level aggregation and
    bucketing — the `_normalize_dim` fix should prevent this for
    NaN dim values, so a regression here means a new code path is
    dropping rows.
    """
    if not sections:
        return None
    section_sum = sum(h.current.totals.committed for h in sections.values())
    diff = section_sum - firm_committed
    if abs(diff) <= EXPOSURE_VALIDATION_TOLERANCE:
        return None
    log.warning(
        "%s does not reconcile to firm-level committed: "
        "section_sum=%.2f, firm=%.2f, diff=%.2f",
        section_name, section_sum, firm_committed, diff,
    )
    return {
        "code": "dim_reconciliation_failed",
        "section": section_name,
        "firm_total": firm_committed,
        "section_sum": section_sum,
        "diff": diff,
    }


def _watchlist_aggregate(latest_df: pd.DataFrame, latest_period) -> WatchlistAggregate:
    if "Credit Watch List Flag" not in latest_df.columns:
        return WatchlistAggregate(period=_to_date(latest_period))
    flag = latest_df["Credit Watch List Flag"].astype(str).str.strip().str.upper()
    mask = flag == "Y"
    sub = latest_df[mask]
    return WatchlistAggregate(
        period=_to_date(latest_period),
        facility_count=int(sub["Facility ID"].nunique()),
        committed=float(sub["Committed Exposure"].sum()) if len(sub) else 0.0,
        outstanding=float(sub["Outstanding Exposure"].sum()) if len(sub) else 0.0,
    )


# ── Top contributors (parent-level, latest period) ────────────

def _top_contributors(latest_df: pd.DataFrame, latest_period) -> ContributorBlock:
    if len(latest_df) == 0:
        return ContributorBlock(period=_to_date(latest_period))

    grouped = (
        latest_df
        .groupby("Ultimate Parent Code", dropna=False)
        .agg(
            entity_name     = ("Ultimate Parent Name", "first"),
            committed       = ("Committed Exposure", "sum"),
            outstanding     = ("Outstanding Exposure", "sum"),
            wapd_numerator  = ("Weighted Average PD Numerator", "sum"),
            walgd_numerator = ("Weighted Average LGD Numerator", "sum"),
            sm              = ("Special Mention Rated Exposure", "sum"),
            ss              = ("Substandard Rated Exposure", "sum"),
            dbt             = ("Doubtful Rated Exposure", "sum"),
            loss_           = ("Loss Rated Exposure", "sum"),
        )
        .reset_index()
    )
    grouped["cc_exposure"] = grouped[["sm", "ss", "dbt", "loss_"]].sum(axis=1)

    def _to_contribs(sorted_df: pd.DataFrame) -> list[Contributor]:
        out: list[Contributor] = []
        for _, row in sorted_df.head(TOP_N_CONTRIBUTORS).iterrows():
            out.append(Contributor(
                entity_id      = str(row["Ultimate Parent Code"]),
                entity_name    = (str(row["entity_name"]) if pd.notna(row["entity_name"]) else None),
                committed      = float(row["committed"]),
                outstanding    = float(row["outstanding"]),
                wapd_numerator = float(row["wapd_numerator"]),
                walgd_numerator= float(row["walgd_numerator"]),
                cc_exposure    = float(row["cc_exposure"]),
            ))
        return out

    # Ties on the primary metric are broken by Ultimate Parent Code (ascending)
    # so slicer re-runs against the same workbook produce identical orderings —
    # required for follow-up correctness (verifiable_values must match).
    def _ranked(by: str) -> pd.DataFrame:
        return grouped.sort_values(
            [by, "Ultimate Parent Code"], ascending=[False, True]
        )

    return ContributorBlock(
        period               = _to_date(latest_period),
        by_committed         = _to_contribs(_ranked("committed")),
        by_outstanding       = _to_contribs(_ranked("outstanding")),
        by_wapd_contribution = _to_contribs(_ranked("wapd_numerator")),
        by_cc_exposure       = _to_contribs(_ranked("cc_exposure")),
    )


# ── Facility-level WAPD contributors ──────────────────────────
#
# Returns the top-N facilities by `Weighted Average PD Numerator`
# within the supplied DataFrame slice. The numerator equals
# PD × Committed Exposure for the loan, so the largest values are
# the loans pulling WAPD up the most.
#
# share_of_numerator is computed against the SCOPE total, not the
# firm total — when called with a horizontal-portfolio slice, shares
# express each facility's contribution within that portfolio.

def _top_wapd_facility_contributors(
    scope_df: pd.DataFrame,
    n: int = TOP_N_WAPD_FACILITIES,
) -> list[FacilityContributor]:
    if len(scope_df) == 0 or "Weighted Average PD Numerator" not in scope_df.columns:
        return []

    # Aggregate to facility level (a facility could appear on multiple rows
    # in edge cases; .groupby(...).sum() collapses safely on a single-row
    # facility too).
    grouped = (
        scope_df
        .groupby("Facility ID", dropna=False)
        .agg(
            facility_name      = ("Facility Name", "first"),
            parent_name        = ("Ultimate Parent Name", "first"),
            committed          = ("Committed Exposure", "sum"),
            wapd_numerator     = ("Weighted Average PD Numerator", "sum"),
            pd_rating          = ("PD Rating", "first"),
            regulatory_rating  = ("Regulatory Rating", "first"),
        )
        .reset_index()
    )

    scope_total_numerator = float(grouped["wapd_numerator"].sum())

    # Secondary sort on Facility ID (ascending) stabilises ties on WAPD
    # numerator so follow-up re-runs produce identical top-N ordering.
    grouped = grouped.sort_values(
        ["wapd_numerator", "Facility ID"], ascending=[False, True]
    ).head(n)

    out: list[FacilityContributor] = []
    for _, row in grouped.iterrows():
        committed     = float(row["committed"])
        numerator     = float(row["wapd_numerator"])
        implied_pd    = (numerator / committed) if committed else None
        share         = (numerator / scope_total_numerator) if scope_total_numerator else None
        out.append(FacilityContributor(
            facility_id        = str(row["Facility ID"]),
            facility_name      = (str(row["facility_name"]) if pd.notna(row["facility_name"]) else None),
            parent_name        = (str(row["parent_name"]) if pd.notna(row["parent_name"]) else None),
            committed          = committed,
            wapd_numerator     = numerator,
            implied_pd         = implied_pd,
            pd_rating          = (str(row["pd_rating"]) if pd.notna(row["pd_rating"]) else None),
            regulatory_rating  = (str(row["regulatory_rating"]) if pd.notna(row["regulatory_rating"]) else None),
            share_of_numerator = share,
        ))
    return out


# ── Month-over-month derivations ──────────────────────────────

def _month_over_month(
    *,
    df_prior: pd.DataFrame,
    df_current: pd.DataFrame,
    prior_period,
    current_period,
) -> MomBlock:
    prior_facs   = set(df_prior["Facility ID"].dropna().astype(str))
    current_facs = set(df_current["Facility ID"].dropna().astype(str))

    new_ids   = current_facs - prior_facs
    exit_ids  = prior_facs - current_facs

    new_originations = _facility_changes(df_current, new_ids)
    exits            = _facility_changes(df_prior,   exit_ids)

    parent_entrants = sorted(
        set(df_current["Ultimate Parent Code"].dropna().astype(str))
        - set(df_prior["Ultimate Parent Code"].dropna().astype(str))
    )
    parent_exits = sorted(
        set(df_prior["Ultimate Parent Code"].dropna().astype(str))
        - set(df_current["Ultimate Parent Code"].dropna().astype(str))
    )

    # Persistent facilities — join on Facility ID for change detection.
    common = prior_facs & current_facs
    pd_changes  = _pd_rating_changes(df_prior, df_current, common)
    reg_changes = _reg_rating_changes(df_prior, df_current, common)

    movers = _exposure_movers(df_prior, df_current, common)

    return MomBlock(
        prior_period         = _to_date(prior_period),
        current_period       = _to_date(current_period),
        new_originations     = new_originations,
        exits                = exits,
        parent_entrants      = parent_entrants,
        parent_exits         = parent_exits,
        pd_rating_changes    = pd_changes,
        reg_rating_changes   = reg_changes,
        top_exposure_movers  = movers,
    )


def _facility_changes(df: pd.DataFrame, ids: set[str]) -> list[FacilityChange]:
    if not ids:
        return []
    sub = df[df["Facility ID"].astype(str).isin(ids)]
    out: list[FacilityChange] = []
    for fid, group in sub.groupby("Facility ID"):
        row = group.iloc[0]
        out.append(FacilityChange(
            facility_id   = str(fid),
            facility_name = str(row.get("Facility Name", "")) or None,
            parent_name   = str(row.get("Ultimate Parent Name", "")) or None,
            committed     = float(group["Committed Exposure"].sum()),
            outstanding   = float(group["Outstanding Exposure"].sum()),
        ))
    # Explicit tiebreaker on facility_id (ascending) — Python sort is stable,
    # but declaring the secondary key makes re-run determinism obvious at the
    # call site and survives future refactors of the upstream groupby.
    out.sort(key=lambda c: (-c.committed, c.facility_id))
    return out


def _pd_rating_changes(
    df_prior: pd.DataFrame,
    df_current: pd.DataFrame,
    common_ids: set[str],
) -> list[RatingChange]:
    if not common_ids:
        return []
    p = df_prior[df_prior["Facility ID"].astype(str).isin(common_ids)] \
        .groupby("Facility ID").first()[["PD Rating"]]
    c = df_current[df_current["Facility ID"].astype(str).isin(common_ids)] \
        .groupby("Facility ID").first()[["PD Rating", "Facility Name", "Ultimate Parent Name"]]

    joined = c.join(p, lsuffix="_current", rsuffix="_prior", how="inner")

    out: list[RatingChange] = []
    for fid, row in joined.iterrows():
        prior   = row.get("PD Rating_prior")
        current = row.get("PD Rating_current")
        if pd.isna(prior) or pd.isna(current):
            continue
        if str(prior).strip().upper() == str(current).strip().upper():
            continue
        out.append(RatingChange(
            facility_id   = str(fid),
            facility_name = (str(row.get("Facility Name")) if pd.notna(row.get("Facility Name")) else None),
            parent_name   = (str(row.get("Ultimate Parent Name")) if pd.notna(row.get("Ultimate Parent Name")) else None),
            prior         = str(prior),
            current       = str(current),
            direction     = pd_scale.direction(str(prior), str(current)),
        ))
    return out


def _reg_rating_changes(
    df_prior: pd.DataFrame,
    df_current: pd.DataFrame,
    common_ids: set[str],
) -> list[RatingChange]:
    if not common_ids:
        return []
    cols = ["Regulatory Rating", "Facility Name", "Ultimate Parent Name"]
    p = df_prior[df_prior["Facility ID"].astype(str).isin(common_ids)] \
        .groupby("Facility ID").first()[["Regulatory Rating"]]
    c = df_current[df_current["Facility ID"].astype(str).isin(common_ids)] \
        .groupby("Facility ID").first()[cols]

    joined = c.join(p, lsuffix="_current", rsuffix="_prior", how="inner")

    out: list[RatingChange] = []
    for fid, row in joined.iterrows():
        prior   = row.get("Regulatory Rating_prior")
        current = row.get("Regulatory Rating_current")
        if reg_rating.equals(prior, current):
            continue
        out.append(RatingChange(
            facility_id   = str(fid),
            facility_name = (str(row.get("Facility Name")) if pd.notna(row.get("Facility Name")) else None),
            parent_name   = (str(row.get("Ultimate Parent Name")) if pd.notna(row.get("Ultimate Parent Name")) else None),
            prior         = (str(prior)   if pd.notna(prior)   else None),
            current       = (str(current) if pd.notna(current) else None),
            direction     = reg_rating.direction(prior, current),
        ))
    return out


def _exposure_movers(
    df_prior: pd.DataFrame,
    df_current: pd.DataFrame,
    common_ids: set[str],
) -> list[ExposureMover]:
    if not common_ids:
        return []
    p = (df_prior[df_prior["Facility ID"].astype(str).isin(common_ids)]
         .groupby("Facility ID")["Committed Exposure"].sum())
    c = (df_current[df_current["Facility ID"].astype(str).isin(common_ids)]
         .groupby("Facility ID")[["Committed Exposure", "Facility Name", "Ultimate Parent Name"]]
         .agg({"Committed Exposure": "sum",
               "Facility Name": "first",
               "Ultimate Parent Name": "first"}))
    c["prior"] = p
    c["delta"] = c["Committed Exposure"] - c["prior"].fillna(0)
    # Explicit secondary sort on Facility ID — relying on groupby's default
    # sort=True + stable sort would also produce deterministic ties today,
    # but the explicit key documents the contract and survives upstream
    # refactors (e.g. anyone adding sort=False for performance).
    c = c.assign(abs_delta=c["delta"].abs())
    c = (
        c.reset_index()
         .sort_values(["abs_delta", "Facility ID"], ascending=[False, True])
         .set_index("Facility ID")
    )

    out: list[ExposureMover] = []
    for fid, row in c.head(TOP_N_EXPOSURE_MOVERS).iterrows():
        out.append(ExposureMover(
            facility_id       = str(fid),
            facility_name     = (str(row.get("Facility Name")) if pd.notna(row.get("Facility Name")) else None),
            parent_name       = (str(row.get("Ultimate Parent Name")) if pd.notna(row.get("Ultimate Parent Name")) else None),
            prior_committed   = float(row.get("prior", 0) or 0),
            current_committed = float(row["Committed Exposure"]),
            delta_committed   = float(row["delta"]),
        ))
    return out


# ── Misc ──────────────────────────────────────────────────────

def _to_date(val):
    """Coerce a numpy/pandas datetime-ish value to a python date."""
    ts = pd.Timestamp(val)
    return ts.date()
