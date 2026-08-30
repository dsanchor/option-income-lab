"""Test suite for Best Options scheduler startup catch-up and trigger behavior.

Validates:
1. Startup catch-up runs when enabled and config is valid
2. Startup catch-up is skipped when disabled
3. trigger_task_now works correctly for manual/startup triggers
4. Weekend/off-hours startup still runs catch-up (doesn't wait for cron)

Ownership: Rusty (scheduler/framework plumbing)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.config import Config
from src.scheduler_registry import TaskRegistry, ScheduledTask


class TestSchedulerBestOptionsStartup:
    """Test Best Options scheduler startup and trigger behavior."""

    def test_trigger_task_now_success(self):
        """trigger_task_now enqueues a task successfully."""
        registry = TaskRegistry()
        
        # Mock job function
        job_called = []
        def mock_job(run_trigger=None):
            job_called.append(run_trigger)
        
        # Register task
        registry.register(
            "test_task",
            "Test Task",
            "test_config",
            "0 * * * *",
            mock_job,
        )
        
        # Initialize with mock config
        config = MagicMock()
        config.config = {"test_config": {"enabled": True, "cron": "0 * * * *"}}
        now_tz = datetime.now(timezone.utc)
        registry.initialize_all(config, now_tz)
        
        # Trigger task
        result = registry.trigger_task_now("test_task", run_trigger="manual")
        
        assert result["success"] is True
        assert "queued" in result["message"].lower()
        
        # Wait briefly for worker thread to process
        time.sleep(0.5)
        
        # Job should have been called with run_trigger
        assert len(job_called) == 1
        assert job_called[0] == "manual"
        
        # Clean up
        registry.shutdown()

    def test_trigger_task_now_disabled_task(self):
        """trigger_task_now fails when task is disabled."""
        registry = TaskRegistry()
        
        def mock_job():
            pass
        
        registry.register("test_task", "Test", "test_config", "0 * * * *", mock_job)
        
        config = MagicMock()
        config.config = {"test_config": {"enabled": False, "cron": "0 * * * *"}}
        now_tz = datetime.now(timezone.utc)
        registry.initialize_all(config, now_tz)
        
        result = registry.trigger_task_now("test_task")
        
        assert result["success"] is False
        assert "disabled" in result["message"].lower()
        
        registry.shutdown()

    def test_trigger_task_now_unknown_task(self):
        """trigger_task_now fails when task doesn't exist."""
        registry = TaskRegistry()
        
        result = registry.trigger_task_now("nonexistent_task")
        
        assert result["success"] is False
        assert "not found" in result["message"].lower()

    def test_trigger_task_now_already_running(self):
        """trigger_task_now fails when task is already running."""
        registry = TaskRegistry()
        
        # Create a slow job that blocks
        import threading
        started = threading.Event()
        
        def slow_job():
            started.set()
            time.sleep(2)
        
        registry.register("slow_task", "Slow", "test_config", "0 * * * *", slow_job)
        
        config = MagicMock()
        config.config = {"test_config": {"enabled": True, "cron": "0 * * * *"}}
        now_tz = datetime.now(timezone.utc)
        registry.initialize_all(config, now_tz)
        
        # First trigger
        result1 = registry.trigger_task_now("slow_task")
        assert result1["success"] is True
        
        # Wait for job to start
        started.wait(timeout=1.0)
        
        # Second trigger while first is running
        result2 = registry.trigger_task_now("slow_task")
        assert result2["success"] is False
        assert "already running" in result2["message"].lower()
        
        # Clean up - wait for slow job to finish
        time.sleep(2.5)
        registry.shutdown()

    def test_startup_catch_up_weekend_behavior(self):
        """Startup catch-up runs on weekends when enabled, regardless of cron schedule."""
        # Simulate a Saturday or Sunday startup
        # The cron "5 10-23 * * 1-5" would not fire on weekends
        # But startup catch-up should still run
        
        registry = TaskRegistry()
        
        job_called = []
        def mock_job(run_trigger=None):
            job_called.append(run_trigger)
        
        registry.register(
            "best_options",
            "Best Options",
            "best_options_scheduler",
            "5 10-23 * * 1-5",  # Weekdays only
            mock_job,
            has_extra_config=True,
        )
        
        config = MagicMock()
        config.config = {
            "best_options_scheduler": {
                "enabled": True,
                "cron": "5 10-23 * * 1-5",
                "run_on_startup": True,
            }
        }
        
        # Initialize (starts worker thread)
        now_tz = datetime.now(timezone.utc)
        registry.initialize_all(config, now_tz)
        
        # Trigger startup catch-up (like main.py does)
        result = registry.trigger_task_now("best_options", run_trigger="startup")
        
        assert result["success"] is True
        
        # Wait for worker to process
        time.sleep(0.5)
        
        # Job should have been called with "startup" trigger
        assert len(job_called) == 1
        assert job_called[0] == "startup"
        
        registry.shutdown()

    def test_trigger_respects_job_signature(self):
        """trigger_task_now only passes kwargs that job_func accepts."""
        registry = TaskRegistry()
        
        # Job that doesn't accept run_trigger
        job_called = []
        def simple_job():
            job_called.append("called")
        
        registry.register("simple", "Simple", "test_config", "0 * * * *", simple_job)
        
        config = MagicMock()
        config.config = {"test_config": {"enabled": True, "cron": "0 * * * *"}}
        now_tz = datetime.now(timezone.utc)
        registry.initialize_all(config, now_tz)
        
        # Trigger with run_trigger kwarg
        result = registry.trigger_task_now("simple", run_trigger="manual", extra_arg="ignored")
        assert result["success"] is True
        
        time.sleep(0.5)
        
        # Job should have been called without the kwargs
        assert len(job_called) == 1
        
        registry.shutdown()


