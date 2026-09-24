import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  fetchAllMovementPages,
  LatestMovementRequest,
} from "../src/lib/movementPagination.ts";

const components = [
  "PortfolioMovementsTable.tsx",
  "StockTransactionsTable.tsx",
];

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("latest movement request wins", () => {
  for (const component of components) {
    it(`${component} wires cancellation, stale guards, pagination, and dedupe`, () => {
      const source = fs.readFileSync(
        new URL(`../src/components/${component}`, import.meta.url),
        "utf8",
      );
      assert.match(source, /LatestMovementRequest/);
      assert.match(source, /requestRef\.current\.begin\(\)/);
      assert.match(source, /request\.isCurrent\(\)/);
      assert.match(source, /signal:\s*request\.signal/);
      assert.match(source, /requestRef\.current\.cancel\(\)/);
      assert.match(source, /dedupeMovementsById/);
      assert.match(source, /fetchAllMovementPages/);
    });
  }

  for (const component of components) {
    for (const newerFilter of ["BUY", "SELL", "date-range"]) {
      it(`${component}: delayed Dividend cannot overwrite newer ${newerFilter} state`, async () => {
        const manager = new LatestMovementRequest();
        const dividend = manager.begin();
        const slowDividend = deferred();
        const commits = [];

        const dividendWork = slowDividend.promise.then((value) => {
          if (dividend.isCurrent()) commits.push(value);
        });

        const newer = manager.begin();
        if (newer.isCurrent()) commits.push(newerFilter);
        slowDividend.resolve("DIVIDEND");
        await dividendWork;

        assert.deepEqual(commits, [newerFilter]);
        assert.equal(dividend.signal.aborted, true);
      });
    }
  }

  it("unmount cancellation invalidates the active request", () => {
    const manager = new LatestMovementRequest();
    const active = manager.begin();
    manager.cancel();
    assert.equal(active.signal.aborted, true);
    assert.equal(active.isCurrent(), false);
  });
});

describe("complete defensive movement batching", () => {
  it("overlap does not stop before a unique later row", async () => {
    const pages = new Map([
      [0, [{ id: "a" }, { id: "b" }]],
      [2, [{ id: "b" }, { id: "c" }]],
      [4, [{ id: "d" }]],
    ]);
    const controller = new AbortController();
    const offsets = [];

    const result = await fetchAllMovementPages({
      pageSize: 2,
      signal: controller.signal,
      fetchPage: async (offset) => {
        offsets.push(offset);
        return {
          movements: pages.get(offset) ?? [],
          total_count: 4,
          limit: 2,
          offset,
        };
      },
    });

    assert.deepEqual(offsets, [0, 2, 4]);
    assert.deepEqual(result.movements.map((row) => row.id), ["a", "b", "c", "d"]);
    assert.equal(result.rawRowCount, 5);
    assert.equal(result.reportedTotal, 4);
    assert.equal(result.incomplete, false);
  });

  it("equal-date rows remain exactly once across page boundaries", async () => {
    const ordered = ["movement-d", "movement-c", "movement-b", "movement-a"].map(
      (id) => ({ id, trade_date: "2026-09-24" }),
    );
    const controller = new AbortController();
    const result = await fetchAllMovementPages({
      pageSize: 2,
      signal: controller.signal,
      fetchPage: async (offset, limit) => ({
        movements: ordered.slice(offset, offset + limit),
        total_count: ordered.length,
        limit,
        offset,
      }),
    });

    assert.deepEqual(result.movements.map((row) => row.id), [
      "movement-d",
      "movement-c",
      "movement-b",
      "movement-a",
    ]);
    assert.equal(new Set(result.movements.map((row) => row.id)).size, 4);
  });
});
