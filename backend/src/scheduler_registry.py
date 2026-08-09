"""Centralized task registry for the Options Agent Scheduler.

Replaces per-task boilerplate with a unified registry-based approach.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional
from croniter import croniter
import logging
import queue
import threading

logger = logging.getLogger(__name__)

# Maximum duration for any single task execution (30 minutes)
_MAX_TASK_DURATION_SECONDS = 1800


@dataclass
class ScheduledTask:
    """Represents a single scheduled task."""
    
    name: str                          # Task identifier (e.g., "monitor_agents")
    display_name: str                  # Human-readable label for display
    config_key: str                    # Key in config.yaml (e.g., "scheduler")
    default_cron: str                  # Default cron expression
    job_func: Callable                 # Function to execute (already bound)
    enabled: bool = True               # Whether task is enabled
    cron_expr: Optional[str] = None    # Current cron expression
    next_run: Optional[datetime] = None
    cron_obj: Optional[croniter] = None
    _cron_changed: bool = False        # Flag for web UI live reschedule
    last_run: Optional[datetime] = None  # Last execution timestamp
    has_extra_config: bool = False     # Whether task has task-specific config beyond the 5 standard fields
    running: bool = False              # True if a job execution is in progress


class TaskRegistry:
    """Centralized registry for all scheduled tasks.
    
    Handles:
    - Task registration and initialization
    - Cron parsing and next-run calculation
    - Config reload and cron change detection
    - Task execution with error isolation
    - Non-blocking job execution via worker thread
    """
    
    def __init__(self):
        self.tasks: dict[str, ScheduledTask] = {}
        self._job_queue: queue.Queue = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._shutdown = False
    
    def register(
        self,
        name: str,
        display_name: str,
        config_key: str,
        default_cron: str,
        job_func: Callable,
        has_extra_config: bool = False,
    ) -> None:
        """Register a new task."""
        task = ScheduledTask(
            name=name,
            display_name=display_name,
            config_key=config_key,
            default_cron=default_cron,
            job_func=job_func,
            has_extra_config=has_extra_config,
        )
        self.tasks[name] = task
    
    def initialize_task(self, task: ScheduledTask, config, now_tz: datetime) -> bool:
        """Initialize a task's cron schedule. Returns True if successful."""
        task_config = config.config.get(task.config_key, {})
        task.enabled = task_config.get('enabled', True)
        
        # Special case: monitor_agents uses config.cron_expression instead of config.config['scheduler']['cron']
        if task.name == "monitor_agents":
            task.cron_expr = config.cron_expression
        else:
            task.cron_expr = task_config.get('cron', task.default_cron)
        
        if not task.enabled:
            return False
        
        try:
            task.cron_obj = croniter(task.cron_expr, now_tz)
            task.next_run = task.cron_obj.get_next(datetime)
            return True
        except (ValueError, KeyError) as e:
            logger.warning(
                "Invalid cron expression '%s' for %s: %s",
                task.cron_expr, task.name, e
            )
            print(f"⚠️  Invalid {task.display_name} cron expression '{task.cron_expr}': {e}")
            print(f"⚠️  {task.display_name} scheduling disabled")
            task.enabled = False
            return False
    
    def initialize_all(self, config, now_tz: datetime) -> None:
        """Initialize all registered tasks and start the worker thread."""
        for task in self.tasks.values():
            self.initialize_task(task, config, now_tz)
        
        # Start the worker thread if not already running
        if not self._worker_thread or not self._worker_thread.is_alive():
            self._shutdown = False
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                daemon=True,
                name="TaskRegistryWorker"
            )
            self._worker_thread.start()
    
    def display_schedule(self) -> None:
        """Print the schedule for all tasks."""
        for task in self.tasks.values():
            if task.enabled and task.next_run:
                print(f"{task.display_name:<22} - Next run: {task.next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            else:
                print(f"{task.display_name:<22} - Disabled")
    
    def reschedule(self, name: str, new_cron: str, config) -> None:
        """Update a task's cron expression (called by web UI)."""
        task = self.tasks.get(name)
        if not task:
            logger.warning("Reschedule called for unknown task: %s", name)
            return
        
        task_config = config.config.get(task.config_key, {})
        task_config['cron'] = new_cron
        config.config[task.config_key] = task_config
        task._cron_changed = True
    
    def handle_cron_changes(self, now_tz: datetime) -> None:
        """Check all tasks for cron changes and reinitialize if needed."""
        for task in self.tasks.values():
            if task._cron_changed:
                task._cron_changed = False
                
                # Special case: monitor_agents uses config.cron_expression
                if task.name == "monitor_agents":
                    task.cron_expr = self._config.cron_expression
                else:
                    # Re-read cron expression from config
                    task_config = self._config.config.get(task.config_key, {})
                    task.cron_expr = task_config.get('cron', task.default_cron)
                
                try:
                    task.cron_obj = croniter(task.cron_expr, now_tz)
                    task.next_run = task.cron_obj.get_next(datetime)
                    
                    # Re-read enabled state
                    if task.name == "monitor_agents":
                        # monitor_agents doesn't have a separate enabled flag in config
                        task.enabled = True
                    else:
                        task_config = self._config.config.get(task.config_key, {})
                        task.enabled = task_config.get('enabled', True)
                    
                    print(f"{task.display_name} cron rescheduled to: {task.cron_expr}")
                    print(f"Next scheduled run: {task.next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
                except (ValueError, KeyError) as e:
                    print(f"⚠️  Invalid {task.display_name} cron expression '{task.cron_expr}': {e}")
                    task.enabled = False
    
    def reload_from_cosmos(self, config, cosmos_settings: dict) -> None:
        """Reload task settings from CosmosDB and detect changes."""
        for task in self.tasks.values():
            task_settings = cosmos_settings.get(task.config_key, {})
            if not task_settings:
                continue
            
            new_cron = task_settings.get('cron')
            current_cron = config.config.get(task.config_key, {}).get('cron', task.default_cron)
            
            cron_changed = False
            if new_cron and new_cron != current_cron:
                if task.config_key not in config.config:
                    config.config[task.config_key] = {}
                config.config[task.config_key]['cron'] = new_cron
                cron_changed = True
            
            # Update scheduling state.
            if task.config_key not in config.config:
                config.config[task.config_key] = {}
            for key in ['enabled']:
                if key in task_settings:
                    config.config[task.config_key][key] = task_settings[key]
                    # Immediately update task.enabled to reflect the change
                    if key == 'enabled':
                        task.enabled = task_settings[key]
            
            if cron_changed:
                task._cron_changed = True
                print(f"✓ Config reloaded from CosmosDB: {task.display_name} cron changed to {new_cron}")
    
    def _worker_loop(self) -> None:
        """Worker thread that executes jobs sequentially off the main loop."""
        while not self._shutdown:
            try:
                # Wait for a job (blocks with timeout so we can check shutdown flag)
                try:
                    task_name = self._job_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                
                task = self.tasks.get(task_name)
                if not task:
                    logger.warning(f"Worker received unknown task: {task_name}")
                    continue
                
                # Execute the job in a sub-thread with max-duration guard
                start_time = datetime.now().astimezone()
                
                def run_task():
                    """Job execution wrapper for sub-thread."""
                    try:
                        task.job_func()
                    except Exception as e:
                        print(f"❌ SCHEDULER ERROR in {task.name}: {e}")
                        logger.exception(f"Error executing task {task_name}")
                
                # Run job in a daemon sub-thread with timeout
                job_thread = threading.Thread(
                    target=run_task,
                    daemon=True,
                    name=f"TaskExec-{task_name}"
                )
                job_thread.start()
                job_thread.join(timeout=_MAX_TASK_DURATION_SECONDS)
                
                if job_thread.is_alive():
                    # Task exceeded max duration — abandon it (thread will linger but won't block queue)
                    logger.error(
                        f"Task {task_name} exceeded max duration of {_MAX_TASK_DURATION_SECONDS}s, abandoning"
                    )
                    print(f"❌ SCHEDULER TIMEOUT: {task.display_name} exceeded {_MAX_TASK_DURATION_SECONDS}s")
                
                # Record execution timestamp even on timeout/error
                task.last_run = start_time
                task.running = False
                    
            except Exception as e:
                logger.exception(f"Worker thread error: {e}")
    
    def execute_due_tasks(self, now_tz: datetime) -> None:
        """Detect due tasks, advance their next_run, and enqueue them for execution.
        
        This method runs on the main scheduler loop and must NOT block.
        Jobs are executed on the worker thread.
        """
        for task in self.tasks.values():
            if not task.enabled or not task.next_run:
                continue
            
            if now_tz >= task.next_run:
                # Overlap guard: skip if previous run still in progress
                if task.running:
                    logger.warning(f"Skipping {task.name}: previous run still in progress")
                    # Advance next_run anyway to prevent tight catch-up loop
                    task.next_run = self._advance_next_run(task, now_tz)
                    print(f"{task.display_name:<22} - Skipped (still running), next run: {task.next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
                    continue
                
                # Advance next_run BEFORE dispatching job
                task.next_run = self._advance_next_run(task, now_tz)
                print(f"{task.display_name:<22} - Next run: {task.next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")
                
                # Enqueue job for worker thread
                task.running = True
                self._job_queue.put(task.name)
    
    def _advance_next_run(self, task: ScheduledTask, now_tz: datetime) -> datetime:
        """Advance next_run to the next future occurrence.
        
        Guards against stale cron base: loops get_next until result is strictly
        in the future relative to now_tz.
        """
        next_run = task.cron_obj.get_next(datetime)
        
        # Guard against tight catch-up loops if cron base is stale
        # Loop until next_run is strictly in the future
        max_iterations = 100  # Safety limit
        iterations = 0
        while next_run <= now_tz and iterations < max_iterations:
            next_run = task.cron_obj.get_next(datetime)
            iterations += 1
        
        if iterations >= max_iterations:
            logger.warning(f"Task {task.name}: hit max iterations advancing next_run")
        
        return next_run
    
    def get_task(self, name: str) -> Optional[ScheduledTask]:
        """Get a task by name."""
        return self.tasks.get(name)
    
    def set_config(self, config):
        """Store config reference for handle_cron_changes."""
        self._config = config
    
    def trigger_task_now(self, name: str) -> dict:
        """Manually trigger a task execution (for Run Now button).
        
        Returns: {"success": bool, "message": str}
        """
        task = self.tasks.get(name)
        if not task:
            return {"success": False, "message": f"Task '{name}' not found"}
        
        if not task.enabled:
            return {"success": False, "message": f"Task '{task.display_name}' is disabled"}
        
        # Overlap guard: don't start manual run if task is already running
        if task.running:
            return {"success": False, "message": f"Task '{task.display_name}' is already running"}
        
        # Enqueue for worker thread
        task.running = True
        self._job_queue.put(task.name)
        return {"success": True, "message": f"{task.display_name} queued for execution"}
    
    def get_all_task_metadata(self) -> list[dict]:
        """Get metadata for all tasks (for unified web API).
        
        Returns list of dicts with: name, display_name, enabled, cron, last_run, next_run, has_extra_config
        """
        result = []
        for task in self.tasks.values():
            result.append({
                "name": task.name,
                "display_name": task.display_name,
                "config_key": task.config_key,
                "enabled": task.enabled,
                "cron": task.cron_expr or task.default_cron,
                "last_run": task.last_run.isoformat() if task.last_run else None,
                "next_run": task.next_run.isoformat() if task.next_run else None,
                "has_extra_config": task.has_extra_config,
            })
        return result
    
    def update_task_enabled(self, name: str, enabled: bool, config) -> bool:
        """Update a task's enabled state and persist to config.
        
        Returns: True if successful, False if task not found
        """
        task = self.tasks.get(name)
        if not task:
            logger.warning("update_task_enabled called for unknown task: %s", name)
            return False
        
        task_config = config.config.get(task.config_key, {})
        task_config['enabled'] = enabled
        config.config[task.config_key] = task_config
        task.enabled = enabled
        
        # If re-enabled, reinitialize cron
        if enabled and task.cron_expr:
            from datetime import datetime
            try:
                from croniter import croniter
                now_tz = datetime.now().astimezone()
                task.cron_obj = croniter(task.cron_expr, now_tz)
                task.next_run = task.cron_obj.get_next(datetime)
            except Exception as e:
                logger.warning("Failed to reinitialize cron for %s: %s", name, e)
        
        return True
    
    def shutdown(self) -> None:
        """Shutdown the worker thread cleanly."""
        self._shutdown = True
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5.0)
