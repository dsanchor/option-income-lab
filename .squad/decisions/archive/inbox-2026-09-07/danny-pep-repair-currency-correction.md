# Danny — URGENT Correction: PEP Repair Currency Invariant (REOPENED/REJECTED)

## Status: currency portion of `danny-pep-security-id-repair-contract.md`
## is REJECTED and superseded by this amendment. All other approved
## repair semantics stand unchanged (see §6).

## 1. New evidence (read-only, verified)

- All 76 `NNYS:PEP` ledger movements have **unanimous**
  `gross.currency="EUR"` and `fx.rate=1.0`.
- A direct `YFinanceFetcher('PEP')` metadata lookup returns
  `info.exchange="NMS"`, `info.fullExchangeName="NasdaqGS"`,
  `info.currency="USD"`, `info.financialCurrency="USD"`.

`NMS`/`NasdaqGS` independently corroborates the already-derived target
MIC `XNAS` (yfinance's Nasdaq-family exchange codes; consistent with the
frontend's own `toExchangeMic` alias set for `NMS`/`NGM`/`NCM` reviewed
in the TradingView gate). The unanimous `EUR` ledger currency is fully
explained as this portfolio's **home/accounting currency** (the same
default `"EUR"` used system-wide for `gross`/`fees`/`net` blocks and
import-batch defaults) — it says nothing about what currency PEP itself
lists/trades in. **`gross.currency` unanimity was never evidence of
`listing_currency`; it was evidence of the user's own bookkeeping
currency.** Applying the previously-approved script as-is would have
left (or worse, "confirmed") `listing_currency="EUR"` on `XNAS:PEP` —
factually wrong for a NASDAQ-listed, USD-denominated security.

## 2. What is rejected

The entire `_currency_verdict`/`_extract_gross_currencies` mechanism in
`backend/scripts/repair_pep_security_id.py` (lines ~230-258, wired into
`discover()` ~505-512, `RepairReport.currency_evidence` ~131/735/794, and
`run_apply`'s target-currency selection ~819-823) is **rejected as a
listing_currency invariant**. Ledger `gross.currency` must never again be
treated as a proxy for a security's listing currency anywhere in this
script.

Everything else in the approved contract/script — MIC derivation via
`LEGACY_ALIAS_TO_MIC`, collision handling, backup/checksum, CAS on
replace, holdings-equivalence gating, no-premature-deletion, restore
semantics, byte-exact ledger financial-field preservation, PEP-12a's
corrected test — **remains approved and unchanged**. This is a narrow,
currency-only reopening.

## 3. Corrected invariant

**`listing_currency` must be sourced from authoritative Security/provider
metadata, cross-checked, never from ledger `gross.currency`/`fx`.**
Ledger amounts, `fx.rate`, `fx.rate_source`, and every other financial
field remain byte-unchanged — this was already true and is unaffected by
this correction; restated here for the record.

### 3a. Source of truth

`YFinanceFetcher.get_ticker_data(ticker)` (already-existing class,
`backend/src/yfinance_fetcher.py` — reused, not reinvented) returns
`{"info": {...}, ...}`. The repair script must read, at minimum,
`info.get("currency")` and `info.get("financialCurrency")` and
`info.get("exchange")`/`info.get("fullExchangeName")`.

### 3b. Explicit, operator-supplied `--listing-currency` (required for
any currency change — never inferred)

- **No currency change happens unless the operator explicitly passes
  `--listing-currency <CUR>`.** Absent this flag, the script behaves
  exactly as before the ledger-based mechanism existed: it copies
  `source_clean.get("listing_currency", "EUR")` onto the target
  unchanged. There is no default inference from any internal document.
