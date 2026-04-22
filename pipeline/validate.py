# ── KRONOS · pipeline/validate.py ─────────────────────────────
# Claim-based verification.
#
# The LLM returns structured claims alongside the narrative — each
# claim is { sentence, source_field, cited_value }. The slicers publish
# a parallel dictionary of verifiable_values (label → { value, type })
# that names every figure the LLM could legitimately cite.
#
# For each claim:
#   1. Look up claim.source_field in verifiable_values.
#      Missing → unverified (reason: field_not_found).
#      Labels match on normalized whitespace + case-insensitive compare.
#   2. Compare claim.cited_value against the real value using the
#      type-appropriate tolerance:
#        count      — integer exact match
#        currency   — match within the rounding precision the LLM used
#                     (e.g. "$1.2B" matches any value in $1.15B–$1.25B)
#        percentage — within ±0.05 percentage points
#        date       — normalized ISO date exact match
#        string     — whitespace-normalized, case-insensitive equality
#   3. Produce a per-claim ClaimVerification row.
#
# The overall result is all_clear only if every claim verifies.
# Mismatches are the dangerous case — the LLM named a real field but
# cited the wrong value. Unverified (field_not_found / computed) is
# the transparency case — the LLM is drawing an inference we can't
# automatically check.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ══════════════════════════════════════════════════════════════
# ── RESULT MODELS ─────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

ClaimStatus = Literal["verified", "unverified", "mismatch"]


class ClaimVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_index: int
    status:      ClaimStatus
    reason:      Optional[str] = None
    expected:    Optional[str] = None
    actual:      Optional[str] = None


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total:             int
    verified_count:    int
    unverified_count:  int
    mismatch_count:    int
    all_clear:         bool
    claim_results:     list[ClaimVerification] = Field(default_factory=list)
    notes:             list[str] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════
# ── PARSERS ───────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

_CURRENCY_RE  = re.compile(r'[\+\-]?\$?\s*([\d,]+(?:\.\d+)?)\s*([BMKbmk]?)')
_PERCENT_RE   = re.compile(r'[\+\-]?([\d,]+(?:\.\d+)?)\s*%')
_NUMBER_RE    = re.compile(r'[\+\-]?[\d,]+(?:\.\d+)?')
_ISO_DATE_RE  = re.compile(r'\b(\d{4})-(\d{2})-(\d{2})\b')
_PROSE_DATE_FORMATS = (
    "%B %d, %Y",
    "%b %d, %Y",
    "%m/%d/%Y",
    "%Y-%m-%d",
)

_SUFFIX_MULT = {"": 1.0, "B": 1e9, "M": 1e6, "K": 1e3}


def parse_currency(s: str) -> Optional[tuple[float, str, int]]:
    """Parse a currency string. Returns (value_usd, suffix, decimals)."""
    if not s:
        return None
    m = _CURRENCY_RE.search(s.replace(",", "").strip())
    if not m:
        return None
    try:
        base = float(m.group(1))
    except ValueError:
        return None
    suffix = m.group(2).upper()
    decimals_m = re.search(r'\.(\d+)', m.group(1))
    decimals = len(decimals_m.group(1)) if decimals_m else 0
    return (base * _SUFFIX_MULT[suffix], suffix, decimals)


def parse_percentage(s: str) -> Optional[float]:
    """Parse '4.1%' → 0.041, or '4.1' (no suffix) → None."""
    if not s:
        return None
    m = _PERCENT_RE.search(s.replace(",", "").strip())
    if not m:
        return None
    try:
        return float(m.group(1)) / 100.0
    except ValueError:
        return None


def parse_count(s: str) -> Optional[int]:
    """Parse an integer, stripping commas and optional leading sign."""
    if s is None:
        return None
    stripped = str(s).replace(",", "").strip()
    m = _NUMBER_RE.search(stripped)
    if not m:
        return None
    try:
        return int(round(float(m.group(0))))
    except ValueError:
        return None


def parse_date(s: str) -> Optional[str]:
    """Return an ISO-formatted date string or None."""
    if not s:
        return None
    stripped = str(s).strip()
    iso = _ISO_DATE_RE.search(stripped)
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)}"
    for fmt in _PROSE_DATE_FORMATS:
        try:
            dt = datetime.strptime(stripped, fmt)
            return dt.date().isoformat()
        except ValueError:
            continue
    return None


# ══════════════════════════════════════════════════════════════
# ── TOLERANCE CHECKS ──────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

_PCT_EPSILON_DECIMAL = 0.0005  # ±0.05 percentage points


def _fmt_currency(v: float) -> str:
    return f"${v:,.2f}"


def _fmt_percent(v: float) -> str:
    return f"{v * 100:.2f}%"


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip()


def _normalize_label(s: str) -> str:
    return _normalize_ws(s).lower()


def _check_count(expected: Any, cited: str) -> tuple[bool, str, str]:
    try:
        expected_val = int(round(float(expected)))
    except (ValueError, TypeError):
        return (False, "expected_not_numeric", str(expected))
    cited_val = parse_count(cited)
    if cited_val is None:
        return (False, "cited_not_numeric", f"{expected_val:,}")
    if cited_val == expected_val:
        return (True, "", f"{expected_val:,}")
    return (False, "value_mismatch", f"{expected_val:,}")


