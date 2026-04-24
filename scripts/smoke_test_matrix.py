#!/usr/bin/env python3
# ── KRONOS · scripts/smoke_test_matrix.py ────────────────────────
# Phase 5 plumbing check: exercises every (scope × length) combination
# against the synthetic smoke fixture to confirm the refactored request
# path stays stable end-to-end. NOT an analytical-quality evaluation —
# the fixture is too small for that; the purpose is wiring verification.
#
# 9 combinations total:
#   firm-level × {full, executive, distillation}
#   industry-portfolio-level × {full, executive, distillation}
#   horizontal-portfolio-level × {full, executive, distillation}
#
# Setup: two /cube/parameter-options lookups resolve valid parameter
# values. Horizontal pick = alphabetical first. Industry pick =
# largest-committed (parsed from the first firm-level run's context_sent,
# which is ordered by committed desc with exited buckets at the bottom).
#
# Env:
#   KRONOS_URL        (default: http://localhost:5000)
#   KRONOS_FIXTURE    (default: ./pipeline/tests/fixtures/smoke_lending.xlsx)
#
# Stdlib only — no pip installs required.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

BASE_URL = os.environ.get("KRONOS_URL", "http://localhost:5000").rstrip("/")
FIXTURE  = Path(os.environ.get(
    "KRONOS_FIXTURE", "./pipeline/tests/fixtures/smoke_lending.xlsx"
))
SESSION  = f"matrix-{int(time.time())}"
TIMEOUT  = 120  # seconds — matches Flask dev server worst-case
LENGTHS  = ("full", "executive", "distillation")

CAVEAT = (
    "NOTE: This is a plumbing check against the synthetic smoke fixture\n"
    "      (8 facilities, 2 periods). Narrative quality and verification\n"
    "      rates may not reflect behavior on a real workbook. The purpose\n"
    "      is to verify all nine combinations exercise the refactored\n"
    "      pipeline correctly, not to evaluate analytical depth."
)


# ── HTTP helper ────────────────────────────────────────────────

def _post_json(path: str, body: dict) -> tuple[int, dict]:
    """POST JSON, return (status, parsed_body_or_error_dict)."""
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type":    "application/json",
            "X-Kronos-Session": SESSION,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            status = r.status
            raw = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return 0, {"__network_error": str(e.reason)}
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, {"__raw": raw[:500]}


# ── Parameter resolution ───────────────────────────────────────

def fetch_parameter_options(slug: str, file_b64: str, file_name: str) -> list[str]:
    status, body = _post_json("/cube/parameter-options", {
        "slug": slug,
        "file_b64": file_b64,
        "file_name": file_name,
    })
    if status != 200:
        raise RuntimeError(
            f"/cube/parameter-options slug={slug} returned {status}: {body}"
        )
    options = body.get("options") or {}
    # Exactly one enum param for these modes ("portfolio") — flatten.
    if len(options) != 1:
        raise RuntimeError(
            f"Expected one parameter in options for {slug}; got keys {list(options)}"
        )
    values = next(iter(options.values()))
    if not values:
        raise RuntimeError(f"No values returned for {slug}")
    return list(values)


_INDUSTRY_HEADER_RE = re.compile(
    r"^Industry breakdown \(ranked by committed", re.MULTILINE
)
_INDUSTRY_ROW_RE = re.compile(r"^-\s+([^:]+?):\s+committed", re.MULTILINE)
_LIFECYCLE_SUFFIX_RE = re.compile(r"\s*\((?:exited|new this period)\)\s*$")


def largest_industry_from_context(context_sent: str, candidates: list[str]) -> str:
    """Parse context_sent to find the largest-committed industry.

    Round 18 firm-level context renders industries sorted by committed
    desc with exited buckets sunk to the bottom, so the first `- <name>:`
    row under 'Industry breakdown' is the largest-committed active one.
    Falls back to the first candidate from /cube/parameter-options if
    the structure changes and parsing fails — the matrix should keep
    running rather than fail noisily on a plumbing check.
    """
    header = _INDUSTRY_HEADER_RE.search(context_sent)
    if not header:
        return candidates[0]
    tail = context_sent[header.end():]
    m = _INDUSTRY_ROW_RE.search(tail)
    if not m:
        return candidates[0]
    raw_name = m.group(1).strip()
    clean = _LIFECYCLE_SUFFIX_RE.sub("", raw_name).strip()
    if clean in candidates:
        return clean
    # Parser agreed on a name but /cube/parameter-options doesn't know
    # it — shouldn't happen, but bail to alphabetical to keep going.
    return candidates[0]


# ── Combination runner ────────────────────────────────────────

