import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import ts from "typescript";

const source = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const require = createRequire(import.meta.url);

function loadModule(path, overrides = {}, extraSource = "") {
  const code = ts.transpileModule(source(path) + extraSource, {
    fileName: path,
    compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  const compiled = { exports: {} };
  new Function("require", "module", "exports", code)(
    (name) => overrides[name] ?? (name.startsWith("@/") ? {} : require(name)),
    compiled, compiled.exports,
  );
  return compiled.exports;
}

const { isValidExpirationDate } = loadModule("src/lib/positionExpiration.ts");

test("expiration accepts real historical and future dates, not normalized invalid dates", () => {
  for (const value of ["2024-02-29", "2020-01-01", "2099-12-31", "0001-01-01"]) {
    assert.equal(isValidExpirationDate(value), true, value);
  }
  for (const value of ["", "2026-02-29", "2026-04-31", "0000-01-01", "2026-1-01",
    "20261120", "2026-11-20T00:00:00Z", " 2026-11-20"]) {
    assert.equal(isValidExpirationDate(value), false, value);
  }
});

const routePath = "src/app/api/symbols/[symbol]/positions/[positionId]/expiration/route.ts";
const { PATCH } = loadModule(routePath, {
  "next/server": { NextResponse: { json: (body, init) => Response.json(body, init) } },
  "@/lib/api": { API_BASE_URL: "https://backend.invalid" },
  "@/lib/symbolEncoding": { decodeSymbolParam: decodeURIComponent },
});

test("expiration proxy forwards the exact correction and preserves upstream failures", async (t) => {
  const body = JSON.stringify({ expiration: "2024-02-29" });
  t.mock.method(globalThis, "fetch", async (url, init) => {
    assert.equal(url, "https://backend.invalid/api/symbols/XNAS%3AAAPL/positions/pos%2F1/expiration");
    assert.equal(init.method, "PATCH");
    assert.equal(init.body, body);
    return Response.json({ error: "Position changed concurrently" }, { status: 409 });
  });
  const response = await PATCH(new Request("https://frontend.invalid", {
    method: "PATCH", body,
  }), { params: Promise.resolve({ symbol: "XNAS%3AAAPL", positionId: "pos/1" }) });
  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), { error: "Position changed concurrently" });
});

test("expiration proxy reports unreachable backend without reporting success", async (t) => {
  t.mock.method(globalThis, "fetch", async () => { throw new Error("Unavailable"); });
  const response = await PATCH(new Request("https://frontend.invalid", {
    method: "PATCH", body: "{}",
  }), { params: Promise.resolve({ symbol: "AAPL", positionId: "pos1" }) });
  assert.equal(response.status, 502);
});

test("inline date correction uses save/cancel/error flow and resets contract-dependent panels", () => {
  const detail = source("src/components/PositionDetail.tsx");
  const table = source("src/components/PositionsTable.tsx");
  assert.match(detail, /field="expiration"/);
  assert.match(detail, /isDate && !isValidExpirationDate\(draft\)/);
  assert.match(detail, /type=\{isDate \? "date" : "number"\}/);
  assert.match(detail, /JSON\.stringify\(\{ \[field\]: isDate \? draft : parsed \}\)/);
  assert.match(detail, /if \(!res\.ok\) throw new Error/);
  assert.match(detail, /setEditing\(false\);\s+router\.refresh\(\)/);
  assert.match(detail, /event\.key === "Escape" && !saving/);
  assert.match(detail, /role="alert"/);
  assert.match(table, /<PositionDetail key=\{`\$\{p\.position_id\}:\$\{p\.expiration\}`\}/);
});

function editorHarness() {
  const state = [];
  let cursor = 0;
  let refreshes = 0;
  const { EditablePositionField } = loadModule("src/components/PositionDetail.tsx", {
    react: {
      useState: (initial) => {
        const index = cursor++;
        if (!(index in state)) state[index] = initial;
        return [state[index], (value) => { state[index] = value; }];
      },
    },
    "next/navigation": { useRouter: () => ({ refresh: () => { refreshes++; } }) },
    "@/lib/positionExpiration": { isValidExpirationDate },
  }, "\nexport { EditablePositionField };");

  function elements(node) {
    if (Array.isArray(node)) return node.flatMap(elements);
    if (!node || typeof node !== "object") return [];
    return [node, ...elements(node.props?.children)];
  }
  return {
    render() {
      cursor = 0;
      const nodes = elements(EditablePositionField({
        symbol: "AAPL", positionId: "pos/1", label: "Expiration",
        field: "expiration", value: "2026-11-20",
      }));
      return {
        button: (label) => nodes.find((n) => n.type === "button" && n.props.children === label),
        input: nodes.find((n) => n.type === "input"),
        alert: nodes.find((n) => n.props?.role === "alert"),
      };
    },
    get refreshes() { return refreshes; },
  };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test("inline editor saves the exact date, disables pending controls and refreshes once", async (t) => {
  const editor = editorHarness();
  let finish;
  t.mock.method(globalThis, "fetch", (url, init) => {
    assert.equal(url, "/api/symbols/AAPL/positions/pos%2F1/expiration");
    assert.deepEqual(JSON.parse(init.body), { expiration: "2024-02-29" });
    return new Promise((resolve) => { finish = resolve; });
  });
  editor.render().button("Edit").props.onClick();
  editor.render().input.props.onChange({ target: { value: "2024-02-29" } });
  editor.render().button("Save").props.onClick();
  assert.equal(editor.render().button("Saving…").props.disabled, true);
  assert.equal(editor.render().button("Cancel").props.disabled, true);
  finish(Response.json({ positions: [] }));
  await settle();
  assert.ok(editor.render().button("Edit"));
  assert.equal(editor.refreshes, 1);
});

test("inline editor preserves failed draft, displays errors, and cancels without writing", async (t) => {
  const editor = editorHarness();
  const fetch = t.mock.method(globalThis, "fetch", async () => (
    Response.json({ error: "Position changed concurrently" }, { status: 409 })
  ));
  editor.render().button("Edit").props.onClick();
  editor.render().input.props.onChange({ target: { value: "2026-02-29" } });
  editor.render().button("Save").props.onClick();
  assert.ok(editor.render().alert);
  assert.equal(fetch.mock.callCount(), 0);
  editor.render().input.props.onChange({ target: { value: "2024-02-29" } });
  editor.render().button("Save").props.onClick();
  await settle();
  assert.equal(editor.render().input.props.value, "2024-02-29");
  assert.match(JSON.stringify(editor.render().alert.props.children), /Position changed concurrently/);
  assert.equal(editor.refreshes, 0);
  editor.render().button("Cancel").props.onClick();
  assert.ok(editor.render().button("Edit"));
  assert.equal(fetch.mock.callCount(), 1);
  editor.render().button("Edit").props.onClick();
  assert.equal(editor.render().input.props.value, "2026-11-20");
  editor.render().input.props.onKeyDown({ key: "Escape", stopPropagation() {} });
  assert.ok(editor.render().button("Edit"));
  assert.equal(fetch.mock.callCount(), 1);
});
