# ── KRONOS · pipeline/tests/test_upload_length.py ─────────────
# Integration tests for the request-level `length` field on /upload.
#
# Phase 3 of the Scope × Length refactor (Round 19) wires `length`
# end-to-end:  request body → server.validate_length → ask_agent →
# State → load_prompt → compose_prompt. These tests exercise the
# request-boundary half of that chain (validation + propagation to
# ask_agent) without spinning up Azure OpenAI or LangGraph.
#
# Pattern: stub server.analyze (no real workbook needed) and spy on
# server.ask_agent (capture the kwargs each /upload passes through),
# then drive the Flask test client with both JSON and multipart bodies.
#
# Three behaviours per transport:
#   1. valid length values reach ask_agent verbatim
#   2. invalid length returns 400 with the valid-values list
#   3. omitted length defaults to "full"
#
# Run from repo root:
#   pytest pipeline/tests/test_upload_length.py
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import base64
import io
import json
import unittest
from unittest.mock import patch

import pytest

# server.py transitively imports mlflow + langgraph (via pipeline.agent).
# Both are heavy production deps that ship in the Domino / bank Python
# environment but aren't required to develop the registry layer locally.
# Skip cleanly when either is absent so the test module never causes a
# collection error on a stripped-down dev env. The tests still run in
# every environment that satisfies requirements.txt.
pytest.importorskip("mlflow")
pytest.importorskip("langgraph")
try:
    # mlflow 3.x added the ResponsesAgent surface that pipeline/agent.py
    # imports unconditionally. mlflow 2.x ships the module but not the
    # symbol — probe explicitly so we skip on older installs (incl. local
    # Python 3.9 environments).
    from mlflow.types.responses import ResponsesAgentRequest  # noqa: F401
except ImportError:
    pytest.skip(
        "needs mlflow >= 3 (ResponsesAgentRequest); requires Python 3.10+",
        allow_module_level=True,
    )

import server  # noqa: E402  (intentional — must follow importorskip)


# Empty xlsx-signature bytes — server.analyze is stubbed out so the
# actual contents are never parsed; we just need a non-empty payload
# the upload route accepts as "a file".
_FAKE_FILE_BYTES = b"PK\x03\x04kronos-test"
_FAKE_FILE_B64   = base64.b64encode(_FAKE_FILE_BYTES).decode("ascii")


def _stub_analyze_result() -> dict:
    """The minimum analyze() return shape /upload reads from."""
    return {
        "context":           "stub context",
        "metrics":           {},
        "verifiable_values": {},
    }


def _stub_agent_response() -> dict:
    return {"narrative": "stub narrative", "claims": []}


class _UploadLengthBase(unittest.TestCase):
    """Shared setup: Flask test client + stubs for analyze + ask_agent.

    Each test reads `self.captured_kwargs` to assert what `length` value
    server.upload() forwarded to ask_agent. No real LLM call happens —
    the spy returns a canned response."""

    def setUp(self):
        self.app = server.app.test_client()

        self.captured_kwargs: dict = {}

        def _spy_ask_agent(**kwargs):
            self.captured_kwargs = kwargs
            return _stub_agent_response()

        # patch.object so the module attribute server.analyze /
        # server.ask_agent is swapped — matches how server.upload()
        # resolves the names at call time.
        self._analyze_patch = patch.object(
            server, "analyze", return_value=_stub_analyze_result()
        )
        self._ask_agent_patch = patch.object(
            server, "ask_agent", side_effect=_spy_ask_agent
        )
        self._analyze_patch.start()
        self._ask_agent_patch.start()

    def tearDown(self):
        self._analyze_patch.stop()
        self._ask_agent_patch.stop()

    # ── Request helpers ─────────────────────────────────────────

    def _post_json(self, **overrides):
        """JSON-transport upload. Caller can override / add fields
        (e.g. length="executive", or omit length entirely by passing
        length=None — the helper drops None values)."""
        body = {
            "file_b64":  _FAKE_FILE_B64,
            "file_name": "test.xlsx",
            "prompt":    "What is the firm's exposure?",
            "mode":      "",   # no canned mode → skip parameter validation path
            "parameters": {},
        }
        body.update(overrides)
        body = {k: v for k, v in body.items() if v is not None}
        return self.app.post(
            "/upload",
            data=json.dumps(body),
            content_type="application/json",
        )

    def _post_multipart(self, **overrides):
        """Multipart upload. Same override semantics as _post_json."""
        fields = {
            "prompt":     "What is the firm's exposure?",
            "mode":       "",
            "parameters": "",
        }
        fields.update(overrides)
        fields = {k: v for k, v in fields.items() if v is not None}

        # Werkzeug's test client expects bytes-like file payloads.
        data = dict(fields)
        data["file"] = (io.BytesIO(_FAKE_FILE_BYTES), "test.xlsx")
        return self.app.post(
            "/upload",
            data=data,
            content_type="multipart/form-data",
        )


