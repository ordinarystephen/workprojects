# pipeline/tests/test_label_collisions.py

import pytest
from pipeline.slicers.firm_level import firm_level_slicer
# adjust imports to match actual module paths


def test_industry_name_appearing_in_multiple_sections_does_not_collide():
    """
    'Manufacturing' can legitimately appear as both a top-industry entry
    and a watchlist-industry entry. The verifiable_values dict must
    preserve both so the verifier can check either citation.
    """
    cube = _build_cube_with_colliding_industry("Manufacturing")

    result = firm_level_slicer(cube)
    vv = result["verifiable_values"]

    # Both sections should have published a verifiable entry for Manufacturing.
    # If the slicer uses a flat dict, one will have silently overwritten the other.
    manufacturing_keys = [k for k in vv.keys() if "Manufacturing" in k]

    assert len(manufacturing_keys) >= 2, (
        f"Expected Manufacturing to appear in multiple sections with "
        f"disambiguated labels, found only: {manufacturing_keys}"
    )

    # Each entry should carry distinct values (otherwise disambiguation
    # is cosmetic and the underlying collision still exists).
    values = {vv[k]["value"] for k in manufacturing_keys}
    assert len(values) > 1, (
        "Labels are disambiguated but point to the same value — "
        "check that each section's slicer is writing its own data."
    )


def _build_cube_with_colliding_industry(industry_name: str):
    """
    Construct a minimal cube where `industry_name` legitimately appears
    in both top-industries and watchlist sections with different values.
    Adjust the shape to match your actual pydantic cube model.
    """
    return YourCubeModel(
        by_industry={
            industry_name: {"committed": 5_000_000_000, "outstanding": 3_000_000_000},
            "Technology":   {"committed": 4_000_000_000, "outstanding": 2_500_000_000},
        },
        watchlist={
            "by_industry": {
                industry_name: {"committed": 800_000_000, "count": 12},
                "Energy":       {"committed": 400_000_000, "count": 7},
            }
        },
        # ... other required fields, minimal values
    )