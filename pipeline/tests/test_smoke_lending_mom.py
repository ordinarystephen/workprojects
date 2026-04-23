# ── KRONOS · pipeline/tests/test_smoke_lending_mom.py ─────────
# Lifecycle/MoM smoke test for the deterministic Lending cube.
#
# Focused regression for two pieces of work that ship together:
#   (a) the `_grouping_history` fix that pins every bucket's
#       `current` to the cube's latest period, so an exited bucket
#       reports $0 instead of inflating section sums with a prior-
#       period total;
#   (b) the four-slicer rendering layer that decorates exited
#       buckets with " (exited)" and new-this-period buckets with
#       " (new this period)" in context strings and metric tile
#       labels — while keeping verifiable_values keys plain so the
#       claim verifier still resolves citations.
#
# The fixture exercises every lifecycle transition we care about:
#   - Industry exit (Crypto), industry new (AI Lending), industry
#     active (Energy)
#   - Horizontal exit (Leveraged Finance), horizontal new (GRM)
#   - Rating bucket exit (Defaulted), rating bucket new (Non-Rated),
#     rating buckets active (IG, NIG)
#
# Run from repo root:
#   pytest pipeline/tests/test_smoke_lending_mom.py -v
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
from pipeline.processors.lending._bucket_status import (
    is_exited,
    is_new,
    status_marker,
)
from pipeline.processors.lending.firm_level import slice_firm_level
from pipeline.tests.fixtures import smoke_lending_mom_expected as ex


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "smoke_lending_mom.xlsx"

DOLLAR_TOL = EXPOSURE_VALIDATION_TOLERANCE  # = $2.00


# ── Shared fixtures ───────────────────────────────────────────

@pytest.fixture(scope="module")
def lending_df() -> pd.DataFrame:
    with FIXTURE_PATH.open("rb") as f:
        result = classify(f)
    assert "lending" in result["classified"], (
        f"MoM fixture didn't classify as lending. Sheets seen: "
        f"{result['metadata']['sheets_seen']}"
    )
    return result["classified"]["lending"]


@pytest.fixture(scope="module")
def cube(lending_df):
    return compute_lending_cube(lending_df)


# ── 1. Periods present ────────────────────────────────────────

def test_periods(cube):
    assert cube.metadata.periods == ex.EXPECTED["periods"]
    assert cube.metadata.as_of   == ex.EXPECTED["current_period"]


# ── 2. Firm-level committed for the latest period ─────────────

def test_firm_committed_p2(cube):
    """Firm committed in P2 must match hand-computed value. Anchor
    test for every reconciliation that follows."""
    actual = cube.firm_level.current.totals.committed
    expected = ex.EXPECTED["firm_committed_p2"]
    assert actual == pytest.approx(expected, abs=DOLLAR_TOL)


# ── 3. Industry section reconciles to firm in P2 (THE BUG FIX) ─

def test_industry_section_reconciles_p2(cube):
    """Σ by_industry[X].current.totals.committed in P2 MUST equal firm.

    This is the regression test for the dim-reconciliation bug
    fixed in `_grouping_history`. Pre-fix, an industry that existed
    in P1 but not P2 had its `current` set to the P1 KriBlock —
    inflating section_sum by that bucket's prior-period total
    while firm stayed at the latest period only. With the fix,
    `current` is pinned to the latest period (empty totals if the
    bucket has no rows there).

    The fixture's exited Crypto bucket ($50M in P1, $0 in P2) is
    the load-bearing case here: pre-fix, section_sum would have
    been $295M while firm = $245M.
    """
    section_sum = sum(
        h.current.totals.committed for h in cube.by_industry.values()
    )
    firm = cube.firm_level.current.totals.committed
    assert section_sum == pytest.approx(firm, abs=DOLLAR_TOL), (
        f"by_industry section_sum ({section_sum:,.2f}) does not "
        f"reconcile to firm committed ({firm:,.2f}). The "
        f"_grouping_history pin to latest_period has likely "
        f"regressed — an exited bucket is reporting prior-period "
        f"totals on its `current` block."
    )


# ── 4. Per-industry P2 committed values match expected ────────

def test_by_industry_committed_p2(cube):
    """Each industry bucket's current committed must match
    hand-computed values, including $0 for the exited Crypto bucket."""
    expected = ex.EXPECTED["by_industry_committed_p2"]
    for name, expected_committed in expected.items():
        assert name in cube.by_industry, (
            f"Industry {name!r} missing from cube.by_industry. "
            f"Available: {sorted(cube.by_industry.keys())}"
        )
        actual = cube.by_industry[name].current.totals.committed
        assert actual == pytest.approx(expected_committed, abs=DOLLAR_TOL), (
            f"Industry {name!r} committed = {actual:,.2f}, "
            f"expected {expected_committed:,.2f}"
        )


# ── 5. Industry lifecycle classification ──────────────────────

