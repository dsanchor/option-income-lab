import assert from "node:assert/strict";
import test from "node:test";

// @ts-expect-error Node's type-stripping test runner requires the source extension.
import { createAutoRefreshPoller, type AutoRefreshScheduler } from "../src/lib/autoRefreshPoller.ts";

class FakeScheduler implements AutoRefreshScheduler {
  now = 0;
  nextId = 1;
  timers = new Map<number, { at: number; callback: () => void }>();

  setTimeout(callback: () => void, delayMs: number) {
    const id = this.nextId++;
    this.timers.set(id, { at: this.now + delayMs, callback });
    return id as unknown as ReturnType<typeof setTimeout>;
  }

  clearTimeout(timer: ReturnType<typeof setTimeout>) {
    this.timers.delete(timer as unknown as number);
  }

  advance(delayMs: number) {
    const target = this.now + delayMs;
    while (true) {
      const next = [...this.timers.entries()]
        .filter(([, timer]) => timer.at <= target)
        .sort((left, right) => left[1].at - right[1].at)[0];
      if (!next) break;
      const [id, timer] = next;
      this.timers.delete(id);
      this.now = timer.at;
      timer.callback();
    }
    this.now = target;
  }
}

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

test("hung polls never overlap across repeated intervals", async () => {
  const scheduler = new FakeScheduler();
  let calls = 0;
  const poller = createAutoRefreshPoller({
    scheduler,
    intervalMs: 10,
    timeoutMs: 100,
    poll: async () => {
      calls += 1;
      return new Promise<string>(() => {});
    },
    onChange: () => assert.fail("hung request cannot refresh"),
  });

  poller.start();
  scheduler.advance(0);
  scheduler.advance(99);
  await flush();

  assert.equal(calls, 1);
});

test("timeout aborts, remains bounded, and permits a successful later poll", async () => {
  const scheduler = new FakeScheduler();
  let calls = 0;
  let refreshes = 0;
  const poller = createAutoRefreshPoller({
    scheduler,
    intervalMs: 10,
    timeoutMs: 5,
    poll: (signal) => {
      calls += 1;
      if (calls === 1) {
        return new Promise<string>((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(new Error("aborted")));
        });
      }
      return Promise.resolve(calls === 2 ? "baseline" : "changed");
    },
    onChange: () => {
      refreshes += 1;
    },
  });

  poller.start();
  scheduler.advance(0);
  scheduler.advance(5);
  await flush();
  scheduler.advance(10);
  await flush();
  scheduler.advance(10);
  await flush();

  assert.equal(calls, 3);
  assert.equal(refreshes, 1);
});

test("stop aborts and late completion cannot update or schedule work", async () => {
  const scheduler = new FakeScheduler();
  let resolvePoll: ((value: string) => void) | undefined;
  let refreshes = 0;
  const poller = createAutoRefreshPoller({
    scheduler,
    intervalMs: 10,
    timeoutMs: 100,
    poll: async () =>
      new Promise<string>((resolve) => {
        resolvePoll = resolve;
      }),
    onChange: () => {
      refreshes += 1;
    },
  });

  poller.start();
  scheduler.advance(0);
  poller.stop();
  resolvePoll?.("late");
  await flush();
  scheduler.advance(1000);
  await flush();

  assert.equal(refreshes, 0);
  assert.equal(scheduler.timers.size, 0);
});

test("restart waits for an aborted late request before polling again", async () => {
  const scheduler = new FakeScheduler();
  let calls = 0;
  let resolveFirst: ((value: string) => void) | undefined;
  const poller = createAutoRefreshPoller({
    scheduler,
    intervalMs: 10,
    timeoutMs: 100,
    poll: async () => {
      calls += 1;
      if (calls === 1) {
        return new Promise<string>((resolve) => {
          resolveFirst = resolve;
        });
      }
      return "current";
    },
    onChange: () => assert.fail("late response cannot refresh"),
  });

  poller.start();
  scheduler.advance(0);
  poller.stop();
  poller.start();
  scheduler.advance(1000);
  assert.equal(calls, 1);

  resolveFirst?.("stale");
  await flush();
  scheduler.advance(0);
  await flush();

  assert.equal(calls, 2);
});
