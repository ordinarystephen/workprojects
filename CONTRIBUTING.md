# Contributing

## Running tests

From the repo root:

```bash
pytest                                       # everything (52 tests, ~1s)
pytest pipeline/tests/test_smoke_lending.py  # cube smoke only
pytest -k registry                           # YAML registry tests only
```

Configuration lives in [pytest.ini](pytest.ini); test discovery is rooted at
[pipeline/tests/](pipeline/tests/).

## Test layout

| File | Covers |
|---|---|
| [pipeline/tests/test_registry.py](pipeline/tests/test_registry.py) | YAML mode registry — schema validation, slicer cross-check, parameter resolve |
| [pipeline/tests/test_validate.py](pipeline/tests/test_validate.py) | Number cross-check + per-claim verification (date stripping, edge cases) |
| [pipeline/tests/test_smoke_lending.py](pipeline/tests/test_smoke_lending.py) | End-to-end Lending cube against [smoke_lending.xlsx](pipeline/tests/fixtures/smoke_lending.xlsx) — 17 invariants (totals, counts, WAPD/WALGD, dim reconciliation, rating-category coverage, bucket membership, horizontal overlap, MoM, determinism) |

## Smoke fixture

The Lending smoke test uses
[pipeline/tests/fixtures/smoke_lending.xlsx](pipeline/tests/fixtures/smoke_lending.xlsx).
The workbook is generated from
[pipeline/tests/fixtures/_build_smoke_lending.py](pipeline/tests/fixtures/_build_smoke_lending.py)
— that script is the source of truth for the fixture; the .xlsx is the
artifact checked into the repo so `pytest` has no generation step.

To change the fixture:

1. Edit `FACILITIES` in `_build_smoke_lending.py`.
2. Hand-recompute every affected value in
   [pipeline/tests/fixtures/smoke_lending_expected.py](pipeline/tests/fixtures/smoke_lending_expected.py).
   **Do not** copy values out of a cube run — that asserts the cube
   agrees with itself.
3. Regenerate the workbook:
   ```bash
   python -m pipeline.tests.fixtures._build_smoke_lending
   ```
4. Re-run `pytest pipeline/tests/test_smoke_lending.py -v`.
