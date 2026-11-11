import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function load(path, overrides = {}) {
  const source = readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
  const code = ts.transpileModule(source, {
    fileName: path,
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} };
  new Function("require", "module", "exports", code)(
    (name) => overrides[name] ?? (name.startsWith("@/") ? {} : require(name)),
    module, module.exports,
  );
  return module.exports.default;
}

const DualRollPanel = load("src/components/DualRollPanel.tsx", {
  "@/components/RuleEvaluationPanel": { default: () => null },
});
const ActivityDetailView = load("src/components/ActivityDetailView.tsx", {
  "next/link": { default: ({ children, href }) => createElement("a", { href }, children) },
  "@/components/DualRollPanel": { default: DualRollPanel },
  "@/components/RuleEvaluationPanel": { default: () => null },
  "@/components/ActivityActions": { default: () => null },
  "@/components/ApplyRecommendation": { default: () => createElement("button", null, "Apply Recommendation") },
});
function activity() {
  return {
    id: "activity-1", symbol: "TEST", activity: "ROLL_UP_AND_OUT",
    new_strike: 105, new_expiration: "2026-12-09",
    roll_alternatives: {
      schema_version: 1, preferred_alternative_id: "DEFENSIVE",
      preference_reason: "Safer shorter exposure.", action_target_id: "DEFENSIVE",
      alternatives: {
        DEFENSIVE: {
          alternative_id: "DEFENSIVE", status: "available", new_strike: 105,
          new_expiration: "2026-12-09", dte: 28,
          roll_economics: { buyback_cost: 2, new_premium: 1.5, net_credit: -0.5 },
        },
        CREDIT_EXTENDED: {
          alternative_id: "CREDIT_EXTENDED", status: "available", new_strike: 105,
          new_expiration: "2026-12-31", dte: 50, advisory_only: true,
          roll_economics: { buyback_cost: 2, new_premium: 2.5, net_credit: 0.5, net_credit_total: 150, quantity: 3, multiplier: 100 },
        },
      },
    },
  };
}
const panel = (a) => renderToStaticMarkup(createElement(DualRollPanel, { activity: a }));
const detail = (a) => renderToStaticMarkup(createElement(ActivityDetailView, {
  data: { activity: a, symbol: "TEST", agent_type: "open_call_monitor", agent_label: "Monitor" },
}));

test("both named contracts and estimated economics render with primary action target unchanged", () => {
  const a = activity();
  a.supervisor_view = { alternative_reviews: {
    DEFENSIVE: { review_status: "complete", net_assessment: "ORIGINAL_HOLDS", challenge_strength: "WEAK", one_liner: "Shorter is safe" },
    CREDIT_EXTENDED: { review_status: "complete", net_assessment: "RECONSIDER", challenge_strength: "STRONG", one_liner: "More exposure" },
  } };
  a.alpha_view = { comparison_status: "complete", preferred_alternative_id: "CREDIT_EXTENDED", comparison_rationale: "Extra credit, longer commitment." };
  const html = panel(a);
  for (const text of ["DEFENSIVE", "CREDIT_EXTENDED", "2026-12-09", "2026-12-31", "$150.00",
    "before commissions", "not guaranteed", "Original decision holds", "Reconsider", "Alpha prefers", "Extra credit"]) {
    assert.ok(html.includes(text), text);
  }
  assert.ok(html.includes("Apply roll always targets DEFENSIVE"));
  assert.ok(!html.includes("<button"));
  assert.equal(a.new_expiration, "2026-12-09");
});

test("unscoped legacy verdict never approves both alternatives or creates an inferred Alpha preference", () => {
  const a = activity();
  a.supervisor_view = { challenge_strength: "WEAK", net_assessment: "ORIGINAL_HOLDS", one_liner: "Everything is safe" };
  a.alpha_view = { opportunity_strength: "STRONG", alternative: { strike: 999 }, one_liner: "Global Alpha claim" };
  const html = detail(a);
  assert.equal((html.match(/Scoped audit unavailable/g) ?? []).length, 2);
  assert.ok(html.includes("Scoped comparison unavailable"));
  assert.ok(!html.includes("Everything is safe"));
  assert.ok(!html.includes("Global Alpha claim"));
  assert.ok(!html.includes("Apply Recommendation"));
});

test("missing eligible data and complete no-candidate outcomes are explicit, not success-shaped contracts", () => {
  const a = activity();
  a.roll_alternatives.alternatives.CREDIT_EXTENDED = {
    alternative_id: "CREDIT_EXTENDED", status: "incomplete_data", reason: "Quotes stale",
  };
  let html = panel(a);
  assert.ok(html.includes("incomplete data") && html.includes("Quotes stale"));
  assert.ok(!html.includes("2026-12-31"));
  a.roll_alternatives.alternatives.CREDIT_EXTENDED.status = "no_candidate";
  a.roll_alternatives.alternatives.CREDIT_EXTENDED.reason = "No positive credit";
  html = panel(a);
  assert.ok(html.includes("no candidate") && html.includes("No positive credit"));
});

test("legacy single-contract activities keep existing Supervisor and Alpha rendering", () => {
  const a = activity();
  delete a.roll_alternatives;
  a.supervisor_view = { challenge_strength: "WEAK", net_assessment: "ORIGINAL_HOLDS", one_liner: "Legacy holds" };
  a.alpha_view = { opportunity_strength: "NONE", one_liner: "Legacy Alpha" };
  const html = detail(a);
  assert.ok(html.includes("Legacy holds") && html.includes("Legacy Alpha"));
  assert.ok(html.includes("Apply Recommendation"));
  assert.ok(!html.includes("Roll alternatives"));
});

test("optional additional Alpha suggestion identifies baseline and stays advisory with no action button", () => {
  const a = activity();
  a.alpha_view = {
    comparison_status: "complete", preferred_alternative_id: "DEFENSIVE",
    comparison_rationale: "Less exposure.", relaxed_parameter: "technical_borderline",
    additional_proposal_status: "available",
    additional_alternative: {
      baseline_alternative_id: "DEFENSIVE", advisory_only: true, strike: 110,
      expiration: "2026-12-09", dte: 28, trade_off: "Less premium.",
      roll_economics: { net_credit: 0.3, net_credit_total: 90 },
    },
  };
  const html = panel(a);
  assert.ok(html.includes("Additional Alpha suggestion · advisory only"));
  assert.ok(html.includes("Baseline: DEFENSIVE") && html.includes("$90.00"));
  assert.ok(!html.includes("<button"));
});
