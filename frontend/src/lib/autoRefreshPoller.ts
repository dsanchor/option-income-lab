export interface AutoRefreshScheduler {
  setTimeout(callback: () => void, delayMs: number): ReturnType<typeof setTimeout>;
  clearTimeout(timer: ReturnType<typeof setTimeout>): void;
}

export interface AutoRefreshPoller {
  start(): void;
  stop(): void;
  checkNow(): Promise<void>;
}

interface AutoRefreshPollerOptions {
  poll(signal: AbortSignal): Promise<string | null>;
  onChange(): void;
  intervalMs: number;
  timeoutMs: number;
  scheduler?: AutoRefreshScheduler;
}

const MIN_DELAY_MS = 1;
const MAX_INTERVAL_MS = 5 * 60 * 1000;
const MAX_TIMEOUT_MS = 60 * 1000;

const boundedDelay = (value: number, maximum: number) =>
  Math.max(MIN_DELAY_MS, Math.min(maximum, Math.trunc(value)));

export function createAutoRefreshPoller({
  poll,
  onChange,
  intervalMs,
  timeoutMs,
  scheduler = {
    setTimeout: (callback, delayMs) => setTimeout(callback, delayMs),
    clearTimeout: (timer) => clearTimeout(timer),
  },
}: AutoRefreshPollerOptions): AutoRefreshPoller {
  const pollIntervalMs = boundedDelay(intervalMs, MAX_INTERVAL_MS);
  const requestTimeoutMs = boundedDelay(timeoutMs, MAX_TIMEOUT_MS);
  let active = false;
  let generation = 0;
  let inFlight = false;
  let signature: string | null = null;
  let pollTimer: ReturnType<typeof setTimeout> | null = null;
  let deadlineTimer: ReturnType<typeof setTimeout> | null = null;
  let controller: AbortController | null = null;

  const clearPollTimer = () => {
    if (pollTimer !== null) {
      scheduler.clearTimeout(pollTimer);
      pollTimer = null;
    }
  };

  const schedule = (delayMs: number) => {
    if (!active || pollTimer !== null || inFlight) return;
    pollTimer = scheduler.setTimeout(() => {
      pollTimer = null;
      void checkNow();
    }, delayMs);
  };

  const checkNow = async () => {
    if (!active || inFlight) return;
    inFlight = true;
    const runGeneration = generation;
    const runController = new AbortController();
    controller = runController;
    deadlineTimer = scheduler.setTimeout(
      () => runController.abort(),
      requestTimeoutMs,
    );

    try {
      const nextSignature = await poll(runController.signal);
      if (
        !active
        || generation !== runGeneration
        || runController.signal.aborted
        || nextSignature === null
      ) {
        return;
      }
      if (signature === null) {
        signature = nextSignature;
      } else if (nextSignature !== signature) {
        signature = nextSignature;
        onChange();
      }
    } catch {
      // Network failures and aborts are retried at the bounded polling cadence.
    } finally {
      if (deadlineTimer !== null) {
        scheduler.clearTimeout(deadlineTimer);
        deadlineTimer = null;
      }
      if (controller === runController) controller = null;
      inFlight = false;
      if (active) {
        schedule(generation === runGeneration ? pollIntervalMs : 0);
      }
    }
  };

  return {
    start() {
      if (active) return;
      active = true;
      generation += 1;
      schedule(0);
    },
    stop() {
      active = false;
      generation += 1;
      clearPollTimer();
      if (deadlineTimer !== null) {
        scheduler.clearTimeout(deadlineTimer);
        deadlineTimer = null;
      }
      controller?.abort();
      controller = null;
    },
    checkNow,
  };
}
