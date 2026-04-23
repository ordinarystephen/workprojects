# KRONOS — Future Work

Planned-but-not-built extensions to the deterministic cube + slicer layer. Each
entry below documents intent, the concrete cube/slicer shape, and the remaining
work before the mode can flip from `status: placeholder` to `status: active` in
`config/modes.yaml`.

---

## Industry vs Horizontal Portfolios — the distinction

Two portfolio kinds coexist in the lending book and must not be conflated:

| Kind | Semantics | Source field(s) | Typical examples |
|---|---|---|---|
| **Industry portfolio** | **Partition** — every facility is in exactly one industry | `Risk Assessment Industry` (NaN/blank → "Unclassified") | Technology, Energy, Healthcare, … |
| **Horizontal portfolio** | **Boolean overlay** — a facility can be in zero, one, or several; horizontals can overlap with each other AND with industries | `FieldSpec(role="horizontal_flag", trigger_value=..., portfolio_name=...)` columns on `LendingTemplate` | Leveraged Finance, Global Recovery Management, … |

The cube exposes them separately so a parameter picker can never silently mix
the two:

- `cube.available_industries` — sorted keys of `cube.by_industry` / `cube.industry_details`
- `cube.available_horizontals` — sorted keys of `cube.by_horizontal` / `cube.horizontal_details`

Active modes consume each independently:

- `industry-portfolio-level` → `industry_portfolio_level` slicer → reads `cube.industry_details[{{portfolio}}]`
- `horizontal-portfolio-level` → `horizontal_portfolio_level` slicer → reads `cube.horizontal_details[{{portfolio}}]`

---

## 1. Industry × Horizontal Cross-Cut (`industry-within-horizontal`)

**Status:** placeholder in `config/modes.yaml` (mode visible in UI, slicer not
yet wired; clicking surfaces `mode_not_implemented` from `/upload`).

**What it is.** A deep-dive on the facilities that sit in BOTH an industry
partition AND a horizontal overlay — e.g. "Technology facilities within
Leveraged Finance". The intersection is a legitimate analytical scope
(concentration risk, sub-portfolio hot spots) but today has no dedicated
cube section.

### Cube extension needed

Add a per-pair composite keyed on the two parameter names. Two candidate
shapes:

1. **Lazy composite** (preferred): no new cube field; the slicer computes the
   intersection at request time from the existing per-facility DataFrame. Keeps
   the cube slim and avoids precomputing an O(industries × horizontals)
   cross-product most of which will never be queried.

2. **Eager composite**: new `cube.industry_horizontal_details: dict[tuple[str, str], PortfolioSlice]`.
   Only worth it if UI surfaces frequent pairs or if the slicer is called in
   hot paths.

### Slicer sketch (lazy composite path)

```python
# pipeline/processors/lending/industry_within_horizontal.py

from pipeline.cube.models import LendingCube
from pipeline.registry import ParameterError, register_slicer
from pipeline.processors.lending._slice_view import render_slice
# Reuse the same helpers already used by compute_lending_cube:
from pipeline.cube.lending import (
    _build_portfolio_slice,
    _normalize_dim,
)
from pipeline.templates.lending import LendingTemplate


@register_slicer(
    "industry_within_horizontal",
    required_params=["industry", "horizontal"],
)
def slice_industry_within_horizontal(
    cube: LendingCube,
    industry: str,
    horizontal: str,
) -> dict:
    if industry not in cube.available_industries:
        raise ParameterError(
            f"Industry {industry!r} not present. "
            f"Available: {cube.available_industries}"
        )
    if horizontal not in cube.available_horizontals:
        raise ParameterError(
            f"Horizontal {horizontal!r} not present. "
            f"Available: {cube.available_horizontals}"
        )

    df = cube.source_frame  # requires exposing the classified df on the cube
    industry_col = df["Risk Assessment Industry"].apply(_normalize_dim)
    industry_mask = industry_col == industry

    horizontal_predicate = LendingTemplate.horizontal_definitions()[horizontal]
    horizontal_mask = horizontal_predicate(df)

    combined_mask = industry_mask & horizontal_mask
    if not combined_mask.any():
        raise ParameterError(
            f"No facilities in {industry!r} ∩ {horizontal!r}."
        )

    slice_ = _build_portfolio_slice(
        df=df,
        mask=combined_mask,
        name=f"{industry} × {horizontal}",
    )

    firm_committed = cube.firm_level.current.totals.committed
    return render_slice(
        slice_=slice_,
        kind="Industry × Horizontal",
        firm_committed=firm_committed,
        as_of=cube.metadata.as_of.isoformat(),
    )
```