- When `--listing-currency` **is** supplied, it is **mandatorily
  cross-checked against a live provider lookup before being applied** —
  never trusted on the operator's word alone, and never silently applied
  if the live check is unreachable:
  1. Call `YFinanceFetcher.get_ticker_data(ticker)`. If this fails
     (network error, rate limit, empty/`None` result, or missing
     `currency`/`exchange` fields) → **abort, exit code 2** ("cannot
     verify listing_currency against live provider metadata; re-run
     when the provider is reachable"). A currency change is a rare,
     high-stakes, manually-triggered operation — "try again later" is
     acceptable; "proceed unverified" is not.
  2. Require `info["currency"] == info["financialCurrency"] ==
     args.listing_currency` (all three must agree). Any mismatch aborts
     (exit 2), reporting all three values for manual review — never
     picks a "majority" or guesses.
  3. Require the provider's own `exchange`/`fullExchangeName` to
     corroborate the **same target MIC** already derived in §MIC
     derivation from `config_PEP.exchange` (e.g. `NMS`/`NasdaqGS` ⇒
     `XNAS`, reusing the same alias resolution the script already has
     for `LEGACY_ALIAS_TO_MIC`/`_KNOWN_MICS` — extend with a small,
     backend-local yfinance-exchange-code → MIC lookup **only if** one
     doesn't already exist; if the frontend's `toExchangeMic` NMS/NGM/NCM
     handling has a backend twin, reuse it — do not invent a second,
     divergent copy). A mismatch (provider says a different exchange
     than the config-derived MIC) aborts (exit 2) — this is exactly the
     kind of cross-check that would have caught a wrong `--listing-
     currency` guess.
  4. Only if all three checks pass does `listing_currency =
     args.listing_currency` get written to the new/target
     `security_master` doc.
- **`--audit` mode performs the same live lookup read-only** (best-effort
  — a network failure in `--audit` degrades to reporting
  `provider_currency_verdict: "unreachable"` rather than aborting the
  whole audit, since audit is informational) and prints the fetched
  `currency`/`financialCurrency`/`exchange` plus whether they'd pass the
  three checks above if `--listing-currency` were supplied, so an
  operator can preview before `--apply`.

### 3c. Ledger `gross.currency` — diagnostic only, explicitly relabeled

If retained at all (optional, for operator context), rename the
report field from `currency_evidence` to `ledger_accounting_currency_note`
and document unambiguously in its docstring/help text: *"This reflects
the portfolio's booking/accounting currency for these movements, not the
security's listing currency. It must never influence
`listing_currency`."* It must not feed `proposed_listing_currency` or any
write path. If this is judged not worth keeping, remove
`_currency_verdict`/`_extract_gross_currencies` entirely — Linus's call
during implementation, either is acceptable as long as it cannot
influence a write.

### 3d. Report/CLI changes required

- New CLI arg: `--listing-currency <CUR>` (optional; absent = no
  currency change, exactly as if never supplied before).
- `RepairReport` gains `provider_currency_verdict: str` (e.g.
  `"verified:USD"`, `"unreachable"`, `"mismatch:<details>"`,
  `"not_requested"`) replacing `currency_evidence`'s role in the
  currency-decision (old field renamed/repurposed per §3c if kept).
- Exit code 2 is used for: provider unreachable during `--apply` with
  `--listing-currency` set, three-way mismatch, and MIC-corroboration
  mismatch — consistent with the existing "collision/discovery
  inconsistency, never proceeds to write" exit-2 convention.

## 4. Today's concrete case (documented for the actual PEP apply,
not hardcoded into the script)

Operator will need to run:
```
python -m scripts.repair_pep_security_id --apply --listing-currency USD
```
which the script will independently verify against
`YFinanceFetcher.get_ticker_data("PEP")` returning
`currency="USD"`, `financialCurrency="USD"`, `exchange="NMS"`/
`fullExchangeName="NasdaqGS"` (corroborating `XNAS`) before writing
`listing_currency="USD"` onto `sec_XNAS_PEP`. This is a documentation/
usage note, not a hardcoded PEP-only branch in the script — the
mechanism must work generically for any future `--listing-currency`
invocation.

## 5. Assignment (reviewer lockout)

- **Livingston** authored the rejected currency mechanism in the
  approved script and is a party to this rejection — **locked out** from
  revising the currency portion. Reassigned to **Linus**: implement §3a-
  §3d in `backend/scripts/repair_pep_security_id.py` only (do not touch
  MIC derivation, collision, backup, CAS, holdings-equivalence, restore,
  or any already-approved non-currency logic).
- **Basher** authored the currency-invariant tests
  (`TestCurrencyEvidence::test_pep3_unanimous_currency_corrects_
  listing_currency`, `test_pep4_mixed_currencies_fail_closed`,
  `test_pep4_mixed_currencies_reported_inconclusive`,
  `test_pep5_no_movements_currency_fail_closed`) that encode the now-
  rejected invariant — these tests must be rewritten to assert the
  corrected behavior (mock `YFinanceFetcher`, never real network in
  tests) and are locked out from self-revising this specific class.
  Reassigned to **Reuben** (test-file-only scope,
  `backend/tests/test_repair_pep_security_id.py::TestCurrencyEvidence`
  and any new provider-verification test class only) — cover: no flag
  → no currency change (existing default-preserve behavior unaffected);
  flag + matching mocked provider triple → currency written; flag +
  any of the three mismatched → abort exit 2, currency untouched; flag
  + provider unreachable during `--apply` → abort exit 2; `--audit` with
  provider unreachable → does not abort, reports `"unreachable"`; ledger
  `gross.currency` unanimity alone (no flag) never changes
  `listing_currency` (regression guard against reintroducing the
  rejected mechanism).

## 6. Explicitly reaffirmed unchanged (not reopened)

MIC derivation (`config_PEP.exchange` → `LEGACY_ALIAS_TO_MIC` → target
MIC), collision fail-closed handling, mandatory backup + SHA-256
checksum before first write, ETag/CAS on `config_PEP`/ledger_txn/
import_session patches, byte-exact preservation of `movement_id`/
`account_id`/all financial fields on ledger_txn, holdings-equivalence
verification gating source deletion, source deleted only after zero
remaining references, idempotent restore reading live state, and
PEP-12a's corrected real-apply-path backup-ordering test — all remain
approved exactly as previously gated. **No re-review of these areas is
requested or needed**; only the currency mechanism is reopened.

## 7. Authorized files (this amendment only)

- `backend/scripts/repair_pep_security_id.py` (Linus — currency
  sections only, per §3/§5)
- `backend/tests/test_repair_pep_security_id.py` (Reuben —
  `TestCurrencyEvidence` class and new provider-verification coverage
  only, per §5)

**Do not run `--apply` against production.** This amendment must pass a
follow-up point reviewer gate (Danny) before the corrected script may be
used for the actual production PEP repair.
