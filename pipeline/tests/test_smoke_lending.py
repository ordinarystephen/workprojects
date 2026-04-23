# ── KRONOS · pipeline/tests/test_smoke_lending.py ─────────────
# Smoke test for the deterministic Lending cube against a fixture.
#
# This is the first end-to-end correctness test the calculation layer
# has had. The fixture (pipeline/tests/fixtures/smoke_lending.xlsx)
# is hand-picked to exercise every bucket and edge case the cube
# computes; expected values come from hand-arithmetic on the fixture
# rows (NOT from running the cube — see the docstring at the top of
# smoke_lending_expected.py).
#
# Every test asserts ONE business concept so a regression points
# directly at the broken invariant. All tests share a single cube
# computed once via the `cube` session fixture for speed.
#
# Run from repo root:
#   pytest pipeline/tests/test_smoke_lending.py -v
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from pipeline.cube.lending import (
    EXPOSURE_VALIDATION_TOLERANCE,
    compute_lending_cube,
)
from pipeline.loaders.classifier import classify
from pipeline.scales import pd_scale
from pipeline.tests.fixtures import smoke_lending_expected as ex


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "smoke_lending.xlsx"

# Currency tolerance — same value the cube uses for its own internal
# reconciliation invariants, so the tests assert against the same bar.
DOLLAR_TOL = EXPOSURE_VALIDATION_TOLERANCE  # = $2.00


# ── Shared fixtures ───────────────────────────────────────────

@pytest.fixture(scope="module")
def lending_df() -> pd.DataFrame:
    """Validated DataFrame from the smoke fixture workbook."""
    with FIXTURE_PATH.open("rb") as f:
        result = classify(f)
    assert "lending" in result["classified"], (
        f"Fixture didn't classify as lending. Sheets seen: "
        f"{result['metadata']['sheets_seen']}"
    )
    return result["classified"]["lending"]


@pytest.fixture(scope="module")
def cube(lending_df):
    """The cube under test — computed once, reused across assertions."""
    return compute_lending_cube(lending_df)


# ── 1. Firm-level totals ──────────────────────────────────────

def test_firm_level_totals(cube):
    totals = cube.firm_level.current.totals
    expected = ex.EXPECTED["firm_level"]["totals"]
    assert totals.committed             == pytest.approx(expected["committed"],             abs=DOLLAR_TOL)
    assert totals.outstanding           == pytest.approx(expected["outstanding"],           abs=DOLLAR_TOL)
    assert totals.criticized_classified == pytest.approx(expected["criticized_classified"], abs=DOLLAR_TOL)
    assert totals.approved_limit        == pytest.approx(expected["approved_limit"],        abs=DOLLAR_TOL)


# ── 2. Firm-level counts ──────────────────────────────────────

def test_firm_level_counts(cube):
    counts = cube.firm_level.current.counts
    expected = ex.EXPECTED["firm_level"]["counts"]
    assert counts.parents    == expected["parents"]
    assert counts.partners   == expected["partners"]
    assert counts.facilities == expected["facilities"]
    assert counts.industries == expected["industries"]


# ── 3. Firm-level WAPD ────────────────────────────────────────

def test_firm_level_wapd(cube):
    wapd = cube.firm_level.current.wapd
    expected = ex.EXPECTED["firm_level"]["wapd"]
    assert wapd.raw     == pytest.approx(expected["raw"], abs=1e-9)
    assert wapd.display == expected["display"]


# ── 4. Firm-level WALGD ───────────────────────────────────────

def test_firm_level_walgd(cube):
    walgd = cube.firm_level.current.walgd
    expected = ex.EXPECTED["firm_level"]["walgd"]
    assert walgd.raw     == pytest.approx(expected["raw"], abs=1e-9)
    assert walgd.display == expected["display"]


# ── 5. Industry reconciliation ────────────────────────────────

def test_industry_reconciliation(cube):
    section_sum = sum(h.current.totals.committed for h in cube.by_industry.values())
    firm = cube.firm_level.current.totals.committed
    assert section_sum == pytest.approx(firm, abs=DOLLAR_TOL), (
        f"by_industry committed sum ({section_sum:,.2f}) does not "
        f"reconcile to firm-level committed ({firm:,.2f}). The "
        f"Unclassified bucket from blank Risk Assessment Industry "
        f"values is the most likely cause if this regresses."
    )


# ── 6. Segment reconciliation ─────────────────────────────────