### Prerequisites before this can ship

1. **Expose the classified DataFrame on the cube.** Today `compute_lending_cube`
   consumes the df and discards it. `industry_within_horizontal` (lazy path)
   needs the underlying rows to recompute an arbitrary intersection. Options:
   stash `cube.source_frame: pd.DataFrame` as a `PrivateAttr` on `LendingCube`
   (so it isn't serialized), or lift `_build_portfolio_slice` to accept a
   pre-masked df at cube-build time and persist the mask registry.

2. **Generalize `_build_portfolio_slice` if needed.** The existing helper
   already takes `(df, mask, name)` and returns `PortfolioSlice` — reusable
   verbatim. Verify the prefix convention in `_slice_view.render_slice`
   accepts `kind="Industry × Horizontal"` without breaking the
   `verifiable_values` label contract (prefix becomes
   `"Industry × Horizontal: {industry} × {horizontal} —"`).

3. **Decide on parameter dependency semantics.** Today each parameter resolves
   independently from the cube. A cross-cut mode ideally restricts the
   `horizontal` picker to horizontals that actually have rows in the chosen
   industry (and vice versa) so the UI can't offer empty intersections. This
   requires either a two-step picker flow or a richer
   `GET /cube/parameter-options` response that accepts a partial
   `parameters` payload. Not blocking — a slicer-side `ParameterError` is
   acceptable V1 behavior for an empty intersection.

4. **Write the prompt template** at `config/prompts/industry_within_horizontal.md`.
   Language should make the overlap semantics explicit: the facilities in
   this scope are counted in both the parent industry (as part of its
   partition) and the parent horizontal (as one flag among potentially
   several). Guide the LLM to frame divergence — e.g. "Tech within LevFin
   looks materially more distressed than Tech as a whole".

### Naming / label collision check

Every verifiable-values label from this slicer must be globally unique. The
prefix `"Industry × Horizontal: {industry} × {horizontal} —"` is sufficient to
disambiguate from:

- `"Industry Portfolio: {industry} —"` (industry-only slicer)
- `"Horizontal Portfolio: {horizontal} —"` (horizontal-only slicer)
- `"Firm Portfolio —"` (firm-level slicer)

`pipeline/tests/test_label_collisions.py` will catch any regression.

---

## 2. Industry Portfolio Comparison (`portfolio-comparison`)

**Status:** placeholder. Parameters already wired to `cube.available_industries`
(both `portfolio_a` and `portfolio_b`). Slicer pending.

**What it needs.** A single slicer that builds two `PortfolioSlice` views from
the existing `cube.industry_details` dict and emits a diff-oriented
`context` block: side-by-side scale, side-by-side rating composition, top
contributors from each, delta on KRIs. The same engine generalizes to
horizontal-vs-horizontal and industry-vs-horizontal (see Item 3) — worth
building it parameterized on "two `PortfolioSlice` instances" rather than
hardcoding two industries.

---

## 3. Cross-kind Comparison (industry vs horizontal)

**Status:** not in `config/modes.yaml`. Would reuse Item 2's engine once that
exists. Low priority until there's a business-facing ask — "compare Tech
vs Leveraged Finance" is analytically valid (both are legitimate slice
views of the book) but rarely requested.

---

## 4. Path B cube collapse — unify per-slice storage on `PortfolioSlice`

**Status:** not started. Part 3 landed "Path A" — the two per-slice dicts
(`by_industry` / `by_horizontal` as `GroupingHistory`, and
`industry_details` / `horizontal_details` as `PortfolioSlice`) coexist, with
`PortfolioSlice.grouping` holding the **same instance** as the paired
`GroupingHistory` (not a copy). That kept the cube surface stable for
`firm_level` and `portfolio_summary` slicers while Part 3 shipped.

Path B finishes the consolidation: `industry_details` / `horizontal_details`
become the sole per-slice home on the cube; `by_industry` and `by_horizontal`
are removed.

### Scope of the collapse

1. **Remove `cube.by_industry`** — replace every read with
   `cube.industry_details[name].grouping`. Callers today: `firm_level` and
   `portfolio_summary` slicers (for the "top industries" section and
   reconciliation checks).

2. **Remove `cube.by_horizontal`** — replace every read with
   `cube.horizontal_details[name].grouping`. Callers today: `firm_level`
   (for the "Horizontal Portfolios" section).

