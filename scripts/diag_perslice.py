#!/usr/bin/env python3
"""KRONOS — per-slice verification-variability diagnostic.

Single one-shot Domino run. Captures everything Phase A's static-analysis
hypothesis ranking needs to confirm or refute decisively, in one trip.

What it does, in order:

  1. Boots no server. Assumes server.py is already running on KRONOS_URL
     (default http://localhost:5000).
  2. Reads the fixture workbook from KRONOS_FIXTURE
     (default pipeline/tests/fixtures/smoke_lending.xlsx).
  3. Hits POST /cube/parameter-options to discover the available
     industry portfolios and horizontal portfolios. Auto-picks the
     first of each (alphabetical).
  4. Imports the slicer layer in-process and dumps the EXACT
     verifiable_values catalog the slicer publishes for the chosen
     industry slice and horizontal slice. Prints labels with types
     and a stable fingerprint (sha256 of sorted keys).
  5. Hits POST /upload three times consecutively for
     industry-portfolio-level × length=full × parameters={portfolio:
     <chosen industry>}. Identical request body each time.
  6. For each run, prints: timings, narrative + context_sent length
     and sha256[:12], claims_count, verification counts, and a
     per-claim dump (source_field, cited_value, status, reason,
     expected).
  7. Cross-run comparison: are the three context_sent strings
     byte-identical (sha256 collision)? Are the narratives byte-
     identical? Which source_fields appear in EVERY run vs only
     SOME runs?
  8. Failed-claim pattern classification — bins every non-"verified"
     claim across the three runs into one of seven failure shapes
     so the report names exactly which pattern is dominant.
  9. One single-shot horizontal-portfolio-level run with the same
     length and prompt to test whether the symptom is industry-only
     (today's prompt change) or shared-layer (slicer / agent /
     verifier).

Stdlib only — no requests dep — so it survives the same minimal
Python env that scripts/smoke_test_matrix.py runs in.

Run from repo root:

    python3 scripts/diag_perslice.py > diag_perslice.out 2>&1
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

# ── Allow `import pipeline.*` when run from repo root ────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ── Config ───────────────────────────────────────────────────────
KRONOS_URL = os.environ.get("KRONOS_URL", "http://localhost:5000").rstrip("/")
FIXTURE_PATH = Path(os.environ.get(
    "KRONOS_FIXTURE",
    str(_REPO_ROOT / "pipeline" / "tests" / "fixtures" / "smoke_lending.xlsx"),
))
# Optional overrides so the diagnostic can target the same slice the user
# originally observed the variability against. Empty / unset → fall back
# to the alphabetical-first option returned by /cube/parameter-options.
INDUSTRY_OVERRIDE   = os.environ.get("KRONOS_INDUSTRY",   "").strip() or None
HORIZONTAL_OVERRIDE = os.environ.get("KRONOS_HORIZONTAL", "").strip() or None
INDUSTRY_RUNS = 3
LENGTH = "full"
HTTP_TIMEOUT = 180  # seconds — LLM calls can take 30-60s on Azure

# Failure-pattern bins surfaced in the per-class summary.
_FAILURE_BINS = (
    "verified",                       # included for completeness; not a failure
    "value_mismatch",                 # label correct, value wrong
    "spurious_committed_suffix",      # the H3 smoking gun: "...— Acme Corp — Committed"
    "spurious_outstanding_suffix",
    "spurious_other_suffix",
    "label_correct_no_match",         # exact in keys set but verifier said unverified (shouldn't happen)
    "case_or_whitespace_only",        # label_lookup is normalised; this would mean a real failure
    "leaked_placeholder",             # source_field literally contains "{{"
    "wrong_prefix",                   # didn't start with "Industry Portfolio: <name>" prefix at all
    "calculated",                     # LLM's own arithmetic
    "empty_source_field",
    "unrecognized_pattern",           # right prefix, but suffix doesn't match any known pattern
)


# ─────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────

def _post_json(path: str, payload: dict, timeout: int = HTTP_TIMEOUT):
    """POST JSON; return (status_code, body_dict). Catches network errors."""
    body_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{KRONOS_URL}{path}",
        data=body_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw[:2000]}
    except Exception as e:
        return 0, {"_error": f"{type(e).__name__}: {e}"}


def _get_options(slug: str, file_b64: str) -> dict:
    code, body = _post_json(
        "/cube/parameter-options",
        {
            "slug": slug,
            "file_name": FIXTURE_PATH.name,
            "file_b64": file_b64,
        },
    )
    if code != 200:
        print(f"[FATAL] /cube/parameter-options for slug={slug!r} returned {code}")
        print(f"        body: {body}")
        sys.exit(2)
    return body.get("options", {})


def _upload(file_b64: str, mode: str, parameters: dict, length: str, prompt: str):
    return _post_json(
        "/upload",
        {
            "file_name":  FIXTURE_PATH.name,
            "file_b64":   file_b64,
            "mode":       mode,
            "parameters": parameters,
            "length":     length,
            "prompt":     prompt,
        },
    )


# ─────────────────────────────────────────────────────────────────
# In-process slicer dump (verifiable_values catalog)
# ─────────────────────────────────────────────────────────────────

class _Base64File:
    """Same shim server.py uses; redefined here so we don't import server.py
    (which would re-trigger registry validation + MLflow side effects)."""
    def __init__(self, data: bytes, name: str = ""):
        self._io = io.BytesIO(data)
        self.filename = name
        self.content_length = len(data)

    def read(self, *args, **kwargs):
        return self._io.read(*args, **kwargs)


def _compute_slicer_dump(file_bytes: bytes, mode_slug: str, parameters: dict) -> dict:
    """Run classify → cube → slicer in-process and return the slicer's full
    output dict (context, metrics, verifiable_values). This is the canonical
    label catalog the LLM is supposed to choose source_field from."""
    from pipeline.loaders.classifier import classify
    from pipeline.cube.lending import compute_lending_cube
    from pipeline.registry import get_mode, get_slicer, load_registry
    load_registry()  # ensures slicers are imported

    file_obj = _Base64File(file_bytes, FIXTURE_PATH.name)
    classified = classify(file_obj)
    if "lending" not in classified["classified"]:
        raise RuntimeError(f"Fixture has no lending sheet; got {list(classified['classified'].keys())}")
    cube = compute_lending_cube(classified["classified"]["lending"])
    mode_def = get_mode(mode_slug)
    if mode_def is None or mode_def.cube_slice is None:
        raise RuntimeError(f"Mode {mode_slug!r} not registered or missing cube_slice")
    slicer = get_slicer(mode_def.cube_slice)
    if slicer is None:
        raise RuntimeError(f"Slicer {mode_def.cube_slice!r} not registered")
    # Match analyze.py:124-127 — slicers take individual kwargs, not a dict.
    if parameters:
        return slicer["fn"](cube, **parameters)
    return slicer["fn"](cube)


# ─────────────────────────────────────────────────────────────────
# Failure-pattern classification
# ─────────────────────────────────────────────────────────────────

def _classify_failure(source_field: str, prefix: str, vv_keys: set) -> str:
    """Bin a single failed claim's source_field into a pattern.
    `prefix` is e.g. "Industry Portfolio: Information Technology" — what
    every label for the slice MUST start with."""
    sf = source_field or ""
    if not sf:
        return "empty_source_field"
    if "{{" in sf or "}}" in sf:
        return "leaked_placeholder"
    if sf.lower() == "calculated":
        return "calculated"
    if sf in vv_keys:
        # Exact match in the catalog — a non-verified result here means
        # the verifier rejected the cited_value, not the label.
        return "value_mismatch"
    # Case/whitespace tolerance — verify_claims normalises labels, so a
    # case-only mismatch SHOULD verify. If it doesn't, that's a real
    # divergence worth surfacing.
    lc = sf.strip().lower()
    if lc in {k.strip().lower() for k in vv_keys}:
        return "case_or_whitespace_only"
    if not sf.startswith(prefix):
        return "wrong_prefix"
    rest = sf[len(prefix):]
    # Strip leading separators (em-dash, hyphen, spaces) for suffix detection.
    rest_stripped = rest.lstrip(" —-")
    # The H3 smoking gun: parent labels published as "<prefix> — <Parent>"
    # with no extra suffix, but the new prompt example teaches the LLM
    # to append "— Committed". Detect both em-dash and hyphen variants.
    if rest.endswith(" — Committed") or rest.endswith(" - Committed"):
        return "spurious_committed_suffix"
    if rest.endswith(" — Outstanding") or rest.endswith(" - Outstanding"):
        return "spurious_outstanding_suffix"
    # Any other "— X" tail
    if " — " in rest_stripped or " - " in rest_stripped:
        return "spurious_other_suffix"
    return "unrecognized_pattern"


# ─────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────

def _h12(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def _kfp(keys) -> str:
    return hashlib.sha256("\n".join(sorted(keys)).encode("utf-8")).hexdigest()[:12]


def _fmt_value(v):
    if isinstance(v, float):
        return f"{v:,.6f}".rstrip("0").rstrip(".")
    return str(v)


def _print_run_block(label: str, run: dict | None):
    print(f"\n  ── {label} ──")
    if run is None:
        print("    (run failed; see error above)")
        return
    if "_status" in run and run["_status"] != 200:
        print(f"    HTTP {run['_status']}: {run.get('_body')}")
        return
    timings = run["timings_ms"]
    print(f"    HTTP 200")
    print(f"    timings_ms:        {timings}")
    print(f"    context_sent:      {len(run['context_sent']):>6,d} chars  sha256[:12]={_h12(run['context_sent'])}")
    print(f"    narrative:         {len(run['narrative']):>6,d} chars  sha256[:12]={_h12(run['narrative'])}")
    print(f"    claims_count:      {len(run['claims'])}")
    v = run["verification"]
    print(f"    verification:      total={v.get('total')} verified={v.get('verified_count')} "
          f"unverified={v.get('unverified_count')} mismatch={v.get('mismatch_count')}")
    print(f"    Per-claim dump:")
    cr = v.get("claim_results") or []
    for j, c in enumerate(run["claims"]):
        r = cr[j] if j < len(cr) else {}
        sf = c.get("source_field", "")
        cv = c.get("cited_value", "")
        st = (r.get("status") or "?")
        rs = (r.get("reason") or "")
        ex = (r.get("expected") or "")
        print(f"      [{j:2d}] {st:>10s}  reason={rs:<22s}  sf={sf!r}")
        print(f"           cited={cv!r}  expected={ex!r}")


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 78)
    print("KRONOS per-slice verification-variability diagnostic")
    print(f"Run at:  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Server:  {KRONOS_URL}")
    print(f"Fixture: {FIXTURE_PATH}")
    print("=" * 78)

    if not FIXTURE_PATH.exists():
        print(f"[FATAL] Fixture not found at {FIXTURE_PATH}.")
        print("        Set KRONOS_FIXTURE=/path/to/your.xlsx if you want a different file.")
        sys.exit(2)

    file_bytes = FIXTURE_PATH.read_bytes()
    file_b64 = base64.b64encode(file_bytes).decode("ascii")
    print(f"\nFixture size: {len(file_bytes):,} bytes  sha256[:12]={hashlib.sha256(file_bytes).hexdigest()[:12]}")

    # ─────────────────────────────────────────────────────────────
    print("\n─── Step 1: Auto-pick parameters ────────────────────────────")
    industry_opts   = _get_options("industry-portfolio-level",   file_b64)
    horizontal_opts = _get_options("horizontal-portfolio-level", file_b64)
    industry_list   = industry_opts.get("portfolio") or []
    horizontal_list = horizontal_opts.get("portfolio") or []
    if not industry_list:
        print("[FATAL] No industry options resolved — fixture has no industry data.")
        sys.exit(3)
    if not horizontal_list:
        print("[WARN] No horizontal options resolved — horizontal step will be skipped.")

    # Honour env overrides so the diagnostic can target the original
    # failure case (the user reported variability for Information
    # Technology specifically). If the override doesn't appear in the
    # cube's option list, exit loudly — silently picking a different
    # slice would defeat the purpose of running this.
    if INDUSTRY_OVERRIDE is not None:
        if INDUSTRY_OVERRIDE not in industry_list:
            print(f"[FATAL] KRONOS_INDUSTRY={INDUSTRY_OVERRIDE!r} not in fixture's industry list.")
            print(f"        Available: {industry_list}")
            sys.exit(3)
        chosen_industry = INDUSTRY_OVERRIDE
        industry_pick_reason = "override (KRONOS_INDUSTRY)"
    else:
        chosen_industry = industry_list[0]
        industry_pick_reason = "alphabetical first"

    if HORIZONTAL_OVERRIDE is not None:
        if HORIZONTAL_OVERRIDE not in horizontal_list:
            print(f"[FATAL] KRONOS_HORIZONTAL={HORIZONTAL_OVERRIDE!r} not in fixture's horizontal list.")
            print(f"        Available: {horizontal_list}")
            sys.exit(3)
        chosen_horizontal = HORIZONTAL_OVERRIDE
        horizontal_pick_reason = "override (KRONOS_HORIZONTAL)"
    elif horizontal_list:
        chosen_horizontal = horizontal_list[0]
        horizontal_pick_reason = "alphabetical first"
    else:
        chosen_horizontal = None
        horizontal_pick_reason = "n/a (no horizontals in fixture)"

    print(f"  Industry choice   [{industry_pick_reason}]:   {chosen_industry!r}")
    print(f"  Horizontal choice [{horizontal_pick_reason}]: {chosen_horizontal!r}")
    print(f"  All industries  ({len(industry_list)}): {industry_list}")
    print(f"  All horizontals ({len(horizontal_list)}): {horizontal_list}")

    industry_prefix   = f"Industry Portfolio: {chosen_industry}"
    horizontal_prefix = f"Horizontal Portfolio: {chosen_horizontal}" if chosen_horizontal else None

    # ─────────────────────────────────────────────────────────────
    print("\n─── Step 2: In-process slicer dump (label catalog) ──────────")
    try:
        industry_slicer = _compute_slicer_dump(
            file_bytes, "industry-portfolio-level", {"portfolio": chosen_industry},
        )
    except Exception as e:
        print(f"[FATAL] In-process industry slicer dump failed: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        sys.exit(4)

    industry_vv = industry_slicer.get("verifiable_values", {}) or {}
    industry_vv_keys = list(industry_vv.keys())
    print(f"\n  Industry slice — verifiable_values: {len(industry_vv_keys)} labels")
    print(f"    Fingerprint (sha256[:12] of sorted keys): {_kfp(industry_vv_keys)}")
    print("    Full label catalog (every label the LLM may legitimately cite):")
    for k in industry_vv_keys:
        spec = industry_vv[k]
        print(f"      - {k}  [{spec.get('type')}={_fmt_value(spec.get('value'))}]")
    print(f"\n  Industry slice — context (what the LLM sees), {len(industry_slicer.get('context',''))} chars:")
    print("    -- BEGIN CONTEXT --")
    for line in (industry_slicer.get("context", "") or "").splitlines():
        print(f"    | {line}")
    print("    -- END CONTEXT --")

    horizontal_slicer = None
    horizontal_vv_keys: list = []
    if chosen_horizontal:
        try:
            horizontal_slicer = _compute_slicer_dump(
                file_bytes, "horizontal-portfolio-level", {"portfolio": chosen_horizontal},
            )
            horizontal_vv = horizontal_slicer.get("verifiable_values", {}) or {}
            horizontal_vv_keys = list(horizontal_vv.keys())
            print(f"\n  Horizontal slice — verifiable_values: {len(horizontal_vv_keys)} labels")
            print(f"    Fingerprint (sha256[:12] of sorted keys): {_kfp(horizontal_vv_keys)}")
            print("    First 30 labels:")
            for k in horizontal_vv_keys[:30]:
                spec = horizontal_vv[k]
                print(f"      - {k}  [{spec.get('type')}={_fmt_value(spec.get('value'))}]")
            if len(horizontal_vv_keys) > 30:
                print(f"      ... +{len(horizontal_vv_keys) - 30} more")
        except Exception as e:
            print(f"[WARN] Horizontal slicer dump failed: {type(e).__name__}: {e}")

    # ─────────────────────────────────────────────────────────────
    print(f"\n─── Step 3: Industry × length={LENGTH} × {INDUSTRY_RUNS} consecutive runs ─")
    industry_runs: list[dict | None] = []
    for i in range(1, INDUSTRY_RUNS + 1):
        t0 = time.perf_counter()
        code, body = _upload(
            file_b64,
            "industry-portfolio-level",
            {"portfolio": chosen_industry},
            LENGTH,
            f"Provide a deep-dive analysis of the {chosen_industry} industry portfolio.",
        )
        t_ms = int((time.perf_counter() - t0) * 1000)
        if code != 200:
            run = {"_status": code, "_body": body}
        else:
            run = {
                "_status":       200,
                "narrative":     body.get("narrative", "") or "",
                "context_sent":  body.get("context_sent", "") or "",
                "claims":        body.get("claims", []) or [],
                "verification":  body.get("verification", {}) or {},
                "timings_ms":    body.get("timings_ms", {}) or {},
                "wall_ms":       t_ms,
            }
        industry_runs.append(run)
        _print_run_block(f"Run {i}/{INDUSTRY_RUNS}", run)

    valid = [r for r in industry_runs if r and r.get("_status") == 200]

    # ─────────────────────────────────────────────────────────────
    print("\n─── Step 4: Cross-run comparison (industry × full) ──────────")
    if len(valid) < 2:
        print("  (fewer than 2 successful runs; cross-run comparison skipped)")
    else:
        ctx_hashes = [_h12(r["context_sent"]) for r in valid]
        nar_hashes = [_h12(r["narrative"])    for r in valid]
        ctx_unique = len(set(ctx_hashes))
        nar_unique = len(set(nar_hashes))
        print(f"  context_sent hashes per run: {ctx_hashes}")
        print(f"     identical across runs:    {'YES' if ctx_unique == 1 else 'NO'} ({ctx_unique} distinct)")
        print(f"  narrative hashes per run:    {nar_hashes}")
        print(f"     identical across runs:    {'YES' if nar_unique == 1 else 'NO'} ({nar_unique} distinct)")
        print(f"  narrative lengths:           {[len(r['narrative']) for r in valid]}")
        print(f"  claim counts:                {[len(r['claims']) for r in valid]}")
        print(f"  verified counts:             {[r['verification'].get('verified_count', 0) for r in valid]}")
        print(f"  unverified counts:           {[r['verification'].get('unverified_count', 0) for r in valid]}")
        print(f"  mismatch counts:             {[r['verification'].get('mismatch_count', 0) for r in valid]}")

        sets = [{c.get("source_field", "") for c in r["claims"]} for r in valid]
        union = set().union(*sets) if sets else set()
        intersection = set.intersection(*sets) if sets else set()
        print(f"\n  unique source_fields across all runs: {len(union)}")
        print(f"  source_fields cited in EVERY run ({len(intersection)} stable):")
        for sf in sorted(intersection):
            print(f"      = {sf!r}")
        run_specific = union - intersection
        print(f"  source_fields cited in ONLY SOME runs ({len(run_specific)} run-specific):")
        for sf in sorted(run_specific):
            in_runs = [i + 1 for i, s in enumerate(sets) if sf in s]
            print(f"      ~ {sf!r}  (runs {in_runs})")

    # ─────────────────────────────────────────────────────────────
    print("\n─── Step 5: Failed-claim pattern classification (industry) ──")
    industry_keys_set = set(industry_vv_keys)
    overall = Counter()
    examples: dict[str, list[str]] = {}
    for r in valid:
        cr = r["verification"].get("claim_results") or []
        for j, c in enumerate(r["claims"]):
            res = cr[j] if j < len(cr) else {}
            status = res.get("status")
            sf = c.get("source_field", "") or ""
            if status == "verified":
                overall["verified"] += 1
                continue
            bin_ = _classify_failure(sf, industry_prefix, industry_keys_set)
            # Mismatch from the verifier overrides our "value_mismatch" guess too;
            # they should agree but record the verifier's view if they don't.
            if status == "mismatch" and bin_ == "value_mismatch":
                pass  # in agreement
            overall[bin_] += 1
            examples.setdefault(bin_, []).append(sf)
    print("  Overall counts (across all 3 runs):")
    for bin_ in _FAILURE_BINS:
        n = overall.get(bin_, 0)
        if n == 0:
            continue
        print(f"    {bin_:35s} {n:4d}")
        for ex in (examples.get(bin_) or [])[:3]:
            print(f"        e.g. {ex!r}")

    # ─────────────────────────────────────────────────────────────
    print(f"\n─── Step 6: Horizontal × length={LENGTH} × 1 run ─────────────")
    if not chosen_horizontal:
        print("  (no horizontal portfolios in fixture; step skipped)")
    else:
        t0 = time.perf_counter()
        code, body = _upload(
            file_b64,
            "horizontal-portfolio-level",
            {"portfolio": chosen_horizontal},
            LENGTH,
            f"Provide a deep-dive analysis of the {chosen_horizontal} horizontal portfolio.",
        )
        t_ms = int((time.perf_counter() - t0) * 1000)
        if code != 200:
            print(f"  HTTP {code}: {body}")
        else:
            run = {
                "_status":       200,
                "narrative":     body.get("narrative", "") or "",
                "context_sent":  body.get("context_sent", "") or "",
                "claims":        body.get("claims", []) or [],
                "verification":  body.get("verification", {}) or {},
                "timings_ms":    body.get("timings_ms", {}) or {},
                "wall_ms":       t_ms,
            }
            _print_run_block("Horizontal run 1/1", run)

            # Horizontal failure-pattern classification
            h_keys_set = set(horizontal_vv_keys)
            h_counts = Counter()
            h_examples: dict[str, list[str]] = {}
            cr = run["verification"].get("claim_results") or []
            for j, c in enumerate(run["claims"]):
                res = cr[j] if j < len(cr) else {}
                status = res.get("status")
                sf = c.get("source_field", "") or ""
                if status == "verified":
                    h_counts["verified"] += 1
                    continue
                bin_ = _classify_failure(sf, horizontal_prefix or "", h_keys_set)
                h_counts[bin_] += 1
                h_examples.setdefault(bin_, []).append(sf)
            print("\n  Horizontal failure pattern (single run):")
            for bin_ in _FAILURE_BINS:
                n = h_counts.get(bin_, 0)
                if n == 0:
                    continue
                print(f"    {bin_:35s} {n:4d}")
                for ex in (h_examples.get(bin_) or [])[:3]:
                    print(f"        e.g. {ex!r}")

    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("Summary fingerprints (paste this block back for the Phase C analysis):")
    print("=" * 78)
    print(f"  industry chosen:               {chosen_industry!r}")
    print(f"  industry vv label count:       {len(industry_vv_keys)}")
    print(f"  industry vv fingerprint:       {_kfp(industry_vv_keys)}")
    if valid:
        print(f"  industry context_sent hashes:  {[_h12(r['context_sent']) for r in valid]}")
        print(f"  industry narrative hashes:     {[_h12(r['narrative'])    for r in valid]}")
        print(f"  industry claim counts:         {[len(r['claims']) for r in valid]}")
        print(f"  industry verified counts:      {[r['verification'].get('verified_count', 0) for r in valid]}")
        print(f"  industry mismatch counts:      {[r['verification'].get('mismatch_count', 0) for r in valid]}")
        print(f"  industry failure-pattern bins: {dict(overall)}")
    if chosen_horizontal:
        print(f"  horizontal chosen:             {chosen_horizontal!r}")
        print(f"  horizontal vv label count:     {len(horizontal_vv_keys)}")
        print(f"  horizontal vv fingerprint:     {_kfp(horizontal_vv_keys)}")

    print("\nDone.")


if __name__ == "__main__":
    main()
