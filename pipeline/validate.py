# ── KRONOS · pipeline/validate.py ─────────────────────────────
# Number cross-check utility. Compares figures in the LLM narrative
# against the figures in the context string that was sent to the LLM.
#
# How it works:
#   1. Extract every numeric token from the narrative
#      (percentages, dollar amounts, multipliers, plain numbers)
#   2. Extract every numeric token from the context string
#   3. Split the narrative numbers into two buckets:
#        - verified   : appears in the context (the LLM cited real data)
#        - unverified : not in the context (the LLM calculated or inferred it)
#
# "Unverified" does NOT mean wrong. Weighted averages, year-over-year
# deltas, and stress-scenario projections are calculated by the LLM
# from context inputs and won't appear verbatim in the source data.
# Use this as a transparency signal, not a correctness gate.
#
# The result dict is returned by server.py as `verification` in the
# JSON response and rendered as a badge in the UI.
# ──────────────────────────────────────────────────────────────

import re


# ── Numeric token pattern ─────────────────────────────────────
# Matches numbers in financial narrative text.
# Captures:
#   - Optional leading sign: + -
#   - Optional currency symbol: $
#   - One or more digits, with optional decimal point
#   - Optional suffix: % x B M K (percent, multiple, billions, etc.)
#
# Examples matched: 4.1%, +3.2%, $4.82B, 2.3x, 698, -15%, 1.48%
#
# Minimum 2 characters — filters out bare single digits like "1" or "0"
# that appear everywhere in prose and create noise.

_NUMBER_PATTERN = re.compile(
    r'[\+\-]?\$?\d+\.?\d*[%xBMKbmk]?'
)

# Dates are metadata, not figures. We strip them from BOTH the narrative
# and the context before tokenizing — otherwise an ISO date in the context
# ("2026-02-28") and the LLM's rewrite of it in the narrative
# ("February 28, 2026" / "Feb 28, 2026") tokenize differently and produce
# false-positive unverified counts. We strip both shapes to avoid leaving
# half a date on either side.
_ISO_DATE_PATTERN  = re.compile(r'\b\d{4}-\d{2}-\d{2}\b')
_PROSE_DATE_PATTERN = re.compile(
    r'\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
    r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
    r'\s+\d{1,2}(?:,\s*\d{4})?',
    re.IGNORECASE,
)

_MIN_TOKEN_LENGTH = 2   # ignore tokens shorter than this
_MIN_DIGIT_COUNT  = 1   # ignore tokens with fewer than N digit characters


def _extract_numbers(text: str) -> set:
    """
    Extract all numeric tokens from a text string.
    Returns a set of strings (e.g. {'4.1%', '$4.82B', '698'}).
    """
    text = _ISO_DATE_PATTERN.sub(' ', text)
    text = _PROSE_DATE_PATTERN.sub(' ', text)
    tokens = _NUMBER_PATTERN.findall(text)
    result = set()
    for token in tokens:
        # Skip tokens that are too short to be meaningful
        if len(token) < _MIN_TOKEN_LENGTH:
            continue
        # Skip tokens that are purely symbolic (e.g. just "$" or "+")
        digit_count = sum(1 for c in token if c.isdigit())
        if digit_count < _MIN_DIGIT_COUNT:
            continue
        result.add(token)
    return result


def cross_check_numbers(narrative: str, context: str) -> dict:
    """
    Cross-checks the numeric figures in the LLM narrative against
    the data that was actually sent to the LLM.

    Args:
        narrative : The narrative text returned by the LLM.
        context   : The context string that was sent to the LLM
                    (the deterministic data payload — same as context_sent).

    Returns:
        dict:
            total           : int — total distinct numeric tokens in narrative
            verified_count  : int — tokens that also appear in context
            unverified_count: int — tokens not found in context
            unverified      : list[str] — the specific unverified values
            all_clear       : bool — True if all numbers are verified

    Example return:
        {
            "total": 14,
            "verified_count": 11,
            "unverified_count": 3,
            "unverified": ["+15%", "1.7x", "100"],
            "all_clear": False
        }
    """

    if not narrative or not context:
        return {
            "total": 0,
            "verified_count": 0,
            "unverified_count": 0,
            "unverified": [],
            "all_clear": True,
        }

    narrative_nums = _extract_numbers(narrative)
    context_nums   = _extract_numbers(context)

    verified   = narrative_nums & context_nums
    unverified = narrative_nums - context_nums

    return {
        "total":            len(narrative_nums),
        "verified_count":   len(verified),
        "unverified_count": len(unverified),
        "unverified":       sorted(list(unverified)),
        "all_clear":        len(unverified) == 0,
    }