3. **Deprecate `cube.wapd_contributors_by_horizontal`.** This dict duplicates
   `cube.horizontal_details[name].top_wapd_facilities` exactly — the values
   are computed from the same `latest_df[horizontal_mask]` on both sides.
   It's currently unused by any slicer (surfaced in the schema, read by
   nothing), so removal is pure cleanup: delete the cube field, delete the
   `wapd_by_horizontal` build loop in `compute_lending_cube`, and migrate any
   eventual reader to `horizontal_details[name].top_wapd_facilities`. Any
   future Concentration Risk slicer or per-horizontal narrative section
   should read from `PortfolioSlice.top_wapd_facilities` directly.

4. **Reconciliation tests follow the rename** — `_check_dim_reconciliation`
   (which today loops over `by_industry` / `by_segment` / `by_branch`) must
   migrate to iterating `industry_details.values()` for the industry case,
   while `by_segment` / `by_branch` stay as-is (they have no `*_details`
   sibling and don't need one — no rating composition or contributor block
   at segment / branch scope).

### Why defer past Part 3

Part 4 and Part 5 of the audit-response series will each touch the cube
surface (Part 4 reconciliation fixes, Part 5 TBD). Collapsing cube fields
now would force both to rebase against a moving schema. Doing Path B as a
single dedicated step after the audit series settles keeps each change
reviewable in isolation and avoids a half-measure where only some of the
per-slice dicts have been merged.

### Signal the collapse is ready to ship

- All Part 3 / 4 / 5 slicers are stable and no new cube-surface changes are
  queued.
- `firm_level` and `portfolio_summary` are the only callers still reading
  `by_industry` / `by_horizontal` (a grep confirms this).
- No external consumer (e.g. a served-model API) has been built on the
  current shape — changing it now is still a local refactor, not a
  backwards-compatibility problem.

---

## Reserved sections in the YAML

`modes.yaml` currently has:

```yaml
syntheses: []
lengths: []
```

These are placeholders for a later iteration. `syntheses` compose multiple
mode outputs into a single document (e.g. a risk assessment spanning
lending + traded products + firm-level); `lengths` modify synthesis output
length (full report, executive briefing, quick update). Neither is
implemented.

### Standardize label forms across slicers

**What:** Currently `firm_level` and `portfolio_summary` use divergent label forms for identical underlying values:

| firm_level | portfolio_summary | Underlying value |
|---|---|---|
| `Committed Exposure` | `Total Committed Exposure` | Firm-wide committed |
| `Outstanding Exposure` | `Total Outstanding Exposure` | Firm-wide outstanding |
| `Criticized & Classified (SM + SS + Dbt + L)` | `Criticized & Classified exposure (SM + SS + Dbt + L)` | Firm-wide C&C |
| `Distinct risk assessment industries` | `Distinct industries` | Industry bucket count |

The divergence is currently documented as intentional scope-based convention in `docs/available-kris.md` (bare labels for firm-wide-only context, "Total" prefix for contexts juxtaposing totals against slice figures). That's a reasonable interim position but long-term the split is a cognitive cost for prompt authors and introduces minor LLM confusion when the same underlying figure has two names.

**Why deferred:** No production bug. The verifier matches strings; neither form is wrong. Migrating touches every active slicer, every prompt, and changes the LLM's input distribution during a stabilization period. Better to do it as a dedicated pass with tests in place to catch regressions.

**Two possible paths (decide at pickup time):**

- **Path 1 — pick one form, migrate everything.** Standardize on either the bare or the "Total" form universally. Cleanest end state, one-time cost.
- **Path 3 — canonical form with contextual prefixing.** Adopt a consistent pattern like `Firm — Committed Exposure` / `Portfolio — Committed Exposure`, mirroring the per-slice prefix convention (`Industry Portfolio: Energy — Committed Exposure`). Fully consistent, largest migration.

Path 2 (codify the current divergence as intentional) is what exists now — documented in `available-kris.md`. If Path 1 or Path 3 is picked up later, update the doc to match.

**Blocked on:** Part 5 smoke tests landing. Without smoke tests, a label rename is a manual-verification-only refactor and high risk for silent regressions. Once smoke tests exist, the migration can be performed with before/after test confirmation that no numbers shifted.

**Design notes when picked up:**
- Update every slicer's `verifiable_values` publication.
- Update every prompt in `config/prompts/` that references old label forms.
- Update `docs/available-kris.md` to reflect the new canonical form.
- Run the full smoke test suite before and after, confirm no mismatches.
- Consider whether the migration should be a single commit or staged by slicer — staging makes review easier but extends the period of inconsistency.

**Triggered by:** Smoke tests existing + either a concrete pain point from prompt authors (this slicer's labels are inconsistent) or a broader doc/prompt refactor where label work is already touching these files.


