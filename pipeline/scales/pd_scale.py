# ── KRONOS · pipeline/scales/pd_scale.py ──────────────────────
# Internal PD rating scale (C00..CDF).
#
# Convention (confirmed):
#   - Boundary is "≤ upper_bound": a PD value v gets the FIRST code
#     in scale order whose upper_bound >= v.
#   - Order: C00 (best) → CDF (worst). Lower index = upgrade.
#   - Cutoff: C00..C07 = investment grade, C08..CDF = non-IG.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from typing import Iterable, Optional


# Ordered best → worst. Each tuple = (code, upper_bound_inclusive).
# A PD value v maps to the FIRST code whose upper bound >= v.
_SCALE: list[tuple[str, float]] = [
    ("C00", 0.0),
    ("C01", 0.0002),
    ("C02", 0.0005),
    ("C03", 0.0012),
    ("C04", 0.0025),
    ("C05", 0.005),
    ("C06", 0.008),
    ("C07", 0.013),
    ("C08", 0.021),
    ("C09", 0.035),
    ("C10", 0.06),
    ("C11", 0.1),
    ("C12", 0.17),
    ("C13", 0.27),
    ("CDF", 1.0),
]

# Lookups derived from _SCALE (build once at import time).
_INDEX:        dict[str, int]   = {code: i for i, (code, _) in enumerate(_SCALE)}
_UPPER_BOUND:  dict[str, float] = {code: ub for code, ub in _SCALE}
_ORDERED_CODES: list[str]       = [code for code, _ in _SCALE]

# Investment grade cutoff: codes at this index or BETTER are IG.
# C07 is the worst IG code; C08 is the first NIG code.
IG_CUTOFF_INDEX = _INDEX["C07"]


# ── Mapping ───────────────────────────────────────────────────

def code_for_pd(pd_value: Optional[float]) -> Optional[str]:
    """
    Map a raw decimal PD to its rating code on the internal scale.

    Returns None if pd_value is None or NaN.
    Negative values clamp to C00; values > 1 clamp to CDF.
    """
    if pd_value is None:
        return None
    try:
        v = float(pd_value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    if v <= 0:
        return "C00"
    if v >= 1:
        return "CDF"
    for code, ub in _SCALE:
        if v <= ub:
            return code
    return "CDF"  # unreachable given the table above


# ── Ordering / comparison ─────────────────────────────────────

def index_of(code: Optional[str]) -> Optional[int]:
    """Position on the scale. C00 → 0, CDF → last. Unknown codes → None."""
    if code is None:
        return None
    return _INDEX.get(str(code).strip().upper())


def is_investment_grade(code: Optional[str]) -> Optional[bool]:
    """True if the code is C00..C07. None for unknown / missing codes."""
    idx = index_of(code)
    if idx is None:
        return None
    return idx <= IG_CUTOFF_INDEX


def direction(prior_code: Optional[str], current_code: Optional[str]) -> Optional[str]:
    """
    Direction of a rating change.

    Returns:
        "upgrade"   — current is better than prior (lower index)
        "downgrade" — current is worse than prior (higher index)
        "unchanged" — same code (or both None for the same row, in which case
                      the caller usually skips)
        None        — either code is unknown
    """
    p = index_of(prior_code)
    c = index_of(current_code)
    if p is None or c is None:
        return None
    if c < p:
        return "upgrade"
    if c > p:
        return "downgrade"
    return "unchanged"


# ── Convenience accessors ─────────────────────────────────────

def all_codes() -> list[str]:
    """Codes in scale order (best → worst)."""
    return list(_ORDERED_CODES)


def investment_grade_codes() -> list[str]:
    """Subset of codes considered investment grade."""
    return _ORDERED_CODES[: IG_CUTOFF_INDEX + 1]


def non_investment_grade_codes() -> list[str]:
    """Subset of codes considered non-investment grade."""
    return _ORDERED_CODES[IG_CUTOFF_INDEX + 1 :]


def upper_bound(code: str) -> float:
    """Upper-bound PD value for a given rating code."""
    return _UPPER_BOUND[str(code).strip().upper()]