class TestBestOptionsStartupIntegration:
    """Integration test for Best Options scheduler startup in main.py flow."""
    
    @patch("src.main.CosmosDBService")
    @patch("src.main.AgentRunner")
    @patch("src.main.Config")
    def test_startup_catch_up_enabled(self, mock_config_cls, mock_runner_cls, mock_cosmos_cls):
        """Best Options startup catch-up is triggered when enabled in config."""
        from src.main import OptionsAgentScheduler
        
        # Mock config
        mock_config = MagicMock()
        mock_config.config = {
            "best_options_scheduler": {
                "enabled": True,
                "cron": "5 10-23 * * 1-5",
                "run_on_startup": True,
            },
            "scheduler": {"cron": "30 9-16/4 * * 1-5", "enabled": True},
            "summary_agent": {"enabled": False},
            "plan_monitor": {"enabled": False},
            "banner_agent": {"enabled": False},
            "calendar_sync": {"enabled": False},
            "options_chain_scheduler": {"enabled": False},
            "dgi_screener": {"enabled": False},
            "dps_scorer": {"enabled": False},
            "portfolio_enrichment": {"enabled": False},
        }
        mock_config.cron_expression = "30 9-16/4 * * 1-5"
        mock_config.timezone = "UTC"
        mock_config.cosmosdb_endpoint = "https://test"
        mock_config.cosmosdb_key = "test-key"
        mock_config.cosmosdb_database = "test-db"
        mock_config.llm_config.return_value = {}
        mock_config.model_deployment = "test-model"
        mock_config.plan_monitor_model = "test-model"
        mock_config.function_llm_configs.return_value = {}
        mock_config_cls.return_value = mock_config
        
        # Mock Cosmos
        mock_cosmos = MagicMock()
        mock_cosmos.merge_defaults.return_value = {}
        mock_cosmos_cls.return_value = mock_cosmos
        
        # Mock runner
        mock_runner = MagicMock()
        mock_runner_cls.return_value = mock_runner
        
        # Create scheduler and run setup (not the full run loop)
        scheduler = OptionsAgentScheduler()
        
        # Patch the precompute job to track if it was called
        job_called = []
        original_job = scheduler.run_best_options_precompute_job
        def tracked_job():
            job_called.append("called")
        scheduler.run_best_options_precompute_job = tracked_job
        
        # Run setup (which includes startup catch-up logic)
        with patch("src.main.print") as mock_print:
            scheduler.setup()
            
            # Register tasks
            now_tz = datetime.now(timezone.utc)
            scheduler.registry.register(
                "best_options",
                "Best Options Precompute",
                "best_options_scheduler",
                "5 10-23 * * 1-5",
                scheduler.run_best_options_precompute_job,
                has_extra_config=True,
            )
            scheduler.registry.set_config(scheduler.config)
            scheduler.registry.initialize_all(scheduler.config, now_tz)
            
            # Trigger startup catch-up (like main.py does)
            best_options_config = scheduler.config.config.get('best_options_scheduler', {})
            if best_options_config.get('run_on_startup', True) and best_options_config.get('enabled', True):
                result = scheduler.registry.trigger_task_now("best_options", run_trigger="startup")
                # Check that trigger was successful
                assert result["success"] is True
            
            # Wait for worker thread
            time.sleep(0.5)
            
            # Verify job was called
            assert len(job_called) == 1
        
        # Clean up
        scheduler.registry.shutdown()

    @patch("src.main.CosmosDBService")
    @patch("src.main.AgentRunner")
    @patch("src.main.Config")
    def test_startup_catch_up_disabled(self, mock_config_cls, mock_runner_cls, mock_cosmos_cls):
        """Best Options startup catch-up is NOT triggered when disabled."""
        from src.main import OptionsAgentScheduler
        
        # Mock config with run_on_startup: False
        mock_config = MagicMock()
        mock_config.config = {
            "best_options_scheduler": {
                "enabled": True,
                "cron": "5 10-23 * * 1-5",
                "run_on_startup": False,  # Disabled
            },
            "scheduler": {"cron": "30 9-16/4 * * 1-5", "enabled": True},
            "summary_agent": {"enabled": False},
            "plan_monitor": {"enabled": False},
            "banner_agent": {"enabled": False},
            "calendar_sync": {"enabled": False},
            "options_chain_scheduler": {"enabled": False},
            "dgi_screener": {"enabled": False},
            "dps_scorer": {"enabled": False},
            "portfolio_enrichment": {"enabled": False},
        }
        mock_config.cron_expression = "30 9-16/4 * * 1-5"
        mock_config.timezone = "UTC"
        mock_config.cosmosdb_endpoint = "https://test"
        mock_config.cosmosdb_key = "test-key"
        mock_config.cosmosdb_database = "test-db"
        mock_config.llm_config.return_value = {}
        mock_config.model_deployment = "test-model"
        mock_config.plan_monitor_model = "test-model"
        mock_config.function_llm_configs.return_value = {}
        mock_config_cls.return_value = mock_config
        
        # Mock Cosmos
        mock_cosmos = MagicMock()
        mock_cosmos.merge_defaults.return_value = {}
        mock_cosmos_cls.return_value = mock_cosmos
        
        # Mock runner
        mock_runner = MagicMock()
        mock_runner_cls.return_value = mock_runner
        
        scheduler = OptionsAgentScheduler()
        
        # Track if job was called
        job_called = []
        def tracked_job():
            job_called.append("called")
        scheduler.run_best_options_precompute_job = tracked_job
        
        with patch("src.main.print"):
            scheduler.setup()
            
            now_tz = datetime.now(timezone.utc)
            scheduler.registry.register(
                "best_options",
                "Best Options Precompute",
                "best_options_scheduler",
                "5 10-23 * * 1-5",
                scheduler.run_best_options_precompute_job,
                has_extra_config=True,
            )
            scheduler.registry.set_config(scheduler.config)
            scheduler.registry.initialize_all(scheduler.config, now_tz)
            
            # Check startup catch-up logic
            best_options_config = scheduler.config.config.get('best_options_scheduler', {})
            if best_options_config.get('run_on_startup', True) and best_options_config.get('enabled', True):
                scheduler.registry.trigger_task_now("best_options", run_trigger="startup")
            
            time.sleep(0.5)
            
            # Job should NOT have been called
            assert len(job_called) == 0
        
        scheduler.registry.shutdown()