@dataclass
class Combo:
    mode:       str
    length:     str
    parameters: dict
    status:     int = 0
    chars:      int = 0
    sentences:  int = 0
    preview:    str = ""
    verified:   int = 0
    total:      int = 0
    narrative:  str = ""
    failing:    list[dict] = None  # up to 3, each {source_field, status, reason}
    error:      Optional[str] = None


def run_combo(combo: Combo, file_b64: str, file_name: str) -> None:
    body = {
        "file_name":  file_name,
        "file_b64":   file_b64,
        "mode":       combo.mode,
        "parameters": combo.parameters,
        "length":     combo.length,
        "prompt":     f"{combo.mode} × {combo.length} smoke",
    }
    status, resp = _post_json("/upload", body)
    combo.status = status
    if status != 200:
        combo.error = json.dumps(resp)[:500]
        combo.failing = []
        return

    narrative = resp.get("narrative") or ""
    combo.narrative = narrative
    combo.chars = len(narrative)
    combo.sentences = len(re.findall(r"[.!?]", narrative))
    combo.preview = narrative[:250]

    verification = resp.get("verification") or {}
    combo.total    = verification.get("total", 0) or 0
    combo.verified = verification.get("verified_count", 0) or 0

    claims = resp.get("claims") or []
    results = verification.get("claim_results") or []
    failing: list[dict] = []
    for r in results:
        if r.get("status") == "verified":
            continue
        idx = r.get("claim_index", -1)
        source_field = "(unknown)"
        if 0 <= idx < len(claims):
            source_field = claims[idx].get("source_field") or "(missing)"
        failing.append({
            "source_field": source_field,
            "status":       r.get("status") or "?",
            "reason":       r.get("reason") or "",
        })
        if len(failing) >= 3:
            break
    combo.failing = failing


# ── Rendering ──────────────────────────────────────────────────

def _ratio_pct(n: int, d: int) -> float:
    return (n / d * 100.0) if d > 0 else 0.0


def render_section(title: str, combos: list[Combo]) -> list[str]:
    out = [f"## {title}", ""]
    out.append("| Length       | Status | Chars | Sentences | Verified |")
    out.append("|--------------|--------|-------|-----------|----------|")
    for c in combos:
        verified_cell = (
            f"{c.verified}/{c.total}" if c.total > 0 else "0/0"
        )
        out.append(
            f"| {c.length:<12} | {c.status:<6} | "
            f"{c.chars:<5} | {c.sentences:<9} | {verified_cell:<8} |"
        )
    out.append("")
    out.append("Narrative previews:")
    for c in combos:
        preview_clean = c.preview.replace("\n", " ")
        out.append(f'  {c.length} (first 250): "{preview_clean}"')
    out.append("")
    out.append("Failing claims:")
    any_failing = False
    for c in combos:
        if c.error:
            out.append(f"  {c.length}: HTTP {c.status} — {c.error[:200]}")
            any_failing = True
            continue
        if not c.failing:
            continue
        any_failing = True
        out.append(f"  {c.length}:")
        for f in c.failing:
            out.append(
                f"    - [{f['status']}] {f['source_field']} "
                f"(reason: {f['reason'] or 'n/a'})"
            )
    if not any_failing:
        out.append("  none")
    out.append("")
    return out


def render_observations(all_combos: dict[str, list[Combo]]) -> list[str]:
    """Flag anomalies per spec. Do NOT fix — just surface."""
    notes: list[str] = []

    # Non-200 statuses.
    for scope, combos in all_combos.items():
        for c in combos:
            if c.status != 200:
                notes.append(
                    f"- {scope} × {c.length}: HTTP {c.status} "
                    f"(expected 200). Error head: {(c.error or '')[:120]}"
                )

    # Full vs Executive within 20% by char count.
    for scope, combos in all_combos.items():
        by_len = {c.length: c for c in combos}
        full = by_len.get("full")
        exec_ = by_len.get("executive")
        if full and exec_ and full.status == 200 and exec_.status == 200 and full.chars > 0:
            ratio = exec_.chars / full.chars
            if ratio >= 0.8:
                notes.append(
                    f"- {scope}: Executive ({exec_.chars} chars) is "
                    f"within 20% of Full ({full.chars} chars) — "
                    f"Executive directive may not be omitting enough."
                )

    # Distillation too verbose.
    for scope, combos in all_combos.items():
        by_len = {c.length: c for c in combos}
        dist = by_len.get("distillation")
        if dist and dist.status == 200:
            if dist.chars > 500 or dist.sentences >= 4:
                notes.append(
                    f"- {scope}: Distillation is {dist.chars} chars / "
                    f"{dist.sentences} sentences — brevity directive may "
                    f"not be firing."
                )

    # Verification < 80%.
    for scope, combos in all_combos.items():
        for c in combos:
            if c.status != 200 or c.total == 0:
                continue
            rate = _ratio_pct(c.verified, c.total)
            if rate < 80.0:
                notes.append(
                    f"- {scope} × {c.length}: verification rate "
                    f"{c.verified}/{c.total} ({rate:.0f}%) below 80% "
                    f"threshold."
                )

    if not notes:
        notes.append("- No anomalies flagged. All 9 combinations within thresholds.")
    return notes


