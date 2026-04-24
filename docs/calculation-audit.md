# KRONOS — Deterministic Calculation Audit

> Audit of everything computed in `pipeline/cube/` and surfaced by the slicers in
> `pipeline/processors/lending/`. This is a *read-only* reference — no code is
> changed here. Findings are documented but not fixed.
>
> **Scope:** Lending template only. Traded Products / other templates are not
> wired.
>
> **Sources consulted:** `pipeline/cube/models.py`, `pipeline/cube/lending.py`,
> `pipeline/templates/lending.py`, `pipeline/templates/base.py`,
> `pipeline/scales/pd_scale.py`, `pipeline/parsers/regulatory_rating.py`,
> `pipeline/loaders/classifier.py`, `pipeline/processors/lending/firm_level.py`,
> `pipeline/processors/lending/portfolio_summary.py`, `pipeline/registry.py`,
> `config/modes.yaml`, `config/prompts/*.md`.
>
> **Date:** 2026-04-22
>
> **Amendment (Round 19, 2026-04-24):** the `portfolio_summary` slicer
> referenced throughout this audit was deprecated and removed in
> Round 19 as part of the Scope × Length refactor — the executive
> summary view is now produced by running the `firm_level` mode with
> the request-level `length` field set to `executive`. References
> below to `portfolio_summary` (as a slicer, prompt template, mode
> slug, or set of `verifiable_values` labels) reflect the codebase
> *as of 2026-04-22* and have been preserved verbatim because this
> document is a point-in-time historical audit. Findings about
> `portfolio_summary`-only labels (e.g. `(% of rated commitment)`,
> `(% of total commitment)`, `(% of NIG)`, `New originations` count,
> `Exits` count, etc.) no longer apply to any active slicer; consult
> [available-kris.md](available-kris.md) for the current label set.

---

## 1. Overview of the cube

