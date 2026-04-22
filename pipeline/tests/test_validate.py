# ── KRONOS · pipeline/tests/test_validate.py ──────────────────
# Unit tests for the claim-based verifier.
#
# Run from repo root:
#   python -m pytest pipeline/tests/test_validate.py
# or:
#   python -m unittest pipeline.tests.test_validate
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import unittest
from datetime import date

from pipeline.validate import verify_claims


def _claim(source_field: str, cited_value: str, sentence: str = "s") -> dict:
    return {"sentence": sentence, "source_field": source_field, "cited_value": cited_value}


class TestEmptyInputs(unittest.TestCase):
    def test_empty_claims_returns_no_structured_claims_note(self):
        result = verify_claims([], {"X": {"value": 1, "type": "count"}})
        self.assertEqual(result.total, 0)
        self.assertEqual(result.verified_count, 0)
        self.assertFalse(result.all_clear)
        self.assertIn("no_structured_claims", result.notes)

    def test_no_verifiable_values_marks_all_unverified(self):
        claims = [_claim("anything", "42")]
        result = verify_claims(claims, {})
        self.assertEqual(result.total, 1)
        self.assertEqual(result.unverified_count, 1)
        self.assertEqual(result.claim_results[0].status, "unverified")
        self.assertIn("no_verifiable_values", result.notes)


class TestCountChecks(unittest.TestCase):
    def test_exact_count_verifies(self):
        vv = {"Parents": {"value": 143, "type": "count"}}
        result = verify_claims([_claim("Parents", "143")], vv)
        self.assertEqual(result.verified_count, 1)
        self.assertTrue(result.all_clear)

    def test_count_with_commas_parses(self):
        vv = {"Facilities": {"value": 12345, "type": "count"}}
        result = verify_claims([_claim("Facilities", "12,345")], vv)
        self.assertEqual(result.verified_count, 1)

    def test_count_mismatch(self):
        vv = {"Parents": {"value": 143, "type": "count"}}
        result = verify_claims([_claim("Parents", "144")], vv)
        self.assertEqual(result.mismatch_count, 1)
        self.assertEqual(result.claim_results[0].status, "mismatch")
        self.assertEqual(result.claim_results[0].reason, "value_mismatch")


class TestCurrencyChecks(unittest.TestCase):
    def test_exact_currency_string_verifies(self):
        vv = {"Committed": {"value": 1_234_567.89, "type": "currency"}}
        result = verify_claims([_claim("Committed", "$1,234,567.89")], vv)
        self.assertEqual(result.verified_count, 1)

    def test_billions_rounding_within_tolerance_verifies(self):
        vv = {"Committed": {"value": 1_240_000_000.0, "type": "currency"}}
        result = verify_claims([_claim("Committed", "$1.2B")], vv)
        # $1.2B has tolerance of $50M; actual is $1.24B, within.
        self.assertEqual(result.verified_count, 1, result.claim_results[0])

    def test_billions_rounding_outside_tolerance_mismatches(self):
        vv = {"Committed": {"value": 1_300_000_000.0, "type": "currency"}}
        result = verify_claims([_claim("Committed", "$1.2B")], vv)
        # $1.2B vs $1.30B differs by $100M, tolerance is $50M → mismatch.
        self.assertEqual(result.mismatch_count, 1)

    def test_millions_rounding_verifies(self):
        vv = {"Committed": {"value": 4_820_000_000.0, "type": "currency"}}
        result = verify_claims([_claim("Committed", "$4.82B")], vv)
        self.assertEqual(result.verified_count, 1)

    def test_currency_mismatch_at_same_precision(self):
        vv = {"Committed": {"value": 5_000_000_000.0, "type": "currency"}}
        result = verify_claims([_claim("Committed", "$4.82B")], vv)
        self.assertEqual(result.mismatch_count, 1)