# ══════════════════════════════════════════════════════════════
# ── JSON transport ────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

class TestUploadLengthJson(_UploadLengthBase):

    def test_full_length_reaches_agent(self):
        resp = self._post_json(length="full")
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertEqual(self.captured_kwargs.get("length"), "full")

    def test_executive_length_reaches_agent(self):
        resp = self._post_json(length="executive")
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertEqual(self.captured_kwargs.get("length"), "executive")

    def test_distillation_length_reaches_agent(self):
        resp = self._post_json(length="distillation")
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertEqual(self.captured_kwargs.get("length"), "distillation")

    def test_omitted_length_defaults_to_full(self):
        resp = self._post_json(length=None)  # field omitted entirely
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertEqual(self.captured_kwargs.get("length"), "full")

    def test_empty_length_defaults_to_full(self):
        # Empty string treated identically to omission — both fall through
        # validate_length() to the default. Important for UI surfaces that
        # send all fields unconditionally.
        resp = self._post_json(length="")
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertEqual(self.captured_kwargs.get("length"), "full")

    def test_invalid_length_returns_400_with_valid_values(self):
        resp = self._post_json(length="short")
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertIn("error", body)
        msg = body["error"]
        # Message must include the offending value AND every valid key
        # so a UI can surface the list to the user without a second call.
        self.assertIn("'short'", msg)
        self.assertIn("full", msg)
        self.assertIn("executive", msg)
        self.assertIn("distillation", msg)
        # ask_agent must NOT have been called — validation runs before
        # the LLM stage.
        self.assertEqual(self.captured_kwargs, {})

    def test_invalid_length_is_case_sensitive(self):
        # "Full" != "full" — the UI must send a known lowercase key.
        # validate_length is intentionally strict; this test pins that
        # behavior so a future refactor doesn't add silent lowercasing.
        resp = self._post_json(length="Full")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.captured_kwargs, {})


# ══════════════════════════════════════════════════════════════
# ── Multipart transport ───────────────────────────────────────
# ══════════════════════════════════════════════════════════════

class TestUploadLengthMultipart(_UploadLengthBase):

    def test_executive_length_reaches_agent(self):
        resp = self._post_multipart(length="executive")
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertEqual(self.captured_kwargs.get("length"), "executive")

    def test_omitted_length_defaults_to_full(self):
        resp = self._post_multipart(length=None)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertEqual(self.captured_kwargs.get("length"), "full")

    def test_invalid_length_returns_400_with_valid_values(self):
        resp = self._post_multipart(length="bogus")
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertIn("error", body)
        msg = body["error"]
        self.assertIn("'bogus'", msg)
        self.assertIn("full", msg)
        self.assertIn("executive", msg)
        self.assertIn("distillation", msg)
        self.assertEqual(self.captured_kwargs, {})


if __name__ == "__main__":
    unittest.main()
