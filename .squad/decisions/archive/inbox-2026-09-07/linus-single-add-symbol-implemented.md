# Decision: Backend portion of single canonical add-symbol contract implemented

**From:** Linus (Quant Dev)
**Re:** `.squad/decisions/inbox/danny-single-add-symbol-contract.md`

## What changed

- `POST /api/symbols/add` (`backend/web/portfolio_routes.py`) is now the
  sole create-or-select path. It pre-checks config existence before
  calling `ensure_symbol_config`, gates warm-up strictly on
  `config_created=True`, and returns a new `warmup_started` field.
- Warm-up (enrichment + forecast backfill) is fire-and-forget via a new
  `_start_symbol_warmup()` helper, reusing `resolve_yfinance_symbol`
  (no duplicated MIC suffix map, per contract).
- Legacy `POST /api/symbols` (`api_create_symbol` in `backend/web/app.py`)
  and `cosmos_db.create_symbol()` are removed. `GET /api/symbols` and
  `GET /api/symbols/{symbol}` are untouched.

## Bug found + fixed (flagging for Basher/Danny awareness)

The prior `config_created` check relied on `_auto_enrolled`, a field that
is set once and never cleared on the persisted config doc — so repeat
`add_symbol` calls for an already-enrolled security would have re-fired
warm-up, violating "exactly once." Fixed by snapshotting config existence
before the mutating call, not after. Covered by a new test
(`TestAddSymbolWarmup::test_warmup_never_refires_on_existing_config`).

## Verification

- Targeted backend sweep: 260 passed, 0 failures (own tests).
- Independently ran Basher's `test_add_symbol_contract.py`: 30/30 pass —
  confirms the implementation satisfies the contract from an
  independently-authored test suite, not just my own tests.
- Confirmed via `git diff --stat` that only the intended 5 backend files
  changed; concurrent frontend work (Rusty) and other agents' unrelated
  changes left untouched. Not committed/pushed, per instructions.

## Scope note

Frontend (`frontend/src/app/api/symbols/route.ts`,
`DgiScreenerView.tsx`) is explicitly out of scope here (Rusty's, per
contract §3) and was not touched.
