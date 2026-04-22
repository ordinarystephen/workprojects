# ── KRONOS · pipeline/cube/models.py ──────────────────────────
# Cube data model.
#
# A "cube" is the deterministic JSON the calculation layer produces
# once per upload. Slicers then pick portions of the cube to send to
# the LLM for narration. The cube holds enough information to answer
# every supported analysis mode without recomputing anything.
#
# Two halves:
#   - Generic blocks (KriBlock, ContributorBlock, MomBlock, ...)
#     used by every template's cube.
#   - Template-specific cube classes (LendingCube, ...) that wire
#     the generic blocks together with the right keys.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


# ── KRI sub-blocks ────────────────────────────────────────────

class ExposureTotals(BaseModel):
    """Sum-able $ totals at any grouping (firm, industry, horizontal, etc.)."""
    model_config = ConfigDict(extra="forbid")

    approved_limit:           float = 0.0
    committed:                float = 0.0
    outstanding:              float = 0.0
    temporary:                float = 0.0
    take_and_hold:            float = 0.0

    pass_rated:               float = 0.0
    special_mention:          float = 0.0
    substandard:              float = 0.0
    doubtful:                 float = 0.0
    loss:                     float = 0.0
    no_regulatory_rating:     float = 0.0

    criticized_classified:    float = 0.0  # SM + SS + Dbt + L
    cc_pct_of_commitment:     Optional[float] = None  # cc / committed (None if committed == 0)

    # Validation: pass + sm + ss + dbt + l + no_reg should ≈ committed.
    validation_diff:          float = 0.0  # signed diff in dollars
    validation_ok:            bool  = True # |diff| ≤ tolerance


class WeightedAverage(BaseModel):
    """Generic weighted-average with optional rating-scale display."""
    model_config = ConfigDict(extra="forbid")

    raw:        Optional[float] = None  # numerator / denominator (decimal)
    display:    Optional[str]   = None  # rendered string (e.g. "C06" or "42.00%")
    numerator:  float = 0.0
    denominator: float = 0.0


class Counts(BaseModel):
    """Distinct-entity counts."""
    model_config = ConfigDict(extra="forbid")

    parents:    int = 0
    partners:   int = 0
    facilities: int = 0
    industries: int = 0


class HorizontalAggregate(BaseModel):
    """Watchlist-style firm-level filter aggregate (NOT a full sub-cube)."""
    model_config = ConfigDict(extra="forbid")

    name:               str
    facility_count:     int = 0
    committed:          float = 0.0
    outstanding:        float = 0.0


class KriBlock(BaseModel):
    """All KRIs for a single grouping at a single period."""
    model_config = ConfigDict(extra="forbid")

    period:            date
    totals:            ExposureTotals
    counts:            Counts
    wapd:              WeightedAverage
    walgd:             WeightedAverage


class GroupingHistory(BaseModel):
    """A single grouping (firm-level, one industry, one horizontal portfolio)
    with its current period plus historical periods if multiple were uploaded."""
    model_config = ConfigDict(extra="forbid")

    current:  KriBlock
    history:  list[KriBlock] = Field(default_factory=list)


# ── Top contributors ──────────────────────────────────────────

class Contributor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id:      str
    entity_name:    Optional[str] = None
    committed:      float = 0.0
    outstanding:    float = 0.0
    wapd_numerator: float = 0.0
    walgd_numerator:float = 0.0
    cc_exposure:    float = 0.0


class ContributorBlock(BaseModel):
    """Top contributors per metric, computed at the parent (top-of-hierarchy) level."""
    model_config = ConfigDict(extra="forbid")

    period:                 date
    by_committed:           list[Contributor] = Field(default_factory=list)
    by_outstanding:         list[Contributor] = Field(default_factory=list)
    by_wapd_contribution:   list[Contributor] = Field(default_factory=list)
    by_cc_exposure:         list[Contributor] = Field(default_factory=list)


# ── Facility-level WAPD contributors ──────────────────────────
#
# Per-loan view of which facilities are driving the weighted-average
# PD. Numerator = PD × Committed Exposure for the facility.
# share_of_numerator = facility_numerator / scope_total_numerator,
# where "scope" is whatever the list was computed within
# (firm-level, a horizontal portfolio, etc.).