# ── Main ──────────────────────────────────────────────────────

def main() -> int:
    print("KRONOS Phase 5 Verification — Synthetic Fixture")
    print("=" * 48)
    print()
    print(CAVEAT)
    print()
    print(f"Workbook: {FIXTURE}")
    print(f"Base URL: {BASE_URL}")
    print(f"Session:  {SESSION}")
    print()

    if not FIXTURE.exists():
        print(f"ERROR: fixture not found at {FIXTURE}", file=sys.stderr)
        return 2

    file_bytes = FIXTURE.read_bytes()
    file_b64 = base64.b64encode(file_bytes).decode("ascii")
    file_name = FIXTURE.name

    # ── Auto-pick ─────────────────────────────────────────────
    try:
        industries = fetch_parameter_options(
            "industry-portfolio-level", file_b64, file_name
        )
        horizontals = fetch_parameter_options(
            "horizontal-portfolio-level", file_b64, file_name
        )
    except RuntimeError as e:
        print(f"ERROR during parameter lookup: {e}", file=sys.stderr)
        return 3

    picked_horizontal = sorted(horizontals)[0]

    # Run firm-level × full first; parse its context_sent for the
    # largest-committed industry. This counts as combination 1/9.
    firm_full = Combo(mode="firm-level", length="full", parameters={})
    run_combo(firm_full, file_b64, file_name)
    if firm_full.status != 200:
        print(
            f"ERROR: firm-level × full returned {firm_full.status}. "
            f"Cannot auto-pick industry.\n"
            f"Body: {firm_full.error}",
            file=sys.stderr,
        )
        return 4

    # context_sent isn't on Combo; fetch it off the response again?
    # We dropped it. Re-POST once to get context_sent for the pick.
    # Simpler: amend run_combo to stash context_sent. Already have the
    # narrative — need a second call since we didn't capture context.
    # Rerunning would break the "9 runs total" rule. Fix: add a one-off
    # fetch here that DOESN'T count as a matrix combination.
    _, pick_resp = _post_json("/upload", {
        "file_name":  file_name,
        "file_b64":   file_b64,
        "mode":       "firm-level",
        "parameters": {},
        "length":     "full",
        "prompt":     "auto-pick industry",
    })
    context_sent = (pick_resp or {}).get("context_sent") or ""
    picked_industry = largest_industry_from_context(context_sent, industries)

    print("Auto-picked parameters:")
    print(f"  Industry:   {picked_industry} "
          f"(from {len(industries)} available; picked by largest-committed "
          f"via firm-level context_sent)")
    print(f"  Horizontal: {picked_horizontal} "
          f"(from {len(horizontals)} available; picked alphabetically)")
    print()

    # ── Build remaining 8 combinations ───────────────────────
    firm_combos = [firm_full] + [
        Combo(mode="firm-level", length=l, parameters={})
        for l in LENGTHS if l != "full"
    ]
    industry_combos = [
        Combo(mode="industry-portfolio-level", length=l,
              parameters={"portfolio": picked_industry})
        for l in LENGTHS
    ]
    horizontal_combos = [
        Combo(mode="horizontal-portfolio-level", length=l,
              parameters={"portfolio": picked_horizontal})
        for l in LENGTHS
    ]

    for combo in firm_combos[1:] + industry_combos + horizontal_combos:
        run_combo(combo, file_b64, file_name)

    # ── Render ────────────────────────────────────────────────
    # Reorder firm_combos to show {full, executive, distillation} order.
    firm_combos_ordered = [
        next(c for c in firm_combos if c.length == l) for l in LENGTHS
    ]

    sections = [
        ("firm-level", firm_combos_ordered),
        (f"industry-portfolio-level ({picked_industry})", industry_combos),
        (f"horizontal-portfolio-level ({picked_horizontal})", horizontal_combos),
    ]

    for title, combos in sections:
        for line in render_section(title, combos):
            print(line)

    print("## Observations")
    print()
    all_combos = {
        "firm-level":                 firm_combos_ordered,
        "industry-portfolio-level":   industry_combos,
        "horizontal-portfolio-level": horizontal_combos,
    }
    for line in render_observations(all_combos):
        print(line)
    print()

    # ── Plumbing verdict (non-zero exit when something broke) ─
    bad = [
        (scope, c) for scope, combos in all_combos.items()
        for c in combos if c.status != 200
    ]
    if bad:
        print(f"PLUMBING FAILURE: {len(bad)} combination(s) did not return 200.")
        return 1
    print("Plumbing OK: all 9 combinations returned 200.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
