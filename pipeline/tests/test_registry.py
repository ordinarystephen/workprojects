# ── KRONOS · pipeline/tests/test_registry.py ──────────────────
# Smoke + behavior tests for the YAML-driven mode registry.
#
# These tests load the real config/modes.yaml so a malformed file
# fails CI before it reaches a /upload call. They also lock in the
# parameter validation contract for future refactors.
#
# Run from repo root:
#   python -m pytest pipeline/tests/test_registry.py
# or:
#   python -m unittest pipeline.tests.test_registry
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import unittest

from pipeline.registry import (
    ParameterError,
    get_mode,
    list_active_modes,
    list_modes_for_ui,
    load_prompt,
    load_registry,
    validate_parameters,
)


class TestRegistryLoad(unittest.TestCase):
    def test_registry_loads(self):
        reg = load_registry()
        self.assertGreater(len(reg.modes), 0)

    def test_active_modes_have_slicer_and_prompt(self):
        # load_registry() raises if any active mode is misconfigured —
        # this just asserts that the active set is non-empty so a
        # silent regression (everything turned to placeholder) trips.
        active = list_active_modes()
        self.assertGreater(len(active), 0)
        for m in active:
            self.assertIsNotNone(m.cube_slice, f"{m.slug} missing cube_slice")
            self.assertIsNotNone(m.prompt_template, f"{m.slug} missing prompt_template")

    def test_modes_for_ui_includes_placeholders(self):
        ui_modes = list_modes_for_ui()
        slugs = [m.slug for m in ui_modes]
        self.assertIn("firm-level", slugs)               # active
        self.assertIn("concentration-risk", slugs)       # placeholder

    def test_get_mode_unknown_returns_none(self):
        self.assertIsNone(get_mode("does-not-exist"))


class TestParameterValidation(unittest.TestCase):
    def test_required_parameter_missing_raises(self):
        pl = get_mode("portfolio-level")
        self.assertIsNotNone(pl)
        with self.assertRaises(ParameterError):
            validate_parameters(pl, {})

    def test_unknown_parameter_raises(self):
        pl = get_mode("portfolio-level")
        with self.assertRaises(ParameterError):
            validate_parameters(pl, {"portfolio": "X", "garbage": 1})

    def test_parameterless_mode_accepts_empty(self):
        fl = get_mode("firm-level")
        cleaned = validate_parameters(fl, {})
        self.assertEqual(cleaned, {})

    def test_parameter_value_passes_when_cube_absent(self):
        # Without a cube, enum membership is skipped — only presence /
        # type are enforced. Used by server.py's pre-classify validation.
        pl = get_mode("portfolio-level")
        cleaned = validate_parameters(pl, {"portfolio": "Manufacturing"}, cube=None)
        self.assertEqual(cleaned, {"portfolio": "Manufacturing"})


class TestPromptLoading(unittest.TestCase):
    def test_default_prompt_loads(self):
        text = load_prompt(None)
        self.assertIn("credit portfolio analyst", text)

    def test_active_mode_prompt_loads(self):
        fl = get_mode("firm-level")
        text = load_prompt(fl)
        self.assertIn("firm-level portfolio snapshot", text)

    def test_parameter_substitution(self):
        pl = get_mode("portfolio-level")
        text = load_prompt(pl, {"portfolio": "Manufacturing"})
        self.assertIn("Manufacturing", text)
        self.assertNotIn("{{portfolio}}", text)

    def test_unsubstituted_token_left_when_param_missing(self):
        # Substitution is dumb string replace — missing keys remain
        # in the template. Documented behavior; tests lock it in so
        # future refactors don't silently change semantics.
        pl = get_mode("portfolio-level")
        text = load_prompt(pl, {})
        self.assertIn("{{portfolio}}", text)


if __name__ == "__main__":
    unittest.main()
