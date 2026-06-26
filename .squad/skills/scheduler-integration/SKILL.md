# Skill: Scheduler Task Integration

**Context:** Adding new scheduled tasks to the Options Agent Scheduler (src/main.py)  
**Pattern:** croniter-based task scheduling with dynamic config reload  
**When to use:** Any time a new periodic job needs to be added to the system

---

## The Pattern

The scheduler uses a **croniter-based** polling loop (1-second interval) with per-task error isolation. Each task has:
- A cron expression (from config.yaml or CosmosDB override)
- An enabled/disabled flag
- A job function (sync wrapper → async implementation)
- A reschedule API for web UI updates

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

### 2. Add Config Accessor (Optional)

**File:** `src/config.py`

```python
@property
def my_new_task_enabled(self) -> bool:
    return bool(self.config.get('my_new_task', {}).get('enabled', True))

@property
def my_new_task_cron(self) -> str:
    return str(self.config.get('my_new_task', {}).get('cron', '0 12 * * 1-5'))
```

### 3. Add Cron Changed Flag

**File:** `src/main.py`

In `OptionsAgentScheduler.__init__()` (around line 75):

```python
self._my_new_task_cron_changed = False
```

### 4. Add Reschedule Method

**File:** `src/main.py`

After existing `reschedule_*` methods (around line 135):

```python
def reschedule_my_new_task(self, new_cron: str):
    """Update my_new_task cron expression."""
    task_config = self.config.config.get('my_new_task', {})
    task_config['cron'] = new_cron
    self.config.config['my_new_task'] = task_config
    self._my_new_task_cron_changed = True
```

### 5. Add Config Logging in Setup

**File:** `src/main.py`

In `setup()` method (around line 200-280):

```python
task_config = self.config.config.get('my_new_task', {})
task_enabled = task_config.get('enabled', True)
task_cron = task_config.get('cron', '0 12 * * 1-5')

print(f"\nMy New Task Configuration:")
print(f"  Enabled: {task_enabled}")
if task_enabled:
    print(f"  Cron: {task_cron}")
else:
    print(f"  Status: Disabled in config")
```

### 6. Add Job Methods

**File:** `src/main.py`

After existing `run_*_job()` methods (around line 434-503):

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

### 7. Add Config Reload Logic

**File:** `src/main.py`

In `_reload_config_from_cosmos()` method (around line 719-809):

```python
# Check my_new_task settings
task_settings = cosmos_settings.get('my_new_task', {})
new_task_cron = task_settings.get('cron')
current_task_cron = self.config.config.get('my_new_task', {}).get('cron', '0 12 * * 1-5')

task_cron_changed = False
if new_task_cron and new_task_cron != current_task_cron:
    if 'my_new_task' not in self.config.config:
        self.config.config['my_new_task'] = {}
    self.config.config['my_new_task']['cron'] = new_task_cron
    task_cron_changed = True

if task_settings:
    if 'my_new_task' not in self.config.config:
        self.config.config['my_new_task'] = {}
    for key in ['enabled']:  # Add any other dynamic config keys
        if key in task_settings:
            self.config.config['my_new_task'][key] = task_settings[key]

if task_cron_changed:
    self._my_new_task_cron_changed = True
    print(f"✓ Config reloaded from CosmosDB: my_new_task cron changed to {new_task_cron}")
```

### 8. Add Cron Initialization

**File:** `src/main.py`

In `run()` method (around line 876-892):

```python
# Initialize my_new_task cron (if enabled)
task_config = self.config.config.get('my_new_task', {})
task_enabled = task_config.get('enabled', True)
task_cron_expr = task_config.get('cron', '0 12 * * 1-5')
task_next_run = None
task_cron = None
```

Then in the initialization block (around line 868-892):

```python
if task_enabled:
    try:
        task_cron = croniter(task_cron_expr, now_tz)
        task_next_run = task_cron.get_next(datetime)
    except (ValueError, KeyError) as e:
        print(f"⚠️  Invalid my_new_task cron expression '{task_cron_expr}': {e}")
        print(f"⚠️  My New Task scheduling disabled")
        task_enabled = False
```

### 9. Add Initial Schedule Display

**File:** `src/main.py`

In the initial schedule display block (around line 878-907):

```python
if task_enabled and task_next_run:
    print(f"My New Task           - Next run: {task_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")
else:
    print(f"My New Task           - Disabled")
```

### 10. Add Cron Change Handler

**File:** `src/main.py`

In the main loop's cron change detection block (around line 1034-1047):

```python
if self._my_new_task_cron_changed:
    self._my_new_task_cron_changed = False
    task_config = self.config.config.get('my_new_task', {})
    task_cron_expr = task_config.get('cron', '0 12 * * 1-5')
    try:
        now_tz = _now_local()
        task_cron = croniter(task_cron_expr, now_tz)
        task_next_run = task_cron.get_next(datetime)
        task_enabled = task_config.get('enabled', True)
        print(f"My New Task cron rescheduled to: {task_cron_expr}")
        print(f"Next scheduled run: {task_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
    except (ValueError, KeyError) as e:
        print(f"⚠️  Invalid my_new_task cron expression '{task_cron_expr}': {e}")
        task_enabled = False
```

### 11. Add Execution Block

**File:** `src/main.py`

In the main loop, before the `time.sleep(1)` line (around line 1113-1122):

```python
# Check my_new_task scheduler
if task_enabled and task_next_run and now_tz >= task_next_run:
    try:
        self.run_my_new_task_job()
    except Exception as e:
        print(f"❌ SCHEDULER ERROR in my_new_task: {e}")
    task_next_run = task_cron.get_next(datetime)
    print(f"My New Task           - Next run: {task_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
```

---

## Validation Checklist

After implementing, verify:

1. ✅ Config entry exists in `config.yaml`
2. ✅ Import test: `python3 -c "from src import main"`
3. ✅ Method exists: `grep "def run_my_new_task_job" src/main.py`
4. ✅ All 11 integration points present:
   - Config flag
   - Reschedule method
   - Setup logging
   - Job methods
   - Config reload
   - Cron initialization
   - Display
   - Change handler
   - Execution block

---

## Common Pitfalls

1. **Forgetting `_run_async()` wrapper** — async tasks need the sync→async bridge
2. **Missing enabled check** — always check `config.get('enabled', True)` in job method
3. **Wrong cron initialization order** — must happen after config load in `run()`
4. **Hardcoded default cron** — use same default in 3 places: config.yaml, config accessor, initialization
5. **Missing error isolation** — always wrap execution in try/except

---

## Example: DPS Scorer Integration (Real Implementation)

See commit `2026-06-26: Add DPS scheduler integration` for a complete working example.

**Files:**
- `src/main.py` — 9 edits, 60 lines
- `config.yaml` — already had `dps_scorer.cron` entry
- `src/dps_cron.py` — existing task logic (no changes needed)

**Result:** DPS now runs nightly at 10 PM, calculating position risk scores.

---

**Last Updated:** 2026-06-26 by Rusty
