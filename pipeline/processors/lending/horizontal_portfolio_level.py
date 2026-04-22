# ── KRONOS · pipeline/processors/lending/horizontal_portfolio_level.py ─
# Horizontal portfolio-level slicer.
#
# A "horizontal portfolio" is a boolean-flag overlay (e.g. Leveraged
# Finance, Global Recovery Management). Unlike industry portfolios
# (which partition the book), horizontals can overlap with each other
# and with industries — a facility can be in zero, one, or several.
# Membership rules live on LendingTemplate.horizontal_definitions().
#
# Reads cube.horizontal_details[<portfolio>] and renders the standard
# slice view via _slice_view.render_slice.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from pipeline.cube.models import LendingCube
from pipeline.registry import ParameterError, register_slicer

from pipeline.processors.lending._slice_view import render_slice


@register_slicer("horizontal_portfolio_level", required_params=["portfolio"])
def slice_horizontal_portfolio_level(cube: LendingCube, portfolio: str) -> dict:
    """Build the slice view for one horizontal portfolio.

    Args:
        cube:       fully-computed LendingCube.
        portfolio:  horizontal-portfolio name (must be a key of
                    cube.horizontal_details).

    Raises:
        ParameterError: if `portfolio` is not a recognized horizontal
            on this upload. cube-aware validate_parameters catches
            this earlier; slicer guards as defense-in-depth.
    """
    slice_ = cube.horizontal_details.get(portfolio)
    if slice_ is None:
        raise ParameterError(
            f"Horizontal portfolio {portfolio!r} not present in this upload. "
            f"Available: {sorted(cube.horizontal_details.keys())}"
        )

    firm_committed = cube.firm_level.current.totals.committed
    return render_slice(
        slice_=slice_,
        kind="Horizontal Portfolio",
        firm_committed=firm_committed,
        as_of=cube.metadata.as_of.isoformat(),
    )