def test_segment_reconciliation(cube):
    section_sum = sum(h.current.totals.committed for h in cube.by_segment.values())
    firm = cube.firm_level.current.totals.committed
    assert section_sum == pytest.approx(firm, abs=DOLLAR_TOL)


# ── 7. Branch reconciliation ──────────────────────────────────

def test_branch_reconciliation(cube):
    section_sum = sum(h.current.totals.committed for h in cube.by_branch.values())
    firm = cube.firm_level.current.totals.committed
    assert section_sum == pytest.approx(firm, abs=DOLLAR_TOL)


# ── 8. Rating category coverage ───────────────────────────────

def test_rating_category_coverage(cube):
    """IG + NIG + Defaulted + Non-Rated should equal firm committed.

    Distressed is a SUB-stat of NIG (not a peer bucket) and is NOT
    added in here — its rows are already counted under NIG.
    """
    ig  = sum(h.current.totals.committed for h in cube.by_ig_status.values())
    df_ = sum(h.current.totals.committed for h in cube.by_defaulted.values())
    nr  = sum(h.current.totals.committed for h in cube.by_non_rated.values())
    total = ig + df_ + nr
    firm = cube.firm_level.current.totals.committed
    assert total == pytest.approx(firm, abs=DOLLAR_TOL), (
        f"Rating-category coverage failed: IG+NIG={ig:,.2f}, "
        f"Defaulted={df_:,.2f}, Non-Rated={nr:,.2f}, sum={total:,.2f}, "
        f"firm={firm:,.2f}. A row's PD Rating may be falling outside "
        f"the four top-level masks — check cube.metadata.warnings for "
        f"a 'pd_rating_unclassified' entry."
    )


# ── 9. Non-Rated bucket membership ────────────────────────────

def test_non_rated_bucket_membership(cube, lending_df):
    """Every row in by_non_rated must have a PD Rating that is a
    Non-Rated token (case-insensitive, whitespace-stripped) or NaN."""
    if "Non-Rated" not in cube.by_non_rated:
        pytest.skip("Fixture has no Non-Rated rows in latest period.")

    period_col = "Month End"
    latest = cube.metadata.as_of
    latest_df = lending_df[lending_df[period_col].dt.date == latest]

    pd_upper = latest_df["PD Rating"].astype(str).str.strip().str.upper()
    is_non_rated = (
        latest_df["PD Rating"].isna() | pd_upper.isin(pd_scale.NON_RATED_TOKENS)
    )
    nr_rows = latest_df[is_non_rated]
    assert len(nr_rows) > 0, "Non-Rated bucket is populated but no rows match."
    for _, row in nr_rows.iterrows():
        v = row["PD Rating"]
        if pd.isna(v):
            continue
        assert str(v).strip().upper() in pd_scale.NON_RATED_TOKENS, (
            f"Facility {row['Facility ID']} bucketed as Non-Rated but "
            f"PD Rating={v!r} is not in NON_RATED_TOKENS."
        )


# ── 10. Distressed bucket membership ──────────────────────────

def test_distressed_bucket_membership(cube, lending_df):
    """Every row contributing to nig_distressed_substats must have
    PD Rating == C13 (case-insensitive)."""
    if cube.nig_distressed_substats is None:
        pytest.skip("Fixture has no Distressed rows in latest period.")

    latest_df = lending_df[lending_df["Month End"].dt.date == cube.metadata.as_of]
    pd_upper  = latest_df["PD Rating"].astype(str).str.strip().str.upper()
    dist_rows = latest_df[pd_upper == pd_scale.distressed_code()]

    assert len(dist_rows) == cube.nig_distressed_substats.facility_count, (
        f"Distressed facility count mismatch: substats says "
        f"{cube.nig_distressed_substats.facility_count}, fixture C13 "
        f"rows = {len(dist_rows)}."
    )
    for _, row in dist_rows.iterrows():
        assert str(row["PD Rating"]).strip().upper() == pd_scale.distressed_code()


# ── 11. Defaulted bucket membership ───────────────────────────

def test_defaulted_bucket_membership(cube, lending_df):
    """Every row in by_defaulted must have PD Rating == CDF."""
    if "Defaulted" not in cube.by_defaulted:
        pytest.skip("Fixture has no Defaulted rows in latest period.")

    latest_df = lending_df[lending_df["Month End"].dt.date == cube.metadata.as_of]
    pd_upper  = latest_df["PD Rating"].astype(str).str.strip().str.upper()
    def_rows  = latest_df[pd_upper == pd_scale.defaulted_code()]

    assert len(def_rows) > 0
    for _, row in def_rows.iterrows():
        assert str(row["PD Rating"]).strip().upper() == pd_scale.defaulted_code()