def test_industry_lifecycle_status(cube):
    """is_exited / is_new must classify industries against fixture
    expectations. Pure helper-logic test — independent of rendering."""
    periods = cube.metadata.periods

    for name in ex.EXPECTED["exited_industries"]:
        grouping = cube.by_industry[name]
        assert is_exited(grouping), f"Industry {name!r} should be exited."
        assert not is_new(grouping, periods), (
            f"Industry {name!r} should not be new and exited at once."
        )

    for name in ex.EXPECTED["new_industries"]:
        grouping = cube.by_industry[name]
        assert is_new(grouping, periods), f"Industry {name!r} should be new."
        assert not is_exited(grouping), (
            f"Industry {name!r} should not be exited and new at once."
        )

    for name in ex.EXPECTED["active_industries"]:
        grouping = cube.by_industry[name]
        assert not is_exited(grouping), (
            f"Active industry {name!r} flagged as exited."
        )
        assert not is_new(grouping, periods), (
            f"Active industry {name!r} flagged as new."
        )


# ── 6. Horizontal lifecycle classification ────────────────────

def test_horizontal_lifecycle_status(cube):
    periods = cube.metadata.periods
    expected = ex.EXPECTED["by_horizontal_committed_p2"]

    for name, expected_committed in expected.items():
        assert name in cube.by_horizontal, (
            f"Horizontal {name!r} missing from cube.by_horizontal."
        )
        grouping = cube.by_horizontal[name]
        assert grouping.current.totals.committed == pytest.approx(
            expected_committed, abs=DOLLAR_TOL,
        )

    for name in ex.EXPECTED["exited_horizontals"]:
        assert is_exited(cube.by_horizontal[name]), (
            f"Horizontal {name!r} should be exited."
        )

    for name in ex.EXPECTED["new_horizontals"]:
        assert is_new(cube.by_horizontal[name], periods), (
            f"Horizontal {name!r} should be new."
        )


# ── 7. Rating-bucket lifecycle classification ─────────────────

def test_rating_bucket_lifecycle_status(cube):
    periods = cube.metadata.periods

    for name in ex.EXPECTED["exited_rating_buckets"]:
        assert name in cube.by_defaulted, (
            f"Exited rating bucket {name!r} missing from by_defaulted."
        )
        grouping = cube.by_defaulted[name]
        assert is_exited(grouping), f"Rating bucket {name!r} should be exited."
        assert grouping.current.totals.committed == pytest.approx(0.0, abs=DOLLAR_TOL)

    for name in ex.EXPECTED["new_rating_buckets"]:
        assert name in cube.by_non_rated, (
            f"New rating bucket {name!r} missing from by_non_rated."
        )
        grouping = cube.by_non_rated[name]
        assert is_new(grouping, periods), f"Rating bucket {name!r} should be new."
        expected_committed = ex.EXPECTED["by_non_rated_committed_p2"][name]
        assert grouping.current.totals.committed == pytest.approx(
            expected_committed, abs=DOLLAR_TOL,
        )

    for name in ex.EXPECTED["active_rating_buckets"]:
        assert name in cube.by_ig_status, (
            f"Active rating bucket {name!r} missing from by_ig_status."
        )
        grouping = cube.by_ig_status[name]
        assert not is_exited(grouping), f"Active bucket {name!r} flagged as exited."
        assert not is_new(grouping, periods), f"Active bucket {name!r} flagged as new."


# ── 8. Rating coverage reconciles to firm in P2 ───────────────

def test_rating_coverage_reconciles_p2(cube):
    """IG + NIG + Defaulted + Non-Rated (current.totals.committed) must
    equal firm committed in P2. Same bug-fix test as industries — but
    for the rating-bucket dimension. Pre-fix, the exited Defaulted
    bucket would have inflated this sum by $20M."""
    ig  = sum(h.current.totals.committed for h in cube.by_ig_status.values())
    df_ = sum(h.current.totals.committed for h in cube.by_defaulted.values())
    nr  = sum(h.current.totals.committed for h in cube.by_non_rated.values())
    total = ig + df_ + nr
    firm = cube.firm_level.current.totals.committed
    assert total == pytest.approx(firm, abs=DOLLAR_TOL), (
        f"Rating-coverage section_sum ({total:,.2f}) does not "
        f"reconcile to firm committed ({firm:,.2f}). The "
        f"_grouping_history pin has likely regressed for one of "
        f"by_ig_status / by_defaulted / by_non_rated."
    )


# ── 9. status_marker emits the right suffix per lifecycle ─────

def test_status_marker_strings(cube):
    periods = cube.metadata.periods
    assert status_marker(cube.by_industry["Crypto"], periods)     == " (exited)"
    assert status_marker(cube.by_industry["AI Lending"], periods) == " (new this period)"
    assert status_marker(cube.by_industry["Energy"], periods)     == ""
    assert status_marker(cube.by_horizontal["Leveraged Finance"], periods)          == " (exited)"
    assert status_marker(cube.by_horizontal["Global Recovery Management"], periods) == " (new this period)"
    assert status_marker(cube.by_defaulted["Defaulted"], periods)  == " (exited)"
    assert status_marker(cube.by_non_rated["Non-Rated"], periods)  == " (new this period)"


