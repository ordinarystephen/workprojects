# Firm-Level Slicer — Context Audit

Read-only audit of `pipeline/processors/lending/firm_level.py` (slice_firm_level)
against `pipeline/cube/models.py::LendingCube`. Documents what the slicer
publishes today, what the cube has available that's silently dropped on the
floor, and a proposed scope to close the gaps. **No code changes here.**

The user's reported symptom: the firm-level analysis "feels shallow — like
just totals and WAPD." This audit confirms that read: the slicer surfaces
firm-wide aggregates and rating-category composition reasonably well, but
ignores the cube's industry breakdown, parent concentration list, facility-
level WAPD drivers, and the entire month-over-month block.

---

## 1. What the firm-level slicer currently publishes

### 1a. Context prose (what the LLM sees)

Built in [firm_level.py:57-146](../pipeline/processors/lending/firm_level.py#L57-L146).
Everything below is `\n`-joined into one string the LLM consumes as the
`Portfolio Data:` block.

| # | Section | Lines | Source on cube |
|---|---|---|---|
| 1 | "Firm-level lending portfolio snapshot — as of {date}." | [L58](../pipeline/processors/lending/firm_level.py#L58) | `cube.metadata.as_of` |
| 2 | Counts (parents, partners, facilities, industries) | [L60-64](../pipeline/processors/lending/firm_level.py#L60-L64) | `cube.firm_level.current.counts` |
| 3 | Total exposures (committed / outstanding / take&hold / temp / approved limit) | [L66-71](../pipeline/processors/lending/firm_level.py#L66-L71) | `cube.firm_level.current.totals` |
| 4 | Reg-rating breakdown (Pass / SM / SS / Dbt / Loss / No-Reg / C&C) | [L73-80](../pipeline/processors/lending/firm_level.py#L73-L80) | `cube.firm_level.current.totals` |
| 5 | C&C as % of commitment (conditional) | [L82-85](../pipeline/processors/lending/firm_level.py#L82-L85) | `totals.cc_pct_of_commitment` |
| 6 | Weighted averages (WAPD + WALGD, with raw-decimal trail) | [L87-94](../pipeline/processors/lending/firm_level.py#L87-L94) | `cube.firm_level.current.wapd` / `walgd` |
| 7 | Rating-category composition (IG / NIG / of-which Distressed / Defaulted / Non-Rated, each with $ + facility count + lifecycle marker) | [L99-103, L303-348](../pipeline/processors/lending/firm_level.py#L303-L348) | `cube.by_ig_status`, `cube.nig_distressed_substats`, `cube.by_defaulted`, `cube.by_non_rated` |
| 8 | Horizontal portfolios (committed + facility count + lifecycle marker, sorted by sort_key) | [L106-118](../pipeline/processors/lending/firm_level.py#L106-L118) | `cube.by_horizontal` (only `.current.totals.committed` + `.current.counts.facilities`) |
| 9 | Watchlist firm-level aggregate (count + committed) | [L121-127](../pipeline/processors/lending/firm_level.py#L121-L127) | `cube.watchlist` |
| 10 | "File covers N periods" footnote (just text, no comparisons) | [L130-136](../pipeline/processors/lending/firm_level.py#L130-L136) | `cube.metadata.periods` |
| 11 | Reg-component reconciliation warning (conditional) | [L139-144](../pipeline/processors/lending/firm_level.py#L139-L144) | `totals.validation_ok` / `validation_diff` |

### 1b. Metrics / Data Snapshot tiles

Built in [firm_level.py:149-196, L351-387](../pipeline/processors/lending/firm_level.py#L149-L196).

| Tile group | Tiles | Source |
|---|---|---|
| `Firm-Level Overview · As of {date}` ([L150-170](../pipeline/processors/lending/firm_level.py#L150-L170)) | Distinct Parents, Distinct Industries, Distinct Facilities, Total Commitment, Total Outstanding, Total Take & Hold, Criticized & Classified, C&C %, Weighted Average PD, Weighted Average LGD | `cube.firm_level.current` (10 tiles) |
| `Rating Category Composition` ([L172-174, L351-387](../pipeline/processors/lending/firm_level.py#L351-L387)) | IG, NIG, of-which Distressed (indented), Defaulted, Non-Rated (each as $ tile) | `cube.by_ig_status`, `cube.nig_distressed_substats`, `cube.by_defaulted`, `cube.by_non_rated` |
| `Horizontal Portfolios` ([L176-186](../pipeline/processors/lending/firm_level.py#L176-L186)) | One tile per horizontal: label = portfolio name (with lifecycle marker), value = committed in $M | `cube.by_horizontal` (committed only — **no WAPD, no facility count**) |
| `Watchlist` ([L188-196](../pipeline/processors/lending/firm_level.py#L188-L196)) | Facility Count, Committed Exposure | `cube.watchlist` |

Tiles use `_money()` ($X.XM / 1 decimal) for currency display.

### 1c. Verifiable values (verifier-resolvable labels)

Built in [firm_level.py:202-278](../pipeline/processors/lending/firm_level.py#L202-L278). Each entry is a label → `{value, type}` mapping
that `pipeline/validate.verify_claims` can resolve.

| Group | Labels | Notes |
|---|---|---|
| Counts ([L203-206](../pipeline/processors/lending/firm_level.py#L203-L206)) | Distinct ultimate parents, Distinct partners, Distinct facilities, Distinct risk assessment industries | type=count |
| Exposure totals ([L208-212](../pipeline/processors/lending/firm_level.py#L208-L212)) | Committed Exposure, Outstanding Exposure, Take & Hold Exposure, Temporary Exposure, Approved Limit | type=currency |
| Reg-rating breakdown ([L214-222](../pipeline/processors/lending/firm_level.py#L214-L222)) | Pass, Special Mention, Substandard, Doubtful, Loss, No Regulatory Rating, Criticized & Classified (SM + SS + Dbt + L) | type=currency |
| C&C % (conditional, [L224-227](../pipeline/processors/lending/firm_level.py#L224-L227)) | C&C as % of commitment | type=percentage |
| Weighted averages (conditional on `.display`, [L228-235](../pipeline/processors/lending/firm_level.py#L228-L235)) | Weighted Average PD, Weighted Average LGD | type=string (the rendered display, e.g. "C06") |
| Rating-category buckets ([L240-251](../pipeline/processors/lending/firm_level.py#L240-L251)) | Investment Grade, Non-Investment Grade, Defaulted, Non-Rated (committed only) | type=currency, plain key (no lifecycle suffix) |
| Distressed sub-stat (conditional, [L253-260](../pipeline/processors/lending/firm_level.py#L253-L260)) | Distressed (of which), Distressed facility count | currency + count |
| Horizontals ([L263-266](../pipeline/processors/lending/firm_level.py#L263-L266)) | One per horizontal: label = portfolio name (plain), value = committed | type=currency |
| Watchlist (conditional, [L269-275](../pipeline/processors/lending/firm_level.py#L269-L275)) | Watchlist facility count, Watchlist committed exposure | count + currency |
| Period meta ([L278](../pipeline/processors/lending/firm_level.py#L278)) | as of | type=date |

Approximate count: ~30 verifiable values published when all conditional
sections are populated. **No verifiable values for any per-industry figure,
any parent contributor, any facility-level WAPD driver, or any MoM
derivation.**

---

## 2. What the cube has available but the slicer doesn't surface

Cross-reference of every `LendingCube` field. Reading
[pipeline/cube/models.py:298-370](../pipeline/cube/models.py#L298-L370) top to
bottom:

| Cube field | Used by firm_level? | Gap detail |
|---|---|---|
| `metadata.as_of` | ✅ used | Surfaced as date in context + tile title + verifiable. |
| `metadata.periods` | ⚠️ partial | Mentioned as a count + comma-joined list in the footnote, but NOT used to drive any prior-period comparison even when `len > 1`. |
| `metadata.row_count` / `file_hash` / `warnings` | ❌ ignored | row_count not surfaced (not critical). `warnings` (e.g. `pd_rating_unclassified`) silently dropped — would be a useful data-quality signal in narrative. |
| `firm_level.current.totals` | ✅ used | All 10+ exposure fields surfaced. |
| `firm_level.current.counts` | ✅ used | All 4 count fields surfaced. |
| `firm_level.current.wapd` | ✅ used | display + raw both in context; display in tiles. |
| `firm_level.current.walgd` | ✅ used | Same as WAPD. |
| `firm_level.history` | ❌ ignored | When `len(periods) > 1`, prior-period firm totals (committed, WAPD, etc.) are sitting on `cube.firm_level.history[-2]` but the slicer never reads them. Could power a "committed grew $X / WAPD shifted from C05→C06" line. |
| `by_industry` | ❌ **completely ignored** | All industry breakdowns silently dropped. Each entry is a `GroupingHistory` carrying committed, outstanding, counts, WAPD, WALGD per industry. The single biggest gap by analyst-utility. |
| `by_segment` | ❌ ignored | Portfolio Segment Description breakdown not surfaced. (Plausibly intentional — overlaps with industry conceptually.) |
| `by_branch` | ❌ ignored | UBS Branch Name breakdown not surfaced. (Plausibly intentional for firm-level.) |
| `by_horizontal` | ⚠️ **partial** | Committed and facility count surfaced. **WAPD, WALGD, outstanding, reg-rating breakdown, C&C all dropped.** User flagged this explicitly. |
| `by_ig_status` | ✅ used | IG and NIG buckets surfaced via `_rating_category_section`. |
| `by_defaulted` | ✅ used | Surfaced. |
| `by_non_rated` | ✅ used | Surfaced. |
| `nig_distressed_substats` | ✅ used | Surfaced as indented sub-line under NIG. |
| `watchlist` | ⚠️ partial | committed + facility_count surfaced. `outstanding` field silently dropped. |
| `top_contributors` (parent-level top-10 by 4 metrics) | ❌ **completely ignored** | All four ranked lists (`by_committed`, `by_outstanding`, `by_wapd_contribution`, `by_cc_exposure`) silently dropped. The single most basic concentration question — "who's our biggest borrower?" — can't be answered from the firm-level output. User flagged. |
| `top_wapd_facility_contributors` (facility-level top-10 by WAPD numerator) | ❌ **completely ignored** | Firm-level WAPD drivers — the facilities actually pulling the WAPD number — silently dropped. |
| `wapd_contributors_by_horizontal` | ❌ ignored | Per-horizontal WAPD drivers dropped. (Less critical for firm-level — belongs more in horizontal-level slicer, which already uses it.) |
| `industry_details` (per-industry rich `PortfolioSlice`) | ❌ ignored | Same data as `by_industry` plus per-industry rating composition / contributors / watchlist / facility WAPD drivers. Not needed at firm-level if `by_industry` is the chosen grain, but the WAPD figures here are the same per-industry WAPD that would be surfaced. |
| `horizontal_details` (per-horizontal rich `PortfolioSlice`) | ❌ ignored | Same — per-horizontal rich data not used at firm level. |
| `month_over_month` (when `len(periods) ≥ 2`) | ❌ **completely ignored** | All MoM derivations dropped: `new_originations` count + list, `exits` count + list, `parent_entrants`, `parent_exits`, `pd_rating_changes`, `reg_rating_changes`, `top_exposure_movers`. The footnote at [L130-136](../pipeline/processors/lending/firm_level.py#L130-L136) is the only acknowledgement that multiple periods exist. User flagged. |
| `available_industries` / `available_horizontals` | n/a | Picker-source properties for the registry — not slicer-relevant. |

### Summary of biggest gaps

1. **`by_industry` is invisible.** `cube.by_industry` is a fully populated dict[str, GroupingHistory] with WAPD per industry, but the firm-level slicer never reads it. An analyst asking "where is the firm's exposure concentrated?" gets no answer.
2. **`top_contributors` is invisible.** Parent-level top-10 by committed (and the three other rankings) are silently dropped.
3. **`top_wapd_facility_contributors` is invisible.** Firm-level WAPD drivers — the facilities actually pulling the firm WAPD number — silently dropped.
4. **`month_over_month` is invisible.** Even when the user uploads two periods, the slicer adds nothing more than a "covers 2 periods" footnote. New originations / exits / rating changes / exposure movers all dropped.
5. **Horizontal WAPD missing.** Horizontals get committed + facility count only; their own weighted-average PD and the rest of their KRI block are dropped.

---

## 3. Proposed additions to close the gaps

For each gap, what would be added to context, metrics, and verifiable_values.
**Prose only — no code.** Numbers in tile counts and list lengths are
recommendations for the user to approve or adjust.

### Gap A — Industry breakdown

**Context**: a new "Industry composition (committed exposure):" section
listing the top-N industries by descending committed, each with: $ committed,
share of firm committed (%), facility count, WAPD display, WALGD display.
Tail summary line: "Plus M more industries totaling $X.XXM (Y% of firm)."

**Metrics**: a new tile group "Industry Composition" with one tile per top-N
industry (label = industry name, value = committed in $M, secondary metric =
share %). Same N as context. Sentiment: neutral, but consider warning when
share > 25%.

**Verifiable values**: per-industry labels — proposal: `"Industry: {name} —
Committed Exposure"` and `"Industry: {name} — Weighted Average PD"`. Prefix
mirrors the existing pattern in `_slice_view.py` so labels can never collide
with industry-portfolio-level slicer's verifiable_values.

**Open question — N**: see "Recommended scope" below; I'd default to top-10
with a tail summary line.

### Gap B — Parent concentration

**Context**: a new "Top parent borrowers (by committed exposure):" section
listing top-N parents from `cube.top_contributors.by_committed`, each with:
parent name, $ committed, share of firm committed (%), $ outstanding,
WAPD numerator share (which directly answers "is this borrower also pulling
WAPD up?").

**Metrics**: a new tile group "Top Parents (by Commitment)" with one tile per
top-N (label = parent name, value = committed in $M, secondary = share %).
Warning sentiment if a single parent > 5% of firm.

**Verifiable values**: `"Top Parent: {name} — Committed"` and `"Top Parent:
{name} — Share of Firm"` per entry.

**Open question — N**: top-5 or top-10?

### Gap C — Facility-level WAPD drivers

**Context**: a new "Top facility-level WAPD drivers (firm-wide):" section
listing top-N facilities from `cube.top_wapd_facility_contributors`, each
with: facility name + parent, PD rating, regulatory rating, $ committed,
implied PD %, share of firm WAPD numerator.

**Metrics**: a new tile group "Top WAPD Drivers (Facility)" with one tile
per top-N (label = facility name, value = implied PD %, secondary =
committed). Warning sentiment if any single facility > 10% of WAPD
numerator.

**Verifiable values**: `"Top WAPD Driver: {facility_id} — Committed"`,
`"Top WAPD Driver: {facility_id} — Implied PD"`, `"Top WAPD Driver:
{facility_id} — Share of WAPD Numerator"`.

**Open question**: facility_id is opaque; would `"{facility_name} (parent
{parent_name})"` make a better human-readable label? Verifiable_values keys
need to be unique, so falling back to facility_id when facility_name is
None is necessary either way.

### Gap D — Month-over-month section

**Context**: a new "Month-over-month movement ({prior_period} → {current_period}):"
section, only when `cube.month_over_month is not None`. Items:
- New originations count + top 3 by committed
- Exits count + top 3 by committed
- PD rating changes count, with up/down split, top 3 downgrades
- Reg rating changes count, with up/down split, top 3 downgrades
- Top 3 exposure movers (signed delta)
- Period-over-period firm-level deltas: committed Δ, WAPD shift (e.g.
  "C05 → C06"), C&C $ Δ — derived from `cube.firm_level.history[-2]` vs
  `firm_level.current`. Mark as "calculated" if not pre-computed in the cube.

**Metrics**: a new tile group "Month-over-Month" with tiles for: New
Originations Count, Exits Count, PD Downgrades, PD Upgrades, Reg
Downgrades, Reg Upgrades, Δ Committed (firm), Δ C&C (firm). 6-8 tiles.
Sentiment: positive for upgrades / negative for downgrades / negative for
exits-volume above a threshold.

**Verifiable values**: `"MoM: New Originations Count"`, `"MoM: Exits Count"`,
`"MoM: PD Downgrades Count"`, etc.; per-facility lookups skipped (would
explode the dict).

**Open question**: prior-period firm deltas (committed Δ, WAPD shift) are
NOT pre-computed by the cube — slicer would compute them inline. Mark as
"calculated" in claims (current pattern), or compute and add to verifiable
values?

### Gap E — Horizontal portfolio enrichment

**Context**: extend the existing horizontal section. Each entry currently
shows "$X across N facilities". Add: WAPD display, WALGD display, $
outstanding, $ C&C. Per-horizontal one or two more lines.

**Metrics**: extend `Horizontal Portfolios` tile group from 1 tile per
horizontal to a small block per horizontal (committed, WAPD, C&C). Or keep
the current 1-tile-per-horizontal layout and add a separate "Horizontal
Portfolios — Risk Profile" group with per-horizontal WAPD tiles.

**Verifiable values**: `"Horizontal: {name} — Weighted Average PD"`,
`"Horizontal: {name} — Outstanding"`, `"Horizontal: {name} — C&C"`.

### Gap F — Firm-level period-over-period (firm history)

If MoM section (Gap D) is added, the firm-level period-over-period delta
naturally falls out of it. If MoM is deferred, a single line "Firm
committed moved from $X to $Y (Δ $Z)" using `cube.firm_level.history[-2]`
and `firm_level.current` would still substantially deepen the analysis.

### Smaller cleanups (low priority)

- Surface `cube.metadata.warnings` (e.g. `pd_rating_unclassified`) as a
  data-quality footnote line. Cheap, useful, and currently invisible.
- Add `outstanding` to the watchlist context line (currently dropped).

---

## 4. Recommended scope

### Minimum viable addition (MVA)

Three additions, in priority order:

1. **Industry breakdown — top-10 + tail summary**. Closes the user's stated
   "no industry breakdown" gap. ~12 new context lines, 10 new tiles, ~20 new
   verifiable_values entries. Highest ratio of analyst utility per line.
2. **Top parent borrowers — top-5**. Closes the "no top borrowers" gap.
   ~7 new context lines, 5 new tiles, ~10 new verifiable_values entries.
3. **MoM section, but trimmed** — counts only (originations, exits, PD
   changes split by direction, reg changes split by direction), plus the
   firm-level committed Δ. No per-facility tables in the firm-level view —
   defer those to a dedicated MoM mode. ~6 new context lines, ~6 new tiles,
   ~8 new verifiable_values entries.

This MVA leaves horizontal-WAPD enrichment and facility-level WAPD drivers
on the floor for now. Both are real gaps but more comfortably belong in
horizontal-level and a future "WAPD attribution" mode respectively.

### Ideal state

MVA above plus:

4. **Horizontal portfolio enrichment** — WAPD, WALGD, outstanding, C&C
   per horizontal alongside committed.
5. **Top facility-level WAPD drivers — top-5**. Includes labels for the
   user to ask the obvious follow-up "tell me about facility X".
6. **MoM detail** — top-3 originations, top-3 exits, top-3 downgrades, top-3
   exposure movers (named, not just counts).
7. **Period-over-period firm deltas** as a dedicated mini-section (committed
   Δ, WAPD shift, C&C Δ).
8. **Cube warnings footnote** — surface `metadata.warnings` so any
   data-quality issue (e.g. unclassified PD codes) appears in narrative.

### Output length impact

Rough character-count estimate of the LLM context:

| State | Approx. context length | Notes |
|---|---|---|
| Current | ~1,500 chars | ~30 lines |
| MVA | ~3,000 chars | ~60 lines — roughly doubles, narrative would shift from one-screen to needs-scrolling |
| Ideal | ~4,500 chars | ~90 lines — a substantive analyst report |

For Azure OpenAI gpt-4o, even the ideal-state context is well within the
prompt budget. The risk is not token cost but narrative quality: the LLM
needs prompt guidance to summarise rather than recite, especially the top-N
lists. The Round 13 prompt rewrite already pushes "do not list every
bucket" — same discipline needed for top-N expansions.

The Data Snapshot tile panel will roughly double in length (current ~16
tiles across 4 groups; MVA ~37 tiles across 7 groups; Ideal ~50 tiles
across 9 groups). Worth confirming the UI handles 9 sections gracefully,
or whether some groups should be collapsed-by-default.

---

## 5. Open questions for the user before implementation

1. **Top-N for industries**: top-10 with tail summary, or all industries
   regardless of count? An average upload has 10-20 industries; a few have
   40+.
2. **Top-N for parents**: top-5 or top-10? A bigger N means more concentration
   signal but also more noise.
3. **MoM scope at firm-level**: counts-only (MVA), or counts + top-3 per
   movement type (Ideal)? A MoM-detail mode could absorb the per-facility
   tables instead.
4. **Verifiable_values key prefixes**: the proposal uses `"Industry: {name}
   — ..."` / `"Top Parent: {name} — ..."` / `"MoM: ..."` to prevent
   collisions with industry-portfolio-level / horizontal-portfolio-level
   slicers (which already use `"Industry Portfolio: {name} —"` and
   `"Horizontal Portfolio: {name} —"`). Confirm naming, or pick a different
   convention.
5. **Tile-group ordering**: where should the new groups appear relative to
   existing? Proposal: Firm-Level Overview → Rating Category → **Industry
   Composition (new)** → **Top Parents (new)** → Horizontal Portfolios →
   Watchlist → **Month-over-Month (new)**.
6. **Firm-level history surfacing**: should period-over-period firm deltas
   live inside the MoM section (only appears with ≥2 periods), or as a
   small standalone block (separates the "what changed at the firm level"
   signal from the per-facility detail)?