class TestPercentageChecks(unittest.TestCase):
    def test_percentage_within_epsilon(self):
        vv = {"C&C": {"value": 0.0413, "type": "percentage"}}
        result = verify_claims([_claim("C&C", "4.1%")], vv)
        # 4.1% → 0.041, actual 0.0413, diff 0.0003 < 0.0005 epsilon.
        self.assertEqual(result.verified_count, 1)

    def test_percentage_outside_epsilon(self):
        vv = {"C&C": {"value": 0.0420, "type": "percentage"}}
        result = verify_claims([_claim("C&C", "4.1%")], vv)
        # diff 0.001 > epsilon → mismatch
        self.assertEqual(result.mismatch_count, 1)

    def test_percentage_missing_suffix_is_mismatch(self):
        vv = {"C&C": {"value": 0.041, "type": "percentage"}}
        result = verify_claims([_claim("C&C", "4.1")], vv)
        # No % suffix → can't parse → mismatch
        self.assertEqual(result.mismatch_count, 1)


class TestDateChecks(unittest.TestCase):
    def test_iso_date_exact(self):
        vv = {"as of": {"value": date(2026, 2, 28), "type": "date"}}
        result = verify_claims([_claim("as of", "2026-02-28")], vv)
        self.assertEqual(result.verified_count, 1)

    def test_prose_date_matches_iso(self):
        vv = {"as of": {"value": "2026-02-28", "type": "date"}}
        result = verify_claims([_claim("as of", "February 28, 2026")], vv)
        self.assertEqual(result.verified_count, 1)

    def test_date_mismatch(self):
        vv = {"as of": {"value": date(2026, 2, 28), "type": "date"}}
        result = verify_claims([_claim("as of", "2026-03-01")], vv)
        self.assertEqual(result.mismatch_count, 1)


class TestStringChecks(unittest.TestCase):
    def test_string_case_insensitive_whitespace_normalized(self):
        vv = {"Weighted Average PD": {"value": "C06", "type": "string"}}
        result = verify_claims([_claim("Weighted Average PD", "c06")], vv)
        self.assertEqual(result.verified_count, 1)

    def test_string_mismatch(self):
        vv = {"Weighted Average PD": {"value": "C06", "type": "string"}}
        result = verify_claims([_claim("Weighted Average PD", "C07")], vv)
        self.assertEqual(result.mismatch_count, 1)


class TestFieldLookup(unittest.TestCase):
    def test_label_case_insensitive(self):
        vv = {"Committed Exposure": {"value": 100.0, "type": "currency"}}
        result = verify_claims([_claim("committed exposure", "$100.00")], vv)
        self.assertEqual(result.verified_count, 1)

    def test_label_whitespace_tolerant(self):
        vv = {"C&C as % of commitment": {"value": 0.05, "type": "percentage"}}
        result = verify_claims([_claim("C&C  as %   of commitment", "5.00%")], vv)
        self.assertEqual(result.verified_count, 1)

    def test_field_not_found_marks_unverified_not_mismatch(self):
        vv = {"Parents": {"value": 10, "type": "count"}}
        result = verify_claims([_claim("calculated", "42")], vv)
        self.assertEqual(result.unverified_count, 1)
        self.assertEqual(result.claim_results[0].status, "unverified")
        self.assertEqual(result.claim_results[0].reason, "field_not_found")


class TestAggregateFlags(unittest.TestCase):
    def test_all_clear_only_when_every_claim_verified(self):
        vv = {"A": {"value": 1, "type": "count"},
              "B": {"value": 2, "type": "count"}}
        result = verify_claims(
            [_claim("A", "1"), _claim("B", "2")], vv
        )
        self.assertTrue(result.all_clear)

    def test_one_unverified_breaks_all_clear(self):
        vv = {"A": {"value": 1, "type": "count"}}
        result = verify_claims(
            [_claim("A", "1"), _claim("calculated", "99")], vv
        )
        self.assertFalse(result.all_clear)
        self.assertEqual(result.verified_count, 1)
        self.assertEqual(result.unverified_count, 1)


if __name__ == "__main__":
    unittest.main()