One Lending upload produces one [`LendingCube`](../pipeline/cube/models.py#L227)
via [`compute_lending_cube(df)`](../pipeline/cube/lending.py#L59).

```
LendingCube
├── metadata                              CubeMetadata
├── firm_level                            GroupingHistory  (no filter)
├── by_industry      : dict[str, …]       GroupingHistory per Risk Assessment Industry
├── by_segment       : dict[str, …]       GroupingHistory per Portfolio Segment Description
├── by_branch        : dict[str, …]       GroupingHistory per UBS Branch Name
├── by_horizontal    : dict[str, …]       GroupingHistory per horizontal flag
│                                         ("Leveraged Finance", "Global Recovery Management")
├── by_ig_status     : dict[str, …]       GroupingHistory per {"Investment Grade","Non-Investment Grade"}
├── watchlist                             WatchlistAggregate  (firm-level, latest period)
├── top_contributors                      ContributorBlock    (parent-level, latest period)
├── top_wapd_facility_contributors        list[FacilityContributor]  (firm-level, latest)
├── wapd_contributors_by_horizontal       dict[str, list[FacilityContributor]]
└── month_over_month                      Optional[MomBlock]  (only when ≥ 2 periods)
```

Every `GroupingHistory` is `{ current: KriBlock, history: list[KriBlock] }`. The
`KriBlock` itself is `{ period, totals, counts, wapd, walgd }`.

**Model:** `GroupingHistory` is a uniform container — all seven groupings
(firm-level, by_industry, by_segment, by_branch, by_horizontal, by_ig_status)
carry the *same* KRIs. That means the cube is internally consistent about what
a "section" provides, even when slicers only render a subset.

Two things are NOT in the `KriBlock`:

- **watchlist sub-aggregate** — only a firm-level `WatchlistAggregate` exists
  (period, facility_count, committed, outstanding).
- **top contributors** — a single firm-level `ContributorBlock` plus a single
  firm-level facility-WAPD list; no per-industry / per-horizontal contributor
  lists.

A declared-but-unused model sits in `cube/models.py`:
[`HorizontalAggregate`](../pipeline/cube/models.py#L71) (name, facility_count,
committed, outstanding) is never written to any field of `LendingCube`.

---

## 2. Section inventory

| Cube field | Type | Populated by | What it covers |
|---|---|---|---|
| `metadata` | `CubeMetadata` | [lending.py:142](../pipeline/cube/lending.py#L142) | template name, `as_of` (latest period), all periods list, row_count |
| `firm_level` | `GroupingHistory` | [`_grouping_history(df, periods, mask=None)`](../pipeline/cube/lending.py#L79) | full portfolio, latest + history |
| `by_industry` | `dict[str, GroupingHistory]` | [`_grouping_by_dim(df, periods, "Risk Assessment Industry")`](../pipeline/cube/lending.py#L82) | one entry per distinct industry |
| `by_segment` | `dict[str, GroupingHistory]` | [lending.py:83](../pipeline/cube/lending.py#L83) | one entry per `Portfolio Segment Description` |
| `by_branch` | `dict[str, GroupingHistory]` | [lending.py:84](../pipeline/cube/lending.py#L84) | one entry per `UBS Branch Name` |
| `by_horizontal` | `dict[str, GroupingHistory]` | [lending.py:87-95](../pipeline/cube/lending.py#L87-L95) | one entry per template `horizontal_flag` field that has ≥ 1 matching row |
| `by_ig_status` | `dict[str, GroupingHistory]` | [lending.py:98-104](../pipeline/cube/lending.py#L98-L104) | `{Investment Grade, Non-Investment Grade}`, derived from `PD Rating` |
| `watchlist` | `WatchlistAggregate` | [`_watchlist_aggregate(latest_df, latest_period)`](../pipeline/cube/lending.py#L304-L315) | firm-level, latest period only |
| `top_contributors` | `ContributorBlock` | [`_top_contributors(latest_df, latest_period)`](../pipeline/cube/lending.py#L320-L370) | parent-level top-10 by committed / outstanding / wapd_numerator / cc_exposure |
| `top_wapd_facility_contributors` | `list[FacilityContributor]` | [`_top_wapd_facility_contributors(latest_df)`](../pipeline/cube/lending.py#L384-L433) | facility-level top-10 by WAPD numerator, firm scope |
| `wapd_contributors_by_horizontal` | `dict[str, list[FacilityContributor]]` | [lending.py:114-129](../pipeline/cube/lending.py#L114-L129) | facility-level top-10 per horizontal portfolio |
| `month_over_month` | `Optional[MomBlock]` | [`_month_over_month(df_prior, df_current, …)`](../pipeline/cube/lending.py#L438-L480) | only when `len(periods) >= 2`; compares `periods[-1]` vs `periods[-2]` |

**Horizontal flags registered in the template:**

| Column | Trigger | Portfolio name |
|---|---|---|
| `Leveraged Finance Flag` | `"Y"` | `Leveraged Finance` |
| `Global Recovery Management Flag` | `"Directly Managed"` | `Global Recovery Management` |

`Credit Watch List Flag` is explicitly **not** a horizontal portfolio (commented
in [lending.py:82](../pipeline/templates/lending.py#L82) as "intentionally NOT a
horizontal — surfaced as a firm-level aggregate tile only").

---

## 3. KRI inventory per section

Every `KriBlock` (firm-level, each industry, each segment, each branch, each
horizontal portfolio, IG, NIG) computes the same set. Sourced from
[`_kri_block`](../pipeline/cube/lending.py#L167-L189).

### 3.1 Totals (`ExposureTotals`)

| KRI | Computed | Source |
|---|---|---|
| `approved_limit` | [x] | [`_exposure_totals`](../pipeline/cube/lending.py#L211) `Approved Limit`.sum() |
| `committed` | [x] | [`_exposure_totals`](../pipeline/cube/lending.py#L203) `Committed Exposure`.sum() |
| `outstanding` | [x] | [lending.py:213](../pipeline/cube/lending.py#L213) `Outstanding Exposure`.sum() |
| `temporary` | [x] | [lending.py:214](../pipeline/cube/lending.py#L214) `Temporary Exposure`.sum() |
| `take_and_hold` | [x] | [lending.py:215](../pipeline/cube/lending.py#L215) `Take & Hold Exposure`.sum() |
| `pass_rated` | [x] | [lending.py:216](../pipeline/cube/lending.py#L216) `Pass Rated Exposure`.sum() |
| `special_mention` | [x] | [lending.py:217](../pipeline/cube/lending.py#L217) `Special Mention Rated Exposure`.sum() |
| `substandard` | [x] | [lending.py:218](../pipeline/cube/lending.py#L218) `Substandard Rated Exposure`.sum() |
| `doubtful` | [x] | [lending.py:219](../pipeline/cube/lending.py#L219) `Doubtful Rated Exposure`.sum() |
| `loss` | [x] | [lending.py:220](../pipeline/cube/lending.py#L220) `Loss Rated Exposure`.sum() |
| `no_regulatory_rating` | [x] | [lending.py:221](../pipeline/cube/lending.py#L221) `No Regulatory Rating Exposure`.sum() |
| `criticized_classified` (SM+SS+Dbt+L) | [x] | [lending.py:202](../pipeline/cube/lending.py#L202) |
| `cc_pct_of_commitment` | [x] | [lending.py:205](../pipeline/cube/lending.py#L205) (None if committed==0) |
| `validation_diff` (Σ rating buckets − committed) | [x] | [lending.py:208](../pipeline/cube/lending.py#L208) |
| `validation_ok` (\|diff\| ≤ $2) | [x] | [lending.py:225](../pipeline/cube/lending.py#L225), tolerance [lending.py:45](../pipeline/cube/lending.py#L45) |

### 3.2 Counts (`Counts`)

| KRI | Computed | Source |
|---|---|---|
| `parents` (distinct Ultimate Parent Code) | [x] | [`_counts`](../pipeline/cube/lending.py#L231) |
| `partners` (distinct Partner Code) | [x] | [lending.py:232](../pipeline/cube/lending.py#L232) |
| `facilities` (distinct Facility ID) | [x] | [lending.py:233](../pipeline/cube/lending.py#L233) |
| `industries` (distinct Risk Assessment Industry) | [x] | [lending.py:234](../pipeline/cube/lending.py#L234) |
| distinct UBS branches | [ ] | not computed (available via `len(by_branch)`) |
| distinct Portfolio Segments | [ ] | not computed (available via `len(by_segment)`) |

### 3.3 Weighted averages (`WeightedAverage`)

| KRI | Numerator column | Denominator column | Display | Source |
|---|---|---|---|---|
| `wapd` (Weighted Avg PD) | `Weighted Average PD Numerator` | `Committed Exposure` | `rating_code` via `pd_scale` | [`_weighted_average`](../pipeline/cube/lending.py#L238-L259) |
| `walgd` (Weighted Avg LGD) | `Weighted Average LGD Numerator` | `Committed Exposure` | `percent_2dp` | same |

Both produce `{ raw, display, numerator, denominator }`. `raw` is the decimal;
`display` is the rendered string the LLM cites verbatim.

### 3.4 What slicers actually surface

Cross-reference between what the cube *computes* and what each slicer *sends to
the LLM / renders as tiles*.

| KRI | `firm_level.py` | `portfolio_summary.py` |
|---|---|---|
| `totals.approved_limit` | [x] context+verif | [ ] |
| `totals.committed` | [x] everything | [x] everything |
| `totals.outstanding` | [x] everything | [x] everything |
| `totals.temporary` | [x] | [ ] |
| `totals.take_and_hold` | [x] | [ ] |
| `totals.pass_rated` | [x] | [ ] |
| `totals.special_mention` | [x] | [ ] (implicit via cc) |
| `totals.substandard` | [x] | [ ] (implicit via cc) |
| `totals.doubtful` | [x] | [ ] (implicit via cc) |
| `totals.loss` | [x] | [ ] (implicit via cc) |
| `totals.no_regulatory_rating` | [x] | [ ] |
| `totals.criticized_classified` | [x] | [x] |
| `totals.cc_pct_of_commitment` | [x] | [x] |
| `totals.validation_diff / ok` | [x] (footnote only) | [x] (footnote only) |
| `counts.parents` | [x] | [x] |
| `counts.partners` | [x] | [ ] |
| `counts.facilities` | [x] | [x] |
| `counts.industries` | [x] | [x] |
| `wapd.display` | [x] | [x] |
| `walgd.display` | [x] | [x] |
| `by_industry` | [ ] | [x] top-5 by committed |
| `by_segment` | [ ] | [ ] |
| `by_branch` | [ ] | [ ] |
| `by_horizontal` | [x] committed + facility count | [ ] |
| `by_ig_status` | [x] committed only | [x] committed + % of rated |
| `watchlist` | [x] | [x] |
| `top_contributors.by_committed` | [ ] | [x] top-5 |
| `top_contributors.by_outstanding` | [ ] | [ ] |
| `top_contributors.by_wapd_contribution` | [ ] | [ ] |
| `top_contributors.by_cc_exposure` | [ ] | [ ] |
| `top_wapd_facility_contributors` | [ ] | [x] top-10 context / top-5 tile |
| `wapd_contributors_by_horizontal` | [ ] | [ ] |
| `month_over_month.new_originations` | [ ] | [x] count only |
| `month_over_month.exits` | [ ] | [x] count only |
| `month_over_month.parent_entrants/exits` | [ ] | [x] count only |
| `month_over_month.pd_rating_changes` | [ ] | [x] up/down counts only |
| `month_over_month.reg_rating_changes` | [ ] | [x] up/down counts only |
| `month_over_month.top_exposure_movers` | [ ] | [ ] |
| `GroupingHistory.history` (prior periods) | [ ] | [ ] |

**Takeaway:** the cube is over-provisioned relative to the slicers. Several
computed blocks (`by_segment`, `by_branch`, `wapd_contributors_by_horizontal`,
`top_exposure_movers`, three of four `ContributorBlock.by_*` lists,
`GroupingHistory.history`) are never emitted.

---

## 4. Sub-statistics audit — GRM / Leveraged Finance / Watchlist

The cube models these three differently:

### 4.1 Leveraged Finance, Global Recovery Management → full sub-cubes

Triggered by template `FieldSpec(role="horizontal_flag", trigger_value=…)` and
computed via [`_grouping_history(df, periods, mask=h_mask)`](../pipeline/cube/lending.py#L87-L95).

Each horizontal portfolio gets a full `GroupingHistory` — *the same KRI
inventory as firm-level* (totals, counts, wapd, walgd, history). See §3.

Matching semantics:

```python
mask = df[col].astype(str).str.strip() == str(spec.trigger_value).strip()
```

[lending.py:92](../pipeline/cube/lending.py#L92)

- Case-sensitive after stripping whitespace. Leveraged Finance requires exact
  `"Y"` (not `"y"`, not `"Yes"`, not `"True"`).
- GRM requires exact `"Directly Managed"` — `"directly managed"`,
  `"DirectlyManaged"`, or `"Directly  Managed"` (double space) would all miss.
- `.astype(str)` converts NaN → `"nan"`, which won't match, so NaN flags fall
  out of the portfolio (safe).

### 4.2 Watchlist → aggregate only (by design)

Computed by [`_watchlist_aggregate`](../pipeline/cube/lending.py#L304-L315):

```python
flag = latest_df["Credit Watch List Flag"].astype(str).str.strip().str.upper()
mask = flag == "Y"
```

- Case-insensitive (`.str.upper()`) — accepts `"y"`, `"Y"`.
- **Fields produced:** period, facility_count, committed, outstanding. No
  rating bucket breakdown, no WAPD, no LGD, no per-industry split.
- **Only** latest period — no history, no MoM.

Template comment explicitly states this is intentional:
[lending.py:82](../pipeline/templates/lending.py#L82).

### 4.3 Within-section breakdowns (IG/NIG, watchlist) — not computed

Neither horizontal portfolio carries its own watchlist count, IG/NIG split, or
industry breakdown. To answer "how much of Leveraged Finance is watchlisted?"
or "what share of GRM is non-investment grade?", the cube has no answer — you
would need to re-intersect the raw DataFrame.

Similarly, `by_industry["Technology"]` has no watchlist-within-Technology
figure; the watchlist is firm-level only.

---

## 5. Horizontal portfolio calculations — deep dive

### 5.1 What IS computed per horizontal

- Full `GroupingHistory` (all KRIs — totals, counts, wapd, walgd, history).
- Top-10 facility-level WAPD contributors within that horizontal
  (`wapd_contributors_by_horizontal[name]`), with `share_of_numerator` computed
  *against the horizontal's total numerator*, not the firm total. See
  [lending.py:384](../pipeline/cube/lending.py#L384) and caller comment
  [lending.py:380-382](../pipeline/cube/lending.py#L380-L382).

### 5.2 What is NOT computed per horizontal

- **No parent-level `ContributorBlock` per horizontal.** Only a firm-level
  ContributorBlock exists; parent concentrations within Leveraged Finance or
  GRM cannot be answered from the cube.
- **No IG/NIG split within horizontal.**
- **No per-industry breakdown within horizontal** — i.e. no
  `by_horizontal["Leveraged Finance"].by_industry` structure.
- **No watchlist count within horizontal.**
- **No period-over-period movement per horizontal** (the `history` list of
  prior KriBlocks is present, but `MomBlock` derivations run only firm-wide).

### 5.3 Horizontal × industry intersection feasibility

The cube does not pre-compute this matrix. To implement:

- Input data is available (each row has both `Risk Assessment Industry` and
  both horizontal flag columns).
- Cost: O(|horizontals| × |industries|) `KriBlock`s per period — modest.
- Schema change needed: `by_horizontal[name]` would have to become a richer
  structure (e.g. `HorizontalCube` with nested `by_industry`), OR add a
  sibling `by_horizontal_industry: dict[(str, str), GroupingHistory]` map.

No slicer currently asks for this, but the `portfolio-level` and
`portfolio-comparison` placeholder modes imply a user expectation of
"drill into one portfolio" — see §9 Gap #3 on semantic ambiguity.

---

## 6. Cross-section consistency checks

### 6.1 Invariants the cube assumes but does not verify

| Invariant | Would hold if… | Current status |
|---|---|---|
| `Σ by_industry[*].current.totals.committed == firm_level.current.totals.committed` | every row has a non-NaN industry | **Not enforced.** `_grouping_by_dim` drops NaN via `.dropna().unique()` so rows with blank industry are excluded from every industry bucket but still contribute to firm total — silent reconciliation failure. |
| `Σ by_segment[*].committed == firm_level.committed` | every row has a non-NaN segment | Same issue. |
| `Σ by_branch[*].committed == firm_level.committed` | every row has a non-NaN branch | Same issue. |
| `by_ig_status["Investment Grade"].committed + by_ig_status["Non-Investment Grade"].committed == firm_level.committed` | every row has a valid `PD Rating` in scale | **Not enforced.** See §8.3 — rows with unknown/missing PD are silently bucketed as NIG via `~is_ig`. |
| `Σ by_horizontal[*].committed ≤ firm_level.committed` | (portfolios may overlap) | Not enforced, but obvious — inequality only. |
| `watchlist.facility_count ≤ firm_level.facilities` | — | True by construction (filter is subset). |
| `top_contributors.by_committed[0].committed ≤ firm_level.committed` | — | True by construction. |
| `Σ {pass, SM, SS, Dbt, L, NoReg} ≈ committed` (per group, within $2) | data is clean | **Enforced** at every `KriBlock` via `validation_diff` / `validation_ok`. Rendered as a footnote in both active slicers. |

### 6.2 Period coverage invariants

| Invariant | Status |
|---|---|
| `metadata.periods == sorted(df["Month End"].dropna().unique())` | Enforced at [lending.py:71](../pipeline/cube/lending.py#L71). |
| `metadata.as_of == max(periods)` | By construction. |
| `firm_level.history` length == `len(periods)` (when every period has ≥ 1 row) | Enforced in `_grouping_history` via `if len(slice_) == 0: continue` — so empty periods are silently dropped. Could produce `history` shorter than `periods`. |
| `month_over_month.prior_period == periods[-2]` | Always, when `len(periods) >= 2`. Earlier periods never compared. |

### 6.3 Rating scale invariants

| Invariant | Status |
|---|---|
| `investment_grade_codes() + non_investment_grade_codes() == all_codes()` | True by construction ([pd_scale.py:119-126](../pipeline/scales/pd_scale.py#L119-L126)). |
| `C00..C07` IG, `C08..CDF` NIG | Documented ([pd_scale.py:43](../pipeline/scales/pd_scale.py#L43)); hard-coded cutoff index. |
| PD value `v` maps to first code whose `upper_bound >= v` | Enforced ([pd_scale.py:67-69](../pipeline/scales/pd_scale.py#L67-L69)); negatives clamp to C00, `v ≥ 1` clamps to CDF. |

---

## 7. Determinism audit

### 7.1 Where explicit tiebreakers are declared

| Site | Primary key | Tiebreaker | File |
|---|---|---|---|
| `_top_contributors` sort (4 metrics) | `committed` / `outstanding` / `wapd_numerator` / `cc_exposure` | `Ultimate Parent Code` asc | [lending.py:359-362](../pipeline/cube/lending.py#L359-L362) |
| `_top_wapd_facility_contributors` | `wapd_numerator` | `Facility ID` asc | [lending.py:412-414](../pipeline/cube/lending.py#L412-L414) |
| `_facility_changes.sort` | `-committed` | `facility_id` asc | [lending.py:500](../pipeline/cube/lending.py#L500) |
| `_exposure_movers` | `abs(delta)` | `Facility ID` asc | [lending.py:589-594](../pipeline/cube/lending.py#L589-L594) (fixed this round) |
| `_grouping_by_dim` dim iteration | `sorted(values, key=str)` | — | [lending.py:298](../pipeline/cube/lending.py#L298) |
| `_month_over_month` parent entrants/exits | `sorted(set(…) − set(…))` | — | [lending.py:454-461](../pipeline/cube/lending.py#L454-L461) |
| `_normalize` (regulatory_rating) | OCC order index | rating code asc | [regulatory_rating.py:117](../pipeline/parsers/regulatory_rating.py#L117) |
| `portfolio_summary._top_groupings` | `-committed` | name asc | [portfolio_summary.py:399](../pipeline/processors/lending/portfolio_summary.py#L399) |

### 7.2 Sites that rely on *implicit* ordering

These are deterministic today but only because of Python / pandas defaults. A
refactor of the upstream operation could silently break determinism.

| Site | Implicit source of order | Risk |
|---|---|---|
| `_pd_rating_changes` iterrows over `joined` | `groupby("Facility ID")` default `sort=True` then join | **LOW**. If anyone adds `sort=False` for perf or changes the join type, iteration order becomes hash-dependent. No explicit secondary sort. [lending.py:511-534](../pipeline/cube/lending.py#L511-L534) |
| `_reg_rating_changes` iterrows over `joined` | same pattern | **LOW**. Same risk. [lending.py:545-566](../pipeline/cube/lending.py#L545-L566) |
| `by_horizontal` dict iteration | `FIELDS.items()` declaration order in `templates/lending.py` | Dict preserves insertion order in Python 3.7+; stable as long as FIELDS is kept declarative. |
| `by_ig_status` dict iteration | insertion order: IG first if any IG rows, then NIG | Stable; only two keys max. |
| `LendingCube.available_portfolios` | `sorted(by_industry.keys())` | Explicit — fine. |
| `_counts.industries` via `.nunique()` | Pandas nunique — order-independent count | Fine. |
| `_watchlist_aggregate` facility_count / committed | Pure sums over a filtered frame | Fine. |
| `classifier.classify` sheet iteration | `pd.read_excel(sheet_name=None)` dict order | Excel sheet order is preserved by openpyxl. Order matters only when two sheets match the same template (raises) — deterministic failure. |

### 7.3 Numeric determinism

- `pd.to_numeric(errors="coerce").fillna(0)` — deterministic; blanks → 0.
- Floating-point sums are deterministic *for the same input row order*. Pandas
  `.sum()` iterates in frame order; frame order comes from the workbook read,
  which is itself deterministic.
- `committed / wapd_numerator` ratios — deterministic IEEE-754.

---

## 8. Calculation correctness spot-checks

### 8.1 `total_committed` / `total_outstanding`

Straight `.sum()` over the column within the current group/period slice. Trusts:

- The template header string is present (`Committed Exposure`, `Outstanding
  Exposure`).
- `coerce_numeric=True` on the FieldSpec → blanks silently become 0.

**Correctness:** correct *if* blanks truly mean zero. If an export ever uses
blank to mean "unknown", the sum is understated silently. See Gap #7.

### 8.2 Weighted average PD / LGD

Formula: `sum(numerator) / sum(Committed Exposure)` — the decimal result is
then:

- PD: mapped to a rating code via `pd_scale.code_for_pd(raw)`.
- LGD: rendered as `percent_2dp` → `"XX.XX%"`.

**Correctness:** correct provided the exporter produces `Weighted Average PD
Numerator = PD × Committed Exposure` per row. The cube has no way to verify
this — it takes the numerator column at face value.

Edge cases handled:

- `denominator == 0` → `WeightedAverage(raw=None, display=None, …)`.
- `raw > 1` → `code_for_pd` clamps to `CDF`.
- `raw < 0` → clamps to `C00`.
- NaN → `None`.

### 8.3 IG/NIG split — **potential bug**

[lending.py:98-104](../pipeline/cube/lending.py#L98-L104):

```python
ig_codes = set(pd_scale.investment_grade_codes())     # {"C00",…,"C07"}
is_ig = df["PD Rating"].astype(str).str.strip().str.upper().isin(ig_codes)
if is_ig.any():
    by_ig_status["Investment Grade"] = _grouping_history(df, periods, mask=is_ig)
if (~is_ig).any():
    by_ig_status["Non-Investment Grade"] = _grouping_history(df, periods, mask=~is_ig)
```

**Finding:** a facility with a blank, NaN, or unrecognized `PD Rating` (e.g.
`"UNRATED"`, `"NA"`, `""`) has `is_ig=False`, which puts it into **`~is_ig` →
Non-Investment Grade**. Unknown ratings are silently bucketed as NIG.

**Impact:** `by_ig_status["Non-Investment Grade"].totals.committed` over-states
NIG exposure by the amount of the unrated book. Downstream tiles (IG % share)
are understated.

**Severity:** HIGH. Flagged; not fixed.

### 8.4 `facility_count` / `watchlist_count`

`.nunique()` on `Facility ID`. Correctness relies on Facility ID being globally
unique. If the same Facility ID appears in multiple rows (e.g. different
product components), nunique collapses them — correct for a count, but WAPD
and totals will then sum across the duplicate rows (double-count) unless they
represent genuinely different dollar positions. No explicit "one row per
facility per period" check exists.

### 8.5 `month_over_month`

[lending.py:438-480](../pipeline/cube/lending.py#L438-L480).

- Facility sets compared using stringified `Facility ID`. NaN `Facility ID`
  rows are dropped from both sides before the set diff ([lending.py:445-446](../pipeline/cube/lending.py#L445-L446)).
- Compares only the **latest two periods**. If the file has 3+ periods, older
  transitions are not captured in `MomBlock`. (They ARE present in
  `firm_level.history`, but no slicer renders history.)
- `parent_entrants` / `parent_exits` — set diff of Ultimate Parent Codes.
- `pd_rating_changes` / `reg_rating_changes` — inner join of common facilities;
  skips rows where either side is NaN. PD comparison uppercases both sides;
  reg comparison uses `regulatory_rating.equals()` which tolerates component
  reordering and 0.5pp rounding.
- `top_exposure_movers` — delta in committed for persistent facilities.
  Computed but not surfaced by any slicer.

### 8.6 `top_contributors`

Parent-level (`Ultimate Parent Code`) aggregation with `dropna=False`, so
facilities with NaN parent all group under a single NaN bucket labeled `"nan"`
in the output. Sort is deterministic (§7.1). Correctness fine **assuming every
facility has an Ultimate Parent Code** — if not, a single "nan" entity appears
in the top-N.

### 8.7 Horizontal filter semantics

Recap from §4.1 — exact string match on the stripped cell value. This is
strict and relies on the export format being consistent. Flagged as a
sensitivity, not a bug.

### 8.8 Validation tolerance

`EXPOSURE_VALIDATION_TOLERANCE = 2.0` dollars absolute
([lending.py:45](../pipeline/cube/lending.py#L45)). Applied to every `KriBlock`
(firm, every industry, every horizontal, IG, NIG, watchlist). For small
groupings the $2 tolerance is effectively a strict equality; for large firm
totals it's a 0.00002% check. Correctness is fine; the choice is just an
absolute-only policy.

---

## 9. Known gaps and risks

### High

1. **IG/NIG misclassification for unrated / invalid PD ratings.** §8.3. Facilities
   without a valid code silently fall into NIG via `~is_ig`. Affects
   `by_ig_status["Non-Investment Grade"]` totals, IG% shares, and every
   claim derived from them.

2. **`by_industry` / `by_segment` / `by_branch` do not reconcile with firm
   totals when dim values are NaN.** §6.1. `_grouping_by_dim` drops NaN via
   `.dropna().unique()`, but those rows still contribute to the firm-level
   total — producing a silent shortfall. No "unclassified" catchall bucket.

3. **Semantic ambiguity: what is a "portfolio"?** `cube.available_portfolios`
   returns `sorted(by_industry.keys())` ([models.py:260-261](../pipeline/cube/models.py#L260-L261)),
   so placeholder modes `portfolio-level` / `portfolio-comparison`
   will operate on **industries**, not horizontals. The prompt templates
   (`portfolio_level.md`) say "the {{portfolio}} portfolio" without
   disambiguation. This is a wording/routing gap the user should resolve
   before the placeholder modes go live.

### Medium

4. **No per-industry / per-horizontal watchlist breakdown.** §4.3. Watchlist
   is firm-level only; questions about watchlist composition can't be
   answered from the cube.

5. **No horizontal × industry intersection.** §5.3. Cube has no
   drill-down for "Leveraged Finance by industry" or similar.

6. **MoM only compares the last two periods.** §8.5. Files with 3+ periods
   lose the earlier transitions; `firm_level.history` is populated but no
   slicer surfaces it.

7. **`coerce_numeric=True` + `fillna(0)` conflates blank and zero.** §8.1. If
   an exporter uses blank to mean "unknown", the sums silently under-count.
   No warning surfaces when blanks exist.

8. **Per-facility rating changes computed but only up/down counts surfaced.**
   §3.4. `MomBlock.pd_rating_changes` and `reg_rating_changes` are lists of
   facility-level transitions; `portfolio_summary.py` reduces them to two
   counts each. Granularity is lost to the narrative.

9. **`firm_level.py` slicer does not surface `top_contributors`,
   `top_wapd_facility_contributors`, or any MoM.** Feels like an asymmetry —
   the cube computes them and portfolio_summary uses them.

10. **`dropna=False` in `_top_contributors.groupby` lets NaN parents into the
    top-N.** §8.6. Results in a "nan" entity if any rows lack Ultimate Parent
    Code. Low frequency in clean data but worth deciding policy.

### Low

11. **`HorizontalAggregate` model declared but unused.** [models.py:71](../pipeline/cube/models.py#L71).
    Dead code; suggests a partial refactor.

12. **`wapd_contributors_by_horizontal` computed but not surfaced** by any
    slicer. [lending.py:114-129](../pipeline/cube/lending.py#L114-L129).

13. **`by_segment` and `by_branch` computed but not surfaced** by any slicer.
    Waste of compute; may be intentional scaffolding for a future mode.

14. **Three of four `ContributorBlock.by_*` lists unused.** Only
    `by_committed` is rendered (by `portfolio_summary`). `by_outstanding`,
    `by_wapd_contribution`, `by_cc_exposure` are dead weight in the cube.

15. **`top_exposure_movers` in MomBlock computed but not surfaced.**

16. **`GroupingHistory.history` populated but never rendered.** Prior-period
    KriBlocks exist in the cube for every grouping, but no slicer walks them.

17. **Implicit sort reliance in `_pd_rating_changes` / `_reg_rating_changes`.**
    §7.2. Deterministic today via pandas groupby default `sort=True`, but no
    explicit secondary key. A refactor could silently break re-run stability.

18. **Empty periods silently dropped in `_grouping_history`.**
    [lending.py:276-277](../pipeline/cube/lending.py#L276-L277). `len(history)`
    could be shorter than `len(periods)` without any warning.

19. **Validation tolerance is absolute ($2), not relative.** §8.8. Works for
    bank-scale portfolios but not self-documenting. A comment or relative
    fallback would reduce confusion if the app is ever tested with tiny
    fixture data.

---

## 10. Recommendations

Prioritised, each narrowly scoped so it can be picked up standalone. None of
these are implementation — they are decisions the user should make before a
follow-up patch.

1. **Fix the IG/NIG bucketing (Gap #1).** Decide the policy: either (a) add an
   `"Unrated"` key to `by_ig_status` for PD codes not in the scale, or (b)
   drop those rows from both IG and NIG and emit a validation warning. Either
   option is a contained change in `cube/lending.py` around line 99 plus a
   documentation update to the prompts.

2. **Reconcile dim-bucketed totals with firm totals (Gap #2).** In
   `_grouping_by_dim`, add an `"Unclassified"` bucket for NaN dim values so
   `Σ by_industry[*].committed == firm_level.committed`. Alternatively, emit
   a `validation_warning` when the sum mismatches.

3. **Clarify "portfolio" semantics (Gap #3).** Either:
   - rename `LendingCube.available_portfolios` → `available_industries` and
     add a new `available_horizontals` for horizontal-portfolio-based modes,
     or
   - keep the name but document that "portfolio" means industry in KRONOS and
     update the `portfolio_level.md` / `portfolio_comparison.md` prompt
     language to use "industry".

4. **Decide whether to wire the dormant cube outputs** (`by_segment`,
   `by_branch`, `wapd_contributors_by_horizontal`, `top_exposure_movers`, the
   three unused `top_contributors.by_*` lists, `GroupingHistory.history`).
   Either surface them in a future slicer or delete them from the cube to
   reduce attack surface and cognitive load.

5. **Add explicit secondary sort to `_pd_rating_changes` and
   `_reg_rating_changes` (Gap #17).** One-line each; matches the pattern
   already in `_top_contributors` / `_top_wapd_facility_contributors` /
   `_exposure_movers`. Removes implicit-sort fragility before it bites.

6. **Remove the unused `HorizontalAggregate` model (Gap #11)** OR wire it in
   as the return type of `_watchlist_aggregate` + any future per-horizontal
   watchlist summary. Right now it's an attractive nuisance.

7. **Add invariant assertions in `compute_lending_cube`.** Cheap runtime
   sanity checks that log warnings (not raise) on: dim-bucket reconciliation,
   IG + NIG = firm, per-horizontal ≤ firm. Would surface the silent
   reconciliation failures (Gaps #2, #1) without blocking the response.

8. **Document the blank-vs-zero policy for stock columns (Gap #7).** Either
   keep `fillna(0)` and note that blank means zero per the exporter contract,
   or switch to `fillna(0)` with a per-column blank-count warning in
   `CubeMetadata.warnings`.

9. **Expand MoM coverage (Gap #6).** Decide whether MoM should support
   latest-vs-earliest, latest-vs-each-prior, or remain latest-vs-second-
   latest. Affects `_month_over_month` and the `MomBlock` schema.

10. **Add at least one smoke-test file under `tests/`.** Currently no tests
    exist. A single fixture `.xlsx` plus a test that asserts
    `compute_lending_cube(fixture).firm_level.current.totals.committed ==
    <known value>` would lock in the reconciliation contract and make every
    future refactor safer.
