# Skill: Scheduler Task Integration

**Context:** Adding new scheduled tasks to the Options Agent Scheduler (src/main.py)  
**Pattern:** Task Registry-based scheduling (replaced 11-point boilerplate as of 2026-06-26)  
**When to use:** Any time a new periodic job needs to be added to the system

---

## The Pattern (NEW — Registry-Based)

The scheduler uses a **TaskRegistry** to manage all periodic tasks with centralized handling of:
- Cron parsing and next-run calculation
- Config reload from CosmosDB
- Cron change detection (for web UI live updates)
- Task execution with error isolation
- Consistent logging and display

**Each task requires only 2 integration points** (down from 11):
1. Register the task in `run()` with: name, display_name, config_key, default_cron, job_func
2. Add a reschedule method (for web UI compatibility)

---

## Step-by-Step Integration

### 1. Add Config Entry

**File:** `config.yaml`

```yaml
my_new_task:
  enabled: true
  cron: "0 12 * * 1-5"  # Noon weekdays
  # ... task-specific settings
```

### 2. Add Config Accessor (Optional, for task logic)

**File:** `src/config.py`

```python
@property
def my_new_task_enabled(self) -> bool:
    return bool(self.config.get('my_new_task', {}).get('enabled', True))

@property
def my_new_task_cron(self) -> str:
    return str(self.config.get('my_new_task', {}).get('cron', '0 12 * * 1-5'))
```

*Note: The registry reads config directly, so these accessors are optional — only add if your task logic needs them.*

### 3. Add Job Methods

**File:** `src/main.py`

Add your task's execution logic (same pattern as before — sync wrapper + async implementation):

```python
def run_my_new_task_job(self):
    """Execute my_new_task (bridges async to sync for scheduler)."""
    _run_async(self._run_my_new_task_async())

async def _run_my_new_task_async(self):
    """Run my_new_task if enabled in config."""
    task_config = self.config.config.get('my_new_task', {})
    if not task_config.get('enabled', True):
        print("⏭️  My New Task disabled in config")
        return

    from .my_new_task import run_my_new_task  # Import your task logic

    now_tz = _now_local()
    print(f"\n{'🔧'*35}")
    print(f"🔧 My New Task - Scheduled run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"{'🔧'*35}\n")

    try:
        result = await run_my_new_task(self.cosmos)
        print(f"My New Task complete: {result.get('success', 0)} success, "
              f"{result.get('errors', 0)} errors")
    except Exception as e:
        print(f"ERROR during My New Task: {e}")
```

### 4. Register the Task

**File:** `src/main.py` — in the `run()` method, after existing `registry.register()` calls:

```python
self.registry.register(
    "my_new_task",           # name (used internally, must be unique)
    "My New Task",           # display_name (shown in schedule output)
    "my_new_task",           # config_key (matches config.yaml section name)
    "0 12 * * 1-5",          # default_cron (fallback if not in config)
    self.run_my_new_task_job,  # job_func (the method to execute)
)
```

**That's it!** The registry now handles:
- Cron initialization
- Next-run calculation
- Config reload from CosmosDB
- Cron change detection
- Execution when due
- Error isolation
- Schedule display

### 5. Add Reschedule Method (For Web UI)

**File:** `src/main.py` — after existing `reschedule_X()` methods:

```python
def reschedule_my_new_task(self, new_cron: str):
    """Update my_new_task cron expression."""
    self.registry.reschedule("my_new_task", new_cron, self.config)
```

This preserves web UI compatibility — the web layer can call `scheduler.reschedule_my_new_task("0 14 * * *")` to live-update the cron.

---

## Validation Checklist

After implementing, verify:

1. ✅ Config entry exists in `config.yaml`
2. ✅ Import test: `python3 -c "from src import main"`
3. ✅ Task registered: Check `run()` method for `registry.register("my_new_task", ...)`
4. ✅ Reschedule method exists: `grep "def reschedule_my_new_task" src/main.py`
5. ✅ Job method exists: `grep "def run_my_new_task_job" src/main.py`

**Optional (if web UI integration needed):**
6. Update `web/app.py` to add UI controls for the new task
7. Update `web/templates/settings_config.html` with task-specific settings section

---

## Example: Portfolio Enrichment (Real Implementation)

```python
# In run() method:
self.registry.register(
    "portfolio_enrichment",
    "Portfolio Enrichment",
    "portfolio_enrichment",
    "0 9-17 * * 1-5",
    self.run_portfolio_enrichment_job,
)

# Reschedule method:
def reschedule_portfolio_enrichment(self, new_cron: str):
    """Update portfolio enrichment cron expression."""
    self.registry.reschedule("portfolio_enrichment", new_cron, self.config)
```

