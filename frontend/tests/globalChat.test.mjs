import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(new URL("../package.json", import.meta.url));
const React = require("react");
const ts = require("typescript");
const source = readFileSync(new URL("../src/components/GlobalChatView.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const settle = () => new Promise((resolve) => setImmediate(resolve));
const elements = (node) => !node || typeof node !== "object" ? []
  : Array.isArray(node) ? node.flatMap(elements) : [node, ...elements(node.props?.children)];

function harness(t) {
  const hooks = [], requests = [], responses = [], cleanups = [];
  let cursor = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (url, options) => {
    requests.push({ url, options });
    const response = responses.shift();
    assert.ok(response, "No automatic or unexpected backend requests");
    return typeof response === "function" ? response() : Promise.resolve(response);
  };
  const react = {
    ...React,
    useState(initial) {
      const index = cursor++;
      if (!(index in hooks)) hooks[index] = typeof initial === "function" ? initial() : initial;
      return [hooks[index], (value) => {
        hooks[index] = typeof value === "function" ? value(hooks[index]) : value;
      }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in hooks)) hooks[index] = { current: initial };
      return hooks[index];
    },
    useEffect(effect) {
      const index = cursor++;
      if (!(index in hooks)) {
        hooks[index] = true;
        const cleanup = effect();
        if (cleanup) cleanups.push(cleanup);
      }
    },
  };
  const loaded = { exports: {} };
  new Function("require", "module", "exports", compiled)((name) => {
    if (name === "react") return react;
    if (name === "@/lib/markdown") return { renderMarkdown: (value) => value };
    return require(name);
  }, loaded, loaded.exports);
  t.after(() => {
    cleanups.forEach((cleanup) => cleanup());
    globalThis.fetch = originalFetch;
  });
  return {
    requests, responses,
    render() { cursor = 0; return elements(loaded.exports.default()); },
  };
}
const input = (tree) => tree.find((node) => node.type === "input");
const form = (tree) => tree.find((node) => node.type === "form");
const reset = (tree) => tree.find((node) => node.type === "button" && node.props.children === "New conversation");
const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
const text = (tree) => tree.map((node) => [
  typeof node.props?.children === "string" ? node.props.children : "",
  node.props?.dangerouslySetInnerHTML?.__html ?? "",
].join(" ")).join(" ");

test("Chat mounts directly with local greeting and no mode chooser or model call", (t) => {
  const h = harness(t);
  const tree = h.render();
  assert.ok(form(tree) && input(tree));
  assert.match(text(tree), /I'm Chat/);
  assert.doesNotMatch(text(tree), /Options Chat|Quick Analysis|API Chat|Choose Chat Mode/);
  assert.equal(h.requests.length, 0);
});

test("send uses compatible api mode, excludes greeting and retains conversation history", async (t) => {
  const h = harness(t);
  input(h.render()).props.onChange({ target: { value: "Show my holdings" } });
  h.responses.push(reply({ reply: "**Current holdings**" }));
  form(h.render()).props.onSubmit({ preventDefault() {} });
  await settle();
  assert.deepEqual(JSON.parse(h.requests[0].options.body), {
    mode: "api", messages: [{ role: "user", content: "Show my holdings" }],
  });
  assert.match(text(h.render()), /\*\*Current holdings\*\*/);
  input(h.render()).props.onChange({ target: { value: "And the news?" } });
  h.responses.push(reply({ reply: "News unavailable" }));
  form(h.render()).props.onSubmit({ preventDefault() {} });
  await settle();
  assert.deepEqual(JSON.parse(h.requests[1].options.body).messages, [
    { role: "user", content: "Show my holdings" },
    { role: "assistant", content: "**Current holdings**" },
    { role: "user", content: "And the news?" },
  ]);
});

test("new conversation aborts pending requests and fences stale replies; errors unlock input", async (t) => {
  const h = harness(t);
  let complete;
  input(h.render()).props.onChange({ target: { value: "Old question" } });
  h.responses.push(() => new Promise((resolve) => { complete = resolve; }));
  form(h.render()).props.onSubmit({ preventDefault() {} });
  assert.equal(input(h.render()).props.disabled, true);
  reset(h.render()).props.onClick();
  assert.equal(h.requests[0].options.signal.aborted, true);
  complete(reply({ reply: "Stale response" }));
  await settle();
  assert.doesNotMatch(text(h.render()), /Old question|Stale response/);
  assert.match(text(h.render()), /I'm Chat/);
  input(h.render()).props.onChange({ target: { value: "New question" } });
  h.responses.push(reply({ error: "MCP unavailable" }, 503));
  form(h.render()).props.onSubmit({ preventDefault() {} });
  await settle();
  assert.match(text(h.render()), /MCP unavailable/);
  assert.equal(input(h.render()).props.disabled, false);
  assert.deepEqual(JSON.parse(h.requests[1].options.body).messages, [
    { role: "user", content: "New question" },
  ]);
});