# ── 12. Unclassified industry bucket ──────────────────────────

def test_unclassified_industry_bucket(cube):
    """A blank/NaN Risk Assessment Industry must collapse into a single
    Unclassified bucket whose totals match the fixture-row hand-count."""
    assert "Unclassified" in cube.by_industry, (
        "Unclassified industry bucket missing — blank Risk Assessment "
        "Industry rows are being dropped (regression of the Round-13 "
        "_normalize_dim fix)."
    )
    expected = ex.EXPECTED["unclassified_industry"]
    grouping = cube.by_industry["Unclassified"]
    assert grouping.current.totals.committed == pytest.approx(
        expected["committed"], abs=DOLLAR_TOL
    )
    assert grouping.current.counts.facilities == expected["facility_count"]


# ── 13. available_industries correctness ──────────────────────

def test_available_industries(cube):
    actual = cube.available_industries
    expected = ex.EXPECTED["available_industries"]
    assert actual == expected, (
        f"available_industries mismatch.\n  expected: {expected}\n  "
        f"actual:   {actual}"
    )
    # Belt-and-braces invariants the test suite locks in:
    assert actual == sorted(actual), "available_industries is not sorted ascending."
    assert "Unclassified" in actual, "Unclassified industry not exposed in picker source."


# ── 14. available_horizontals correctness ─────────────────────

def test_available_horizontals(cube):
    actual = cube.available_horizontals
    expected = ex.EXPECTED["available_horizontals"]
    assert actual == expected, (
        f"available_horizontals mismatch.\n  expected: {expected}\n  "
        f"actual:   {actual}"
    )
    assert actual == sorted(actual), "available_horizontals is not sorted ascending."
    # cube.by_horizontal must agree with the picker source — one is
    # derived from the other, but locking it down here protects against
    # future divergence.
    assert set(actual) == set(cube.by_horizontal.keys())


# ── 15. Horizontal overlap handled ────────────────────────────

def test_horizontal_overlap(cube):
    """A facility flagged in two horizontals must contribute to BOTH
    portfolio totals — the predicates are independent overlays, not
    a partition."""
    overlap_fid = ex.EXPECTED["horizontal_overlap_facility"]
    overlap_committed = ex.COMMITTED[overlap_fid]

    for h_name, members in ex.EXPECTED["horizontal_members"].items():
        assert h_name in cube.by_horizontal, f"Horizontal '{h_name}' missing from cube."
        expected_total = sum(ex.COMMITTED[fid] for fid in members)
        actual_total   = cube.by_horizontal[h_name].current.totals.committed
        assert actual_total == pytest.approx(expected_total, abs=DOLLAR_TOL), (
            f"Horizontal '{h_name}' committed = {actual_total:,.2f}, "
            f"expected {expected_total:,.2f}."
        )
        # Overlap-facility's committed must be inside this horizontal's total.
        if overlap_fid in members:
            assert actual_total >= overlap_committed - DOLLAR_TOL


# ── 16. Month-over-month populated ────────────────────────────

def test_month_over_month(cube):
    mom = cube.month_over_month
    assert mom is not None, "MoM block missing — fixture has 2 periods."
    expected = ex.EXPECTED["month_over_month"]

    assert len(mom.new_originations)   == expected["new_originations_count"]
    assert len(mom.exits)              == expected["exits_count"]
    assert len(mom.pd_rating_changes)  == expected["pd_rating_changes_count"]
    assert len(mom.reg_rating_changes) == expected["reg_rating_changes_count"]

    # Identity asserts — locks down which facility moved, not just the count.
    assert mom.new_originations[0].facility_id   == expected["new_origination_facility"]
    assert mom.exits[0].facility_id              == expected["exit_facility"]
    assert mom.pd_rating_changes[0].facility_id  == expected["pd_change_facility"]
    assert mom.reg_rating_changes[0].facility_id == expected["reg_change_facility"]


# ── 17. Determinism ───────────────────────────────────────────

def test_determinism(lending_df):
    """Re-running the cube on the same DataFrame must produce byte-
    identical JSON. Catches any non-deterministic ordering, set
    iteration, or dict ordering left in the cube path."""
    a = compute_lending_cube(lending_df).model_dump_json()
    b = compute_lending_cube(lending_df).model_dump_json()
    assert a == b, "compute_lending_cube produced non-deterministic output."