class FacilityContributor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id:        str
    facility_name:      Optional[str] = None
    parent_name:        Optional[str] = None
    committed:          float = 0.0
    wapd_numerator:     float = 0.0     # PD × Committed for this facility
    implied_pd:         Optional[float] = None  # numerator / committed (decimal)
    pd_rating:          Optional[str] = None
    regulatory_rating:  Optional[str] = None
    share_of_numerator: Optional[float] = None  # this facility's share of scope total


# ── Month-over-month ──────────────────────────────────────────

class RatingChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id:   str
    facility_name: Optional[str]  = None
    parent_name:   Optional[str]  = None
    prior:         Optional[str]  = None
    current:       Optional[str]  = None
    direction:     Optional[str]  = None  # "upgrade" | "downgrade" | "unchanged"


class FacilityChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id:   str
    facility_name: Optional[str] = None
    parent_name:   Optional[str] = None
    committed:     float = 0.0
    outstanding:   float = 0.0


class ExposureMover(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id:    str
    facility_name:  Optional[str] = None
    parent_name:    Optional[str] = None
    prior_committed:    float = 0.0
    current_committed:  float = 0.0
    delta_committed:    float = 0.0


class MomBlock(BaseModel):
    """All cross-period derivations between two adjacent periods."""
    model_config = ConfigDict(extra="forbid")

    prior_period:        date
    current_period:      date

    new_originations:    list[FacilityChange] = Field(default_factory=list)
    exits:               list[FacilityChange] = Field(default_factory=list)
    parent_entrants:     list[str] = Field(default_factory=list)
    parent_exits:        list[str] = Field(default_factory=list)

    pd_rating_changes:   list[RatingChange] = Field(default_factory=list)
    reg_rating_changes:  list[RatingChange] = Field(default_factory=list)

    top_exposure_movers: list[ExposureMover] = Field(default_factory=list)


# ── Cube metadata ─────────────────────────────────────────────

class CubeMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template:       str
    as_of:          date                           # latest period in the file
    periods:        list[date]                     # all periods, sorted ascending
    row_count:      int
    file_hash:      Optional[str] = None           # for caching, populated by caller
    warnings:       list[dict]    = Field(default_factory=list)


# ── Watchlist firm-level aggregate ────────────────────────────

class WatchlistAggregate(BaseModel):
    """Firm-level Watchlist tile (NOT a horizontal portfolio per requirements)."""
    model_config = ConfigDict(extra="forbid")

    period:           date
    facility_count:   int = 0
    committed:        float = 0.0
    outstanding:      float = 0.0


# ── Distressed sub-stat (subset of Non-Investment Grade) ──────
#
# C13 facilities are part of the NIG bucket by rating, AND tracked
# separately as Distressed by state. Reported in parallel to (not
# inside) `by_ig_status["Non-Investment Grade"]` so KriBlock /
# GroupingHistory stay uniform across all top-level buckets — see
# the "Option C" decision in the rating-category refactor.
#
# Only the latest period is computed (no history) to keep the
# parallel-field approach simple; expand if a downstream slicer
# needs distressed-over-time.

class DistressedSubstats(BaseModel):
    """Latest-period totals for the C13 subset of NIG."""
    model_config = ConfigDict(extra="forbid")

    period:         date
    committed:      float = 0.0
    outstanding:    float = 0.0
    facility_count: int   = 0


# ── Per-slice rating composition (Part 1 structure, scoped) ──
#
# The firm-level cube exposes the five-category breakdown via four
# parallel dicts (by_ig_status / by_defaulted / by_non_rated) plus
# nig_distressed_substats. For per-industry and per-horizontal slices
# the same shape is bundled into one RatingComposition for ergonomics
# — slicers iterate one container instead of five.
#
# Any of the four GroupingHistory fields is None when the slice has
# zero rows in that bucket (rather than an empty GroupingHistory).
# distressed_substats is None when the slice has no C13 rows in the
# latest period.

class RatingComposition(BaseModel):
    """Five-category rating breakdown for one slice (industry / horizontal)."""
    model_config = ConfigDict(extra="forbid")

    investment_grade:     Optional[GroupingHistory] = None  # C00..C07
    non_investment_grade: Optional[GroupingHistory] = None  # C08..C13
    defaulted:            Optional[GroupingHistory] = None  # CDF
    non_rated:            Optional[GroupingHistory] = None  # placeholder values
    distressed_substats:  Optional[DistressedSubstats] = None  # C13 subset of NIG


# ── Portfolio slice (industry or horizontal) ─────────────────
#
# Container for everything an industry-level or horizontal-level
# slicer needs about its scope: the period-level KRIs (grouping),
# the rating-category breakdown, the parent-level top contributors,
# the watchlist aggregate, and the facility-level WAPD drivers
# computed within that scope.
#
# `name` is the slice's display label (the industry name or the
# horizontal portfolio name). It matches the key in the cube's
# industry_details / horizontal_details dict — duplicated on the
# slice so the slicer can use it without threading the key.

class PortfolioSlice(BaseModel):
    """All cube data scoped to a single industry or horizontal portfolio."""
    model_config = ConfigDict(extra="forbid")

    name:                str
    grouping:            "GroupingHistory"
    rating_composition:  RatingComposition
    top_contributors:    "ContributorBlock"
    watchlist:           "WatchlistAggregate"
    top_wapd_facilities: list["FacilityContributor"] = Field(default_factory=list)


# ── The Lending cube itself ───────────────────────────────────

class LendingCube(BaseModel):
    """Full deterministic output of the Lending pipeline for one upload."""
    model_config = ConfigDict(extra="forbid")

    metadata:       CubeMetadata

    firm_level:     GroupingHistory
    by_industry:    dict[str, GroupingHistory] = Field(default_factory=dict)
    by_segment:     dict[str, GroupingHistory] = Field(default_factory=dict)
    by_branch:      dict[str, GroupingHistory] = Field(default_factory=dict)
    by_horizontal:  dict[str, GroupingHistory] = Field(default_factory=dict)
    by_ig_status:   dict[str, GroupingHistory] = Field(default_factory=dict)
        # Keys: "Investment Grade" (C00..C07) / "Non-Investment Grade"
        # (C08..C13). C13 is included in NIG; surfaced separately via
        # `nig_distressed_substats`. CDF is NOT in NIG — see
        # `by_defaulted` below.

    by_defaulted:   dict[str, GroupingHistory] = Field(default_factory=dict)
        # Top-level peer to IG/NIG. Single key "Defaulted" populated
        # iff any facility has PD Rating == CDF in the latest period.

    by_non_rated:   dict[str, GroupingHistory] = Field(default_factory=dict)
        # Top-level peer. Single key "Non-Rated" populated iff any
        # facility's PD Rating is a placeholder (TBR/NTR/Unrated/NA/
        # N/A/#REF/blank) — see pd_scale.NON_RATED_TOKENS.

    nig_distressed_substats: Optional[DistressedSubstats] = None
        # Latest-period sub-stat for the C13 subset of NIG. Populated
        # iff the NIG bucket has any C13 rows. None when NIG is empty
        # or has no Distressed facilities.

    watchlist:      WatchlistAggregate

    top_contributors: ContributorBlock

    # Facility-level WAPD drivers (latest period).
    # firm-level list, plus per-horizontal-portfolio lists.
    top_wapd_facility_contributors: list[FacilityContributor] = Field(default_factory=list)
    wapd_contributors_by_horizontal: dict[str, list[FacilityContributor]] = Field(default_factory=dict)

    # ── Per-slice composites (industry, horizontal) ───────────
    # Rich slice containers for industry-level and horizontal-level
    # slicers. Each PortfolioSlice carries the slice's KRI grouping,
    # rating-category composition (Part 1 structure), top parents,
    # watchlist aggregate, and facility-level WAPD drivers.
    #
    # The flat by_industry / by_horizontal dicts above are kept
    # alongside these so the firm-level and portfolio-summary
    # slicers (which only need a GroupingHistory) don't have to
    # navigate the richer structure. The two views are computed
    # from the same masks, so per-slice committed totals match.
    industry_details:   dict[str, PortfolioSlice] = Field(default_factory=dict)
    horizontal_details: dict[str, PortfolioSlice] = Field(default_factory=dict)

    # Populated only when the file contains ≥ 2 periods.
    month_over_month: Optional[MomBlock] = None

    # ── Parameter-picker sources ──────────────────────────────
    # Exposed for the mode registry. A YAML parameter declaration
    # like `source: cube.available_industries` resolves here. The
    # legacy `available_portfolios` was ambiguous once horizontal
    # portfolios joined industry portfolios as a peer concept —
    # picker sources now name the kind explicitly.

    @property
    def available_industries(self) -> list[str]:
        """Industry-portfolio names (Risk Assessment Industry partition)."""
        return sorted(self.by_industry.keys())

    @property
    def available_horizontals(self) -> list[str]:
        """Horizontal-portfolio names (boolean flag overlays)."""
        return sorted(self.by_horizontal.keys())