**Result:** Task runs hourly during market hours (9 AM - 5 PM weekdays), reschedule-able via web UI.

---

## Migration Notes (From Old 11-Point Pattern)

**OBSOLETE (pre-2026-06-26):** The old pattern required 11 separate edits across ~50 lines:
- ❌ `_X_cron_changed` flag in `__init__()`
- ❌ Manual cron initialization in `run()`
- ❌ Manual config reload logic in `_reload_config_from_cosmos()`
- ❌ Manual cron change handler in main loop
- ❌ Manual execution if-block in main loop
- ❌ Manual schedule display
- ❌ Hardcoded reschedule method implementation

**NEW (registry-based):** 2 integration points:
- ✅ `registry.register(...)` — 1 line
- ✅ `def reschedule_X(self, new_cron): self.registry.reschedule(...)` — 2 lines

**Net reduction:** ~50 lines → 3 lines per task (94% less boilerplate).

---

## Common Pitfalls

1. **Forgetting `_run_async()` wrapper** — async tasks need the sync→async bridge (same as before).
2. **Missing enabled check** — always check `config.get('enabled', True)` in job method (same as before).
3. **Mismatched config_key** — the `config_key` in `registry.register()` must match the section name in `config.yaml`.
4. **Forgetting reschedule method** — without it, the web UI can't live-update the cron (causes runtime error in web layer).
5. **Duplicate task names** — task names must be unique within the registry.

---

## Architecture Notes

**Why keep the sync→async wrapper pattern?**
- The scheduler loop is synchronous (while-loop with `time.sleep(1)`).
- Most tasks (agent runs, fetchers, etc.) are async.
- `_run_async(coro)` provides the bridge with proper cleanup to avoid "Event loop is closed" errors in Python 3.12+.

**Why separate reschedule methods instead of a generic `reschedule(task_name, new_cron)`?**
- Web UI backwards compatibility — existing `/api/settings/config/save` endpoint calls specific methods like `scheduler.reschedule_summary(...)`.
- Explicit API surface — each task's reschedule method is self-documenting.
- Could be refactored to generic in future if web UI is updated.

---

**Last Updated:** 2026-06-26 by Rusty  
**See Also:**
- `.squad/decisions/inbox/rusty-scheduler-registry-refactor.md` — full refactor rationale
- `.squad/agents/rusty/history.md` — registry design patterns and learnings
- `src/scheduler_registry.py` — registry implementation reference


---

## UI Auto-Provisioning (Since 2026-06-26)

**Every task registered in the registry automatically gets 5 standard UI controls** in the scheduler settings page:

1. ✅ **Enabled checkbox** — toggle on/off, persists to CosmosDB
2. ✅ **Cron expression field** — editable, live-reschedule
3. ✅ **Last run timestamp** — auto-tracked when task executes
4. ✅ **Next run timestamp** — computed from cron
5. ✅ **Run Now button** — manual trigger

**No additional code needed** — the unified endpoints (`/api/scheduler/tasks`, `/api/scheduler/tasks/{name}/run`, etc.) handle all tasks uniformly.

### Per-Task Extra Config

If your task needs **task-specific config beyond the 5 standard fields** (e.g., symbol list, threshold, max items):

1. Set `has_extra_config=True` in `registry.register()`:
   ```python
   self.registry.register(
       "my_task",
       "My Task",
       "my_task",
       "0 12 * * *",
       self.run_my_task_job,
       has_extra_config=True,  # <-- indicates extra config
   )
   ```

2. Add your extra config fields to the template (e.g., `web/templates/settings_config.html`) within the task's section:
   ```html
   <div>
       <label>Max Items</label>
       <input type="number" name="my_task_max_items" value="{{ my_task_max_items }}">
   </div>
   ```

3. Extract extra config in `_build_settings_config_context()` (web/app.py):
   ```python
   my_task_cfg = config.get("my_task", {})
   my_task_max_items = my_task_cfg.get("max_items", 10)
   ```

**Result:** Your task has the 5 standard controls + your custom extra fields.

---

**See also:**
- `.squad/decisions/inbox/rusty-scheduler-settings-unify.md` — unified UI model design
- `.squad/agents/rusty/history.md` — registry metadata extensions
- `src/scheduler_registry.py` — registry implementation
- `web/app.py:3911-4038` — unified endpoints reference
