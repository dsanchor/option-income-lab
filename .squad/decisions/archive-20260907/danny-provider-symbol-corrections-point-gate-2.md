# Danny — POINT Gate: Canonical Script Constructor Defect (Provider-Symbol Repair)

## Verdict: REJECTED — product script only. Reuben's test artifact (shim
deletion + defect report) is APPROVED as-is; no revision required there
except the additive regression test specified below.

## Confirmed: this is a real, high-confidence production-breaking defect

Direct source read of `backend/scripts/repair_provider_symbols_enag_micct_ulvr.py`,
`verify_provider_symbol()`:

```python
if fetcher is None:
    fetcher = YFinanceFetcher(spec["expected_yfinance"])   # BUG
...
try:
    data = fetcher.get_ticker_data(spec["expected_yfinance"])
```

`YFinanceFetcher.__init__(self, requests_per_minute: int = 60, max_retries:
int = 3)` (`src/yfinance_fetcher.py`) computes
`self._min_interval = 60.0 / requests_per_minute` **inside `__init__`**.
Passing a ticker string (e.g. `"ENG.MC"`) positionally means
`requests_per_minute="ENG.MC"`, and `60.0 / "ENG.MC"` raises `TypeError`
**at construction time** — before `get_ticker_data` is ever reached.

Traced the blast radius through `main()`:
- The `is_audit` branch (**the script's default mode with zero CLI
  flags**) calls `audit_repair(symbols_c, portfolio_c)` with **no
  surrounding try/except at all** — the `TypeError` propagates fully
  uncaught, producing a raw traceback and Python's default exit code,
  not this script's fail-closed `exit_code=2` convention.
- The `--apply` branch happens to be wrapped in a `try/except Exception:
  return 2` at the `main()` call site — so `--apply` would coincidentally
  exit 2, but only by accident of an unrelated blanket catch, with a
  misleading generic "Apply failed: ..." message rather than a clear
  diagnosis, and only *after* `discover()` has already been invoked for
  all three targets.
- **Net effect: every real (non-test) invocation of this script —
  including the default no-argument audit — crashes.** The script cannot
  currently be run against production, or even safely dry-run, at all.
  This fully corroborates Reuben's finding; it is not a false positive or
  a test-only artifact.

Confirmed no test exercises this path: `grep` of
`tests/test_repair_provider_symbols.py` shows every test injects
`yf_fetcher=`/`fetcher=` explicitly (as documented candidly in Reuben's
own file-header comment, "KNOWN PRODUCT-SCRIPT DEFECT FOUND WHILE WRITING
THESE TESTS... script is read-only for this revision") — this is a
genuine test-suite blind spot on the never-injected constructor path, now
correctly identified rather than silently patched around.

## Also confirmed: stale docstring references to the deleted shim

The module docstring's `Usage::` block still reads:
```
python -m scripts.repair_provider_symbols
python -m scripts.repair_provider_symbols --apply
python -m scripts.repair_provider_symbols --restore ...
```
`scripts.repair_provider_symbols` was the unauthorized shim rejected and
deleted in the prior gate — these three usage lines are now factually
wrong and must be corrected to the canonical module path
(`scripts.repair_provider_symbols_enag_micct_ulvr`). (The internal
`logger`/`argparse prog=` string `"repair_provider_symbols"` is a short
internal identifier, not a usage instruction, and is not required to
change — noted for completeness, not a blocker.)

## Required fix (minimal, precise — no other logic may change)

1. In `verify_provider_symbol()`: change
   `fetcher = YFinanceFetcher(spec["expected_yfinance"])` to
   `fetcher = YFinanceFetcher()` (no positional argument — the ticker is
   already correctly passed separately to `fetcher.get_ticker_data(spec["expected_yfinance"])`
   on the next line, which is untouched and already correct).
2. Correct the three `Usage::` docstring lines to reference
   `scripts.repair_provider_symbols_enag_micct_ulvr` instead of the
   deleted shim's module path.
3. No other behavior, ordering, exit-code, backup, CAS, or enrichment
   logic may change — this is a narrowly-scoped defect fix, not a
   reopening of the already-approved design.

## Test requirement (additive, same file, Reuben — no lockout)

Add a regression test that exercises the **non-injected** construction
path (i.e. does not pass `yf_fetcher=`), using `monkeypatch` to replace
the module's `YFinanceFetcher` class reference with a lightweight fake
constructed with **zero required positional args** (mirroring the real
class's actual signature), and asserts:
- The call succeeds (no `TypeError` propagates) when `yf_fetcher` is
  omitted.
- The fake class is constructed with no positional ticker argument (i.e.
  assert on the fake's captured `__init__` args, not just that "some
  call happened") — this must fail against the current buggy code and
  pass only once Linus's fix lands, so it actually proves the fix rather
  than merely re-confirming the already-covered injected-fetcher path.

This is additive to Reuben's already-approved 33-test suite; no existing
test needs to change.

## Assignment under reviewer lockout

- **Product fix**: Linus is locked out from revising this artifact again
  in this cycle (he authored both the original defect and the just-
  rejected unauthorized shim in back-to-back cycles on this same script).
  Assigned to **Livingston** — deepest existing familiarity with this
  exact repair-script family (PEP/AD), and uninvolved in either of
  Linus's two rejected items on this specific file, so no lockout
  applies. (Rusty is a frontend specialist and not the appropriate owner
  for a backend Python CLI defect.)
- **Test addition**: **Reuben** — his existing artifact is approved, not
  rejected; he is the one who correctly found and documented the defect
  without exceeding his authorized test-only scope, so no lockout applies
  to him adding the regression test that proves the eventual fix.

## Tests run

`pytest backend/tests/test_repair_provider_symbols.py` → 33/33 still
passing (this run does not cover the defective path, per the above — the
count itself is not evidence the script is production-safe).

## Result

REJECTED for the canonical script only, pending the one-line constructor
fix (Livingston) plus the corrected docstring and Reuben's new regression
test proving the non-injected path. Reuben's already-delivered shim
deletion and defect report stand approved. No production execution
occurred or is authorized under this gate.
