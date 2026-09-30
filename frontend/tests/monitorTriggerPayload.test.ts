import assert from "node:assert/strict";
import test from "node:test";

// @ts-expect-error Node's type-stripping test runner requires the source extension.
import { OPTIONAL_MONITOR_IDENTITY_FIELDS, buildManualTriggerPayload, omitAbsentMonitorConstraints } from "../src/lib/monitorTriggerPayload.ts";

test("open call rows omit null and blank optional identity fields", () => {
  const payload = buildManualTriggerPayload("MSFT", {
    position_id: "pos-call-1",
    option_type: "call",
    strike: 500,
    expiration: "2026-10-16",
    account_id: null,
    contract_id: "",
    instrument_id: "   ",
    is_paper: false,
  });

  assert.deepEqual(payload, {
    symbol: "MSFT",
    position_id: "pos-call-1",
    option_type: "call",
    strike: 500,
    expiration: "2026-10-16",
    is_paper: false,
    run_trigger: "manual",
    force_alpha: true,
  });
});

test("open put rows preserve every known present identity value", () => {
  const payload = buildManualTriggerPayload("NVDA", {
    position_id: "pos-put-1",
    option_type: "put",
    strike: "175.50",
    expiration: "2026-11-20",
    account_id: "acct-live",
    contract_id: "NVDA261120P00175500",
    instrument_id: "instrument-nvda-put",
    is_paper: true,
  });

  assert.deepEqual(payload, {
    symbol: "NVDA",
    position_id: "pos-put-1",
    option_type: "put",
    strike: "175.50",
    expiration: "2026-11-20",
    account_id: "acct-live",
    contract_id: "NVDA261120P00175500",
    instrument_id: "instrument-nvda-put",
    is_paper: true,
    run_trigger: "manual",
    force_alpha: true,
  });
});

test("missing optional IDs serialize without explicit identity nulls", () => {
  const json = JSON.stringify(
    buildManualTriggerPayload("AAPL", {
      position_id: "pos-put-legacy",
      option_type: "put",
      strike: 190,
      expiration: "2026-12-18",
    }),
  );

  assert.equal(json.includes('"account_id"'), false);
  assert.equal(json.includes('"contract_id"'), false);
  assert.equal(json.includes('"instrument_id"'), false);
});

test("non-empty malformed values remain for backend fail-closed validation", () => {
  const payload = omitAbsentMonitorConstraints({
    symbol: "MSFT",
    position_id: "pos-call-1",
    option_type: "calls",
    strike: true,
    expiration: ["2026-10-16"],
    account_id: 42,
    contract_id: { id: "contract" },
    instrument_id: false,
    is_paper: 1,
  });

  assert.deepEqual(payload, {
    symbol: "MSFT",
    position_id: "pos-call-1",
    option_type: "calls",
    strike: true,
    expiration: ["2026-10-16"],
    account_id: 42,
    contract_id: { id: "contract" },
    instrument_id: false,
    is_paper: 1,
  });
});

test("required position identity is never normalized away", () => {
  assert.deepEqual(
    omitAbsentMonitorConstraints({
      symbol: "MSFT",
      position_id: "   ",
      account_id: null,
    }),
    {
      symbol: "MSFT",
      position_id: "   ",
    },
  );
});

test("every optional canonical field and alias is omitted only when sparse", () => {
  for (const field of OPTIONAL_MONITOR_IDENTITY_FIELDS) {
    for (const value of [null, undefined, "", " \t "]) {
      const payload = omitAbsentMonitorConstraints({
        position_id: "pos-call-1",
        [field]: value,
        unrelated_null: null,
      });
      assert.equal(field in payload, false, `${field} retained ${String(value)}`);
      assert.equal(payload.unrelated_null, null);
    }
  }
});

test("source aliases use the same exact sparse allowlist", () => {
  const source = Object.fromEntries(
    OPTIONAL_MONITOR_IDENTITY_FIELDS.map((field) => [field, " \n "]),
  );
  const payload = omitAbsentMonitorConstraints({
    position_id: "pos-put-1",
    source: { ...source, unrelated_null: null },
  });

  assert.deepEqual(payload, {
    position_id: "pos-put-1",
    source: { unrelated_null: null },
  });
});

test("valid exact alias values survive at top level and in source", () => {
  const payload = omitAbsentMonitorConstraints({
    position_id: "pos-call-1",
    brokerage_account_id: "  Acct-Exact  ",
    contract_symbol: " MSFT261016C00500000 ",
    source: {
      instrument_identifier: " Instrument-Exact ",
      is_paper: false,
    },
  });

  assert.deepEqual(payload, {
    position_id: "pos-call-1",
    brokerage_account_id: "  Acct-Exact  ",
    contract_symbol: " MSFT261016C00500000 ",
    source: {
      instrument_identifier: " Instrument-Exact ",
      is_paper: false,
    },
  });
});

test("every allowlisted field preserves exact valid and malformed non-empty values", () => {
  for (const field of OPTIONAL_MONITOR_IDENTITY_FIELDS) {
    const exact =
      field === "is_paper"
        ? false
        : field === "strike"
          ? 500.25
          : ` Exact ${field} `;
    const malformed = { field };
    assert.deepEqual(
      omitAbsentMonitorConstraints({
        position_id: "pos-call-1",
        [field]: exact,
        source: { [field]: malformed },
      }),
      {
        position_id: "pos-call-1",
        [field]: exact,
        source: { [field]: malformed },
      },
    );
  }
});

test("conflicting aliases and malformed non-empty aliases remain fail-closed", () => {
  const payload = omitAbsentMonitorConstraints({
    position_id: "pos-put-1",
    account_id: "acct-a",
    account: "acct-b",
    option_contract_id: { malformed: true },
    source: {
      contract_id: "contract-a",
      occ_symbol: "contract-b",
      security_id: 42,
    },
  });

  assert.deepEqual(payload, {
    position_id: "pos-put-1",
    account_id: "acct-a",
    account: "acct-b",
    option_contract_id: { malformed: true },
    source: {
      contract_id: "contract-a",
      occ_symbol: "contract-b",
      security_id: 42,
    },
  });
});

test("both monitor row types share the sparse contract", () => {
  for (const option_type of ["call", "put"]) {
    const payload = buildManualTriggerPayload("MSFT", {
      position_id: `pos-${option_type}`,
      option_type,
      account: null,
      source: {
        option_contract_id: "",
        security_id: "   ",
      },
    });
    assert.deepEqual(payload, {
      symbol: "MSFT",
      position_id: `pos-${option_type}`,
      option_type,
      source: {},
      run_trigger: "manual",
      force_alpha: true,
    });
  }
});
