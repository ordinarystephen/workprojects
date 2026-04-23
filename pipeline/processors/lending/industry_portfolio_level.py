# ── KRONOS · pipeline/processors/lending/industry_portfolio_level.py ─
# Industry portfolio-level slicer.
#
# An "industry portfolio" is the partition of facilities by Risk
# Assessment Industry — every facility is in exactly one industry
# (with NaN/blank collapsed to "Unclassified" upstream). Distinct
# from horizontal portfolios (Leveraged Finance, GRM), which are
# boolean overlays — a facility can be in zero, one, or several.
#
# Reads cube.industry_details[<portfolio>] and renders the standard
# slice view via _slice_view.render_slice. The shared renderer keeps
# this slicer thin (parameter validation + lookup + delegation).
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from pipeline.cube.models import LendingCube
from pipeline.registry import ParameterError, register_slicer

from pipeline.processors.lending._slice_view import render_slice


@register_slicer("industry_portfolio_level", required_params=["portfolio"])
def slice_industry_portfolio_level(cube: LendingCube, portfolio: str) -> dict:
    """Build the slice view for one industry portfolio.

    Args:
        cube:       fully-computed LendingCube.
        portfolio:  industry name (must be a key of cube.industry_details).

    Raises:
        ParameterError: if `portfolio` is not a recognized industry on
            this upload. The pre-cube validate_parameters pass cannot
            catch this (cube isn't built yet); cube-aware validation
            does, but slicer guards anyway as a defense-in-depth.
    """
    slice_ = cube.industry_details.get(portfolio)
    if slice_ is None:
        raise ParameterError(
            f"Industry portfolio {portfolio!r} not present in this upload. "
            f"Available: {sorted(cube.industry_details.keys())}"
        )

    firm_committed = cube.firm_level.current.totals.committed
    return render_slice(
        slice_=slice_,
        kind="Industry Portfolio",
        firm_committed=firm_committed,
        as_of=cube.metadata.as_of.isoformat(),
        cube_periods=cube.metadata.periods,
    )
