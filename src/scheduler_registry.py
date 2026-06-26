"""Centralized task registry for the Options Agent Scheduler.

Replaces per-task boilerplate with a unified registry-based approach.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional
from croniter import croniter
import logging

logger = logging.getLogger(__name__)


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


class TaskRegistry:
    """Centralized registry for all scheduled tasks.
    
    Handles:
    - Task registration and initialization
    - Cron parsing and next-run calculation
    - Config reload and cron change detection
    - Task execution with error isolation
    """
    
    def __init__(self):
        self.tasks: dict[str, ScheduledTask] = {}
    
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
        """Initialize all registered tasks."""
        for task in self.tasks.values():
            self.initialize_task(task, config, now_tz)
    
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
                
                # Re-read cron expression from config
                task_config = getattr(self, '_config').config.get(task.config_key, {})
                task.cron_expr = task_config.get('cron', task.default_cron)
                
                try:
                    task.cron_obj = croniter(task.cron_expr, now_tz)
                    task.next_run = task.cron_obj.get_next(datetime)
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
            
            # Update other config keys (enabled, etc.)
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
    
    def execute_due_tasks(self, now_tz: datetime) -> None:
        """Execute any tasks that are due."""
        for task in self.tasks.values():
            if task.enabled and task.next_run and now_tz >= task.next_run:
                try:
                    task.job_func()
                    task.last_run = now_tz  # Record successful run timestamp
                except Exception as e:
                    print(f"❌ SCHEDULER ERROR in {task.name}: {e}")
                
                # Calculate next run
                task.next_run = task.cron_obj.get_next(datetime)
                print(f"{task.display_name:<22} - Next run: {task.next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
    
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
        
        try:
            from datetime import datetime
            now_tz = datetime.now().astimezone()
            task.job_func()
            task.last_run = now_tz  # Record manual run timestamp
            return {"success": True, "message": f"{task.display_name} executed successfully"}
        except Exception as e:
            logger.exception(f"Error triggering task {name}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
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