def _check_currency(expected: Any, cited: str) -> tuple[bool, str, str]:
    try:
        expected_val = float(expected)
    except (ValueError, TypeError):
        return (False, "expected_not_numeric", str(expected))
    parsed = parse_currency(cited)
    if parsed is None:
        return (False, "cited_not_numeric", _fmt_currency(expected_val))
    cited_val, suffix, decimals = parsed
    # Tolerance = half of one unit at the precision the LLM cited.
    # "$1.2B" → tolerance = 0.05 × 1e9 = 5e7.
    # "$1,234,567.89" (no suffix, 2 decimals) → tolerance = $0.005.
    unit = _SUFFIX_MULT[suffix] * (10 ** -decimals)
    tolerance = unit / 2.0
    if abs(cited_val - expected_val) <= tolerance:
        return (True, "", _fmt_currency(expected_val))
    return (False, "value_mismatch", _fmt_currency(expected_val))


def _check_percentage(expected: Any, cited: str) -> tuple[bool, str, str]:
    try:
        expected_val = float(expected)
    except (ValueError, TypeError):
        return (False, "expected_not_numeric", str(expected))
    cited_val = parse_percentage(cited)
    if cited_val is None:
        return (False, "cited_not_percentage", _fmt_percent(expected_val))
    if abs(cited_val - expected_val) <= _PCT_EPSILON_DECIMAL:
        return (True, "", _fmt_percent(expected_val))
    return (False, "value_mismatch", _fmt_percent(expected_val))


def _check_date(expected: Any, cited: str) -> tuple[bool, str, str]:
    if isinstance(expected, datetime):
        expected_iso = expected.date().isoformat()
    elif isinstance(expected, date):
        expected_iso = expected.isoformat()
    else:
        expected_iso = parse_date(str(expected))
    if expected_iso is None:
        return (False, "expected_not_date", str(expected))
    cited_iso = parse_date(cited)
    if cited_iso is None:
        return (False, "cited_not_date", expected_iso)
    if cited_iso == expected_iso:
        return (True, "", expected_iso)
    return (False, "value_mismatch", expected_iso)


def _check_string(expected: Any, cited: str) -> tuple[bool, str, str]:
    expected_str = _normalize_ws(str(expected))
    cited_str    = _normalize_ws(cited)
    if expected_str.lower() == cited_str.lower():
        return (True, "", expected_str)
    return (False, "value_mismatch", expected_str)


_CHECKERS = {
    "count":      _check_count,
    "currency":   _check_currency,
    "percentage": _check_percentage,
    "date":       _check_date,
    "string":     _check_string,
}


# ══════════════════════════════════════════════════════════════
# ── MAIN ENTRY POINT ──────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

def verify_claims(
    claims: list[dict],
    verifiable_values: dict,
) -> VerificationResult:
    """
    Verify structured LLM claims against the slicer-published catalog
    of verifiable values.

    Args:
        claims : list of claim dicts with keys {sentence, source_field, cited_value}.
        verifiable_values : mapping of label → {"value": Any, "type": str}.
                            The slicer that produced the context owns this dict.

    Returns:
        VerificationResult — aggregate counts plus per-claim rows.
    """

    if not claims:
        return VerificationResult(
            total=0, verified_count=0, unverified_count=0, mismatch_count=0,
            all_clear=False, claim_results=[], notes=["no_structured_claims"],
        )

    if not verifiable_values:
        results = [
            ClaimVerification(
                claim_index=i, status="unverified", reason="no_verifiable_values",
                expected=None, actual=(c.get("cited_value") or None),
            )
            for i, c in enumerate(claims)
        ]
        return VerificationResult(
            total=len(claims), verified_count=0,
            unverified_count=len(claims), mismatch_count=0,
            all_clear=False, claim_results=results,
            notes=["no_verifiable_values"],
        )

    # Pre-compute a normalized lookup so label matching tolerates spacing / case.
    label_lookup = {_normalize_label(k): (k, v) for k, v in verifiable_values.items()}

    results: list[ClaimVerification] = []
    verified_ct = 0
    mismatch_ct = 0

    for i, claim in enumerate(claims):
        source_field = (claim.get("source_field") or "").strip()
        cited_value  = (claim.get("cited_value") or "").strip()

        hit = label_lookup.get(_normalize_label(source_field))
        if hit is None:
            results.append(ClaimVerification(
                claim_index=i, status="unverified",
                reason="field_not_found",
                expected=None, actual=cited_value or None,
            ))
            continue

        _, spec = hit
        expected_value = spec.get("value") if isinstance(spec, dict) else spec
        claim_type     = (spec.get("type") if isinstance(spec, dict) else "string") or "string"
        checker = _CHECKERS.get(claim_type, _check_string)

        ok, reason, expected_rendered = checker(expected_value, cited_value)
        if ok:
            verified_ct += 1
            results.append(ClaimVerification(
                claim_index=i, status="verified",
                reason=None, expected=expected_rendered,
                actual=cited_value or None,
            ))
        else:
            mismatch_ct += 1
            results.append(ClaimVerification(
                claim_index=i, status="mismatch",
                reason=reason, expected=expected_rendered,
                actual=cited_value or None,
            ))

    unverified_ct = len(claims) - verified_ct - mismatch_ct
    return VerificationResult(
        total=len(claims),
        verified_count=verified_ct,
        unverified_count=unverified_ct,
        mismatch_count=mismatch_ct,
        all_clear=(verified_ct == len(claims)),
        claim_results=results,
    )
