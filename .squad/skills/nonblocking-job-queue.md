# Skill: Non-Blocking Job Queue Pattern

**When to use:** You have a scheduler/daemon with a main loop that needs to execute long-running jobs without blocking the loop itself (e.g., to keep heartbeat ticking, UI responsive, schedule advancement).

**When NOT to use:**
- Jobs are fast (<1 second) — just run them inline
- Jobs can run concurrently — use ThreadPoolExecutor with N workers instead
- You need complex job orchestration (dependencies, priorities, retries) — use a real task queue like Celery/RQ

## Pattern

**Architecture:**
```
Main Loop Thread           Worker Thread
      |                         |
      |-- Detect due job        |
      |-- Advance next_run      |
      |-- Enqueue job --------> | Receive job
      |-- Continue looping      | Execute job_func()
      |                         | Set last_run, clear running flag
      |                         | Loop for next job
```

**Key Properties:**
- Jobs execute sequentially (one at a time) off the main loop thread
- Main loop NEVER blocks on job execution
- Overlap guard prevents duplicate runs of the same job
- Worker thread is a daemon (dies when main thread exits, unless detached)

## Implementation Template

```python
import queue
import threading
from datetime import datetime

class JobRegistry:
    def __init__(self):
        self.jobs = {}
        self._job_queue = queue.Queue()
        self._worker_thread = None
        self._shutdown = False
    
    def start_worker(self):
        """Start the worker thread (call once)."""
        if not self._worker_thread or not self._worker_thread.is_alive():
            self._shutdown = False
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                daemon=True,
                name="JobWorker"
            )
            self._worker_thread.start()
    
    def _worker_loop(self):
        """Worker thread: consumes jobs from queue and executes them."""
        while not self._shutdown:
            try:
                # Wait for a job (timeout so we can check shutdown flag)
                try:
                    job_id = self._job_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                
                job = self.jobs.get(job_id)
                if not job:
                    continue
                
                # Execute the job
                start_time = datetime.now()
                try:
                    job['func']()
                    job['last_run'] = start_time
                except Exception as e:
                    print(f"ERROR in {job_id}: {e}")
                    job['last_run'] = start_time  # Record attempt even on failure
                finally:
                    job['running'] = False
                    
            except Exception as e:
                print(f"Worker thread error: {e}")
    
    def execute_due_jobs(self, now):
        """Main loop: detect due jobs, advance next_run, enqueue."""
        for job_id, job in self.jobs.items():
            if not job['enabled'] or not job['next_run']:
                continue
            
            if now >= job['next_run']:
                # Overlap guard: skip if previous run still in progress
                if job['running']:
                    print(f"Skipping {job_id}: previous run still in progress")
                    job['next_run'] = self._advance_next_run(job, now)
                    continue
                
                # Advance next_run BEFORE dispatching
                job['next_run'] = self._advance_next_run(job, now)
                
                # Enqueue job for worker thread
                job['running'] = True
                self._job_queue.put(job_id)
    
    def _advance_next_run(self, job, now):
        """Advance next_run to the next future occurrence."""
        next_run = job['cron'].get_next(datetime)
        
        # Guard against stale cron base: loop until strictly in future
        max_iterations = 100
        iterations = 0
        while next_run <= now and iterations < max_iterations:
            next_run = job['cron'].get_next(datetime)
            iterations += 1
        
        return next_run
    
    def shutdown(self):
        """Shutdown the worker thread cleanly."""
        self._shutdown = True
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5.0)
```

## Main Loop Integration

```python
def run(self):
    registry = JobRegistry()
    
    # Register jobs
    registry.jobs['heavy_task'] = {
        'func': self.do_heavy_work,
        'enabled': True,
        'next_run': calculate_next_run(),
        'cron': croniter(...),
        'running': False,
        'last_run': None,
    }
    
    # Start worker thread
    registry.start_worker()
    
    # Main loop
    while self.running:
        now = datetime.now()
        
        # Detect and enqueue due jobs (non-blocking)
        registry.execute_due_jobs(now)
        
        # Other loop work (heartbeat, config reload, etc.)
        time.sleep(1)
    
    # Shutdown
    registry.shutdown()
```

## Key Details

**Overlap Guard:**
- Each job has a `running: bool` flag
- Set to `True` when enqueuing, `False` when job completes
- If a job is due but `running == True`, skip it and advance next_run
- Prevents duplicate runs of the same job

**Advance next_run BEFORE dispatch:**
- Always compute the NEXT future occurrence BEFORE enqueuing the job
- Loop `get_next()` until the result is strictly in the future
- UI/monitoring always shows a future next_run, never a past timestamp

**Worker thread is sequential:**
- Only ONE worker thread, executes jobs one at a time
- Use this pattern when jobs are NOT thread-safe (shared state, no locks)
- For thread-safe jobs, use `ThreadPoolExecutor` with N workers instead

**Shutdown:**
- Set `_shutdown = True` to stop the worker loop
- Join worker thread with timeout (e.g., 5s) to wait for current job to finish
- Don't try to drain the queue — unfinished jobs are lost

**Error isolation:**
- Worker thread catches exceptions, logs them, and continues
- A failing job NEVER kills the worker thread
- `last_run` is still set (records the attempt) even on exception

## Pitfalls to Avoid

1. **Don't advance next_run AFTER job completes** — UI will show past timestamps during long runs
2. **Don't skip the overlap guard** — duplicate runs can corrupt state or waste resources
3. **Don't use multiple workers** unless jobs are proven thread-safe — sequential is safer
4. **Don't forget to call `shutdown()`** — worker thread won't stop cleanly on exit
5. **Don't join the worker thread indefinitely** — use a timeout to prevent hanging on exit

## Validation Checklist

- [ ] Import check: `python3 -c "import your_module; print('OK')"`
- [ ] Runtime check: Job runs off main thread (loop not blocked)
- [ ] Runtime check: `next_run` is in the future immediately after dispatch
- [ ] Runtime check: Job runs only once per due time (no duplicates)
- [ ] Runtime check: `last_run` gets set after execution
- [ ] Runtime check: Overlap guard prevents double-run
- [ ] Shutdown check: Worker thread stops within timeout

## Real-World Example

**options-agent scheduler fix (2026-06-29):**
- **Problem:** Long-running agent jobs (LLM + yfinance calls) blocked the scheduler loop → UI showed past `next_run` timestamps, heartbeat froze
- **Solution:** Implemented this pattern in `src/scheduler_registry.py`
  - Worker thread executes jobs sequentially
  - Main loop advances `next_run` before enqueuing
  - Overlap guard prevents duplicate `run_all_agents()` calls
- **Result:** Loop never freezes, UI always shows future timestamps, heartbeat ticks every 10 minutes
- **Files:** `src/scheduler_registry.py` (TaskRegistry class), `src/main.py` (main loop)