# ── 10. firm_level slicer renders markers in context ──────────

def test_firm_level_renders_markers(cube):
    """The rendered context string must contain the lifecycle markers
    for the exited/new buckets the fixture exercises. This is the
    end-to-end check that the slicer wires `decorate()` into context
    output."""
    payload = slice_firm_level(cube)
    context = payload["context"]
    assert "Leveraged Finance (exited)"           in context
    assert "Global Recovery Management (new this period)" in context
    assert "Defaulted (exited)"                   in context
    assert "Non-Rated (new this period)"          in context
    # Active buckets must NOT carry markers.
    assert "Investment Grade (exited)"            not in context
    assert "Investment Grade (new this period)"   not in context
    assert "Non-Investment Grade (exited)"        not in context


# ── 11. firm_level verifiable_values keys stay plain ──────────

def test_firm_level_verifiable_values_keys_are_plain(cube):
    """Per Option-A: lifecycle markers live in rendered context and
    metric tile labels only — verifiable_values keys must be the plain
    bucket names so the LLM can cite them and the verifier can resolve
    citations. Decorating the keys would silently break verification."""
    payload = slice_firm_level(cube)
    vv = payload["verifiable_values"]
    for key in vv.keys():
        assert "(exited)" not in key, (
            f"verifiable_values key carries '(exited)' suffix: {key!r}. "
            f"Markers belong in context/metrics only — not in verifier keys."
        )
        assert "(new this period)" not in key, (
            f"verifiable_values key carries '(new this period)' suffix: {key!r}."
        )
    # The plain bucket labels MUST be present for citation resolution.
    assert "Defaulted" in vv
    assert "Non-Rated" in vv
    assert "Leveraged Finance" in vv
    assert "Global Recovery Management" in vv


# ── 12. firm_level metrics tiles carry markers ────────────────

def test_firm_level_metrics_tiles_carry_markers(cube):
    payload = slice_firm_level(cube)
    metrics = payload["metrics"]

    horizontal_labels = [tile["label"] for tile in metrics.get("Horizontal Portfolios", [])]
    assert "Leveraged Finance (exited)"           in horizontal_labels
    assert "Global Recovery Management (new this period)" in horizontal_labels

    rating_labels = [tile["label"] for tile in metrics.get("Rating Category Composition", [])]
    assert "Defaulted (exited)"                   in rating_labels
    assert "Non-Rated (new this period)"          in rating_labels


# ── 13. Horizontal-portfolio sort: exits sink to bottom ───────

def test_horizontal_sort_exits_to_bottom(cube):
    """sort_key must push exited horizontals after active ones — the
    rendered context iterates in this order, so an exited bucket
    can't shadow active ones in a top-N callout."""
    payload = slice_firm_level(cube)
    context = payload["context"]
    # GRM (active/new, $25M) must appear before LF (exited, $0).
    grm_idx = context.find("Global Recovery Management")
    lf_idx  = context.find("Leveraged Finance")
    assert grm_idx > 0 and lf_idx > 0
    assert grm_idx < lf_idx, (
        f"Exited horizontal Leveraged Finance should sort after the "
        f"new/active Global Recovery Management. grm_idx={grm_idx}, "
        f"lf_idx={lf_idx}."
    )


# ── 14. MoM populated correctly ───────────────────────────────

def test_month_over_month(cube):
    mom = cube.month_over_month
    assert mom is not None
    expected = ex.EXPECTED["month_over_month"]

    assert len(mom.new_originations)   == expected["new_originations_count"]
    assert len(mom.exits)              == expected["exits_count"]
    assert len(mom.pd_rating_changes)  == expected["pd_rating_changes_count"]
    assert len(mom.reg_rating_changes) == expected["reg_rating_changes_count"]

    actual_new   = {e.facility_id for e in mom.new_originations}
    actual_exits = {e.facility_id for e in mom.exits}
    assert actual_new   == expected["new_origination_facilities"]
    assert actual_exits == expected["exit_facilities"]


# ── 15. Determinism ───────────────────────────────────────────

def test_determinism(lending_df):
    a = compute_lending_cube(lending_df).model_dump_json()
    b = compute_lending_cube(lending_df).model_dump_json()
    assert a == b


# ── 16. History contains only actual-data periods ─────────────

def test_history_only_actual_data_periods(cube):
    """The fix must NOT append empty trailing blocks to history when
    a bucket has no rows in the latest period. history is a list of
    real data points — `current` carries the latest-period view (which
    may be empty)."""
    crypto = cube.by_industry["Crypto"]
    history_periods = [block.period for block in crypto.history]
    assert history_periods == [ex.EXPECTED["prior_period"]], (
        f"Exited Crypto bucket should have history = [P1] only, got "
        f"{history_periods}. An empty P2 block leaking into history "
        f"would re-introduce the over-counting bug."
    )
    assert crypto.current.period == ex.EXPECTED["current_period"]
    assert crypto.current.totals.committed == pytest.approx(0.0, abs=DOLLAR_TOL)
