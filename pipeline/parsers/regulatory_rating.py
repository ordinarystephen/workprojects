# ── KRONOS · pipeline/parsers/regulatory_rating.py ────────────
# Parser for the "Current Month Regulatory Rating" field.
#
# Possible shapes in the source data:
#   Single rating:  "Pass"  /  "SS"  /  "Substandard"
#   Split rating:   "SS - 18%, D - 42%, L - 40%"
#                   "SS - 54%, D - 46%"
#   Components are <rating> - <percent>%, separated by commas.
#   They may or may not sum to exactly 100% (rounding tolerated).
#
# Output shape: list of (canonical_code, fraction) tuples.
#   fraction is in [0, 1]; we keep canonical OCC codes:
#     "Pass" | "SM" | "SS" | "Dbt" | "Loss"
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import re
from typing import Optional


# Canonical OCC codes in worsening order. Any rating change going
# RIGHT in this list is a downgrade.
ORDER: list[str] = ["Pass", "SM", "SS", "Dbt", "Loss"]
_INDEX: dict[str, int] = {code: i for i, code in enumerate(ORDER)}


# Aliases the source data might use → canonical code.
# Add here if new shorthand surfaces in real exports.
_ALIASES: dict[str, str] = {
    "P":               "Pass",
    "PASS":            "Pass",
    "SM":              "SM",
    "SPECIAL MENTION": "SM",
    "SPECIALMENTION":  "SM",
    "SS":              "SS",
    "SUBSTANDARD":     "SS",
    "SUB":             "SS",
    "D":               "Dbt",
    "DBT":             "Dbt",
    "DOUBT":           "Dbt",
    "DOUBTFUL":        "Dbt",
    "L":               "Loss",
    "LOSS":            "Loss",
}


def canonicalize(token: str) -> Optional[str]:
    """Map a free-form rating token to its canonical code, or None if unknown."""
    if token is None:
        return None
    key = re.sub(r"\s+", "", str(token)).upper()
    return _ALIASES.get(key)


# Matches one component like "SS - 18%" or "Pass - 100 %" or "D-42%".
_COMPONENT_RE = re.compile(
    r"""
    \s*
    (?P<token>[A-Za-z][A-Za-z\s]*?)   # rating token (letters / spaces)
    \s*-\s*
    (?P<pct>\d+(?:\.\d+)?)            # percent (int or decimal)
    \s*%?\s*
    """,
    re.VERBOSE,
)


def parse(value: object) -> list[tuple[str, float]]:
    """
    Parse a Current Month Regulatory Rating cell value.

    Returns a list of (canonical_code, fraction) tuples, normalized to
    canonical order. Fraction is in [0, 1].

    A single-rating cell ("Pass", "SS") returns [(code, 1.0)].
    Unparseable cells return an empty list.
    """
    if value is None:
        return []

    raw = str(value).strip()
    if not raw:
        return []

    # ── Try the split-rating syntax first ──────────────────────
    matches = list(_COMPONENT_RE.finditer(raw))
    if matches and any("%" in m.group(0) or "-" in m.group(0) for m in matches):
        components: list[tuple[str, float]] = []
        for m in matches:
            code = canonicalize(m.group("token"))
            if code is None:
                continue
            try:
                pct = float(m.group("pct"))
            except ValueError:
                continue
            components.append((code, pct / 100.0))
        if components:
            return _normalize(components)

    # ── Fallback: treat the whole cell as a single rating ─────
    code = canonicalize(raw)
    if code is not None:
        return [(code, 1.0)]

    return []


def _normalize(components: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Combine duplicate codes and sort by canonical order."""
    merged: dict[str, float] = {}
    for code, frac in components:
        merged[code] = merged.get(code, 0.0) + frac
    return sorted(merged.items(), key=lambda kv: _INDEX.get(kv[0], 999))


def equals(a: object, b: object) -> bool:
    """
    Robust equality between two regulatory-rating cell values.

    Compares parsed normalized tuples so "SS - 18%, D - 42%, L - 40%"
    equals "D - 42%, SS - 18%, L - 40%" (component order is irrelevant).
    Tolerates small percentage rounding noise (within 0.5pp per component).
    """
    pa, pb = parse(a), parse(b)
    if len(pa) != len(pb):
        return False
    for (ca, fa), (cb, fb) in zip(pa, pb):
        if ca != cb:
            return False
        if abs(fa - fb) > 0.005:
            return False
    return True


def worst_code(value: object) -> Optional[str]:
    """The most-severe component on a (potentially split) rating cell."""
    parts = parse(value)
    if not parts:
        return None
    return max((c for c, _ in parts), key=lambda c: _INDEX.get(c, -1))


def index_of(code: Optional[str]) -> Optional[int]:
    """Position on the OCC scale. Pass → 0, Loss → 4. Unknown → None."""
    if code is None:
        return None
    return _INDEX.get(code)


def direction(prior_value: object, current_value: object) -> Optional[str]:
    """
    Direction of a regulatory-rating change, judged by the worst component
    on each side.

    Returns "upgrade" / "downgrade" / "unchanged" / None.
    """
    p = index_of(worst_code(prior_value))
    c = index_of(worst_code(current_value))
    if p is None or c is None:
        return None
    if c < p:
        return "upgrade"
    if c > p:
        return "downgrade"
    return "unchanged"


def format_percent(fraction: float) -> str:
    """Render a [0, 1] fraction as 'XX.XX%' for display."""
    return f"{fraction * 100:.2f}%"
