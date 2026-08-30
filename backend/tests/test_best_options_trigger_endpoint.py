"""Test suite for Best Options trigger endpoint and Settings Run Now flow.

Validates:
1. POST /api/trigger/best_options works correctly
2. Frontend Settings "Run Now" button flow
3. Error handling when scheduler unavailable

Ownership: Rusty (frontend/BFF plumbing)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from web.app import app


class TestBestOptionsTriggerEndpoint:
    """Test POST /api/trigger/best_options endpoint."""

    def test_trigger_success(self):
        """Trigger endpoint succeeds when scheduler is available and task is enabled."""
        client = TestClient(app)
        
        # Mock scheduler with registry
        mock_scheduler = MagicMock()
        mock_task = MagicMock()
        mock_task.name = "best_options"
        mock_registry = MagicMock()
        mock_registry.get_task.return_value = mock_task
        mock_registry.trigger_task_now.return_value = {
            "success": True,
            "message": "Best Options precompute queued for execution"
        }
        mock_scheduler.registry = mock_registry
        
        # Inject mock scheduler into app state
        app.state.scheduler = mock_scheduler
        
        # Make request
        response = client.post("/api/trigger/best_options")
        
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "queued" in data["message"].lower() or "triggered" in data["message"].lower()
        
        # Verify trigger_task_now was called with correct args
        mock_registry.trigger_task_now.assert_called_once_with("best_options", trigger="manual")

    def test_trigger_scheduler_unavailable(self):
        """Trigger endpoint returns 503 when scheduler is unavailable."""
        client = TestClient(app)
        
        # Remove scheduler from app state
        if hasattr(app.state, "scheduler"):
            delattr(app.state, "scheduler")
        
        response = client.post("/api/trigger/best_options")
        
        assert response.status_code == 503
        data = response.json()
        assert "error" in data
        assert "scheduler" in data["error"].lower()

    def test_trigger_task_not_registered(self):
        """Trigger endpoint returns 404 when task is not registered."""
        client = TestClient(app)
        
        # Mock scheduler with registry that returns None
        mock_scheduler = MagicMock()
        mock_registry = MagicMock()
        mock_registry.get_task.return_value = None  # Task not found
        mock_scheduler.registry = mock_registry
        
        app.state.scheduler = mock_scheduler
        
        response = client.post("/api/trigger/best_options")
        
        assert response.status_code == 404
        data = response.json()
        assert "error" in data
        assert "not registered" in data["error"].lower()

    def test_trigger_task_disabled(self):
        """Trigger endpoint returns 400 when task is disabled."""
        client = TestClient(app)
        
        # Mock scheduler with registry that returns disabled result
        mock_scheduler = MagicMock()
        mock_task = MagicMock()
        mock_registry = MagicMock()
        mock_registry.get_task.return_value = mock_task
        mock_registry.trigger_task_now.return_value = {
            "success": False,
            "message": "Task 'Best Options Precompute' is disabled"
        }
        mock_scheduler.registry = mock_registry
        
        app.state.scheduler = mock_scheduler
        
        response = client.post("/api/trigger/best_options")
        
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "disabled" in data["error"].lower()

    def test_trigger_task_already_running(self):
        """Trigger endpoint returns 400 when task is already running."""
        client = TestClient(app)
        
        # Mock scheduler with registry that returns already-running result
        mock_scheduler = MagicMock()
        mock_task = MagicMock()
        mock_registry = MagicMock()
        mock_registry.get_task.return_value = mock_task
        mock_registry.trigger_task_now.return_value = {
            "success": False,
            "message": "Task 'Best Options Precompute' is already running"
        }
        mock_scheduler.registry = mock_registry
        
        app.state.scheduler = mock_scheduler
        
        response = client.post("/api/trigger/best_options")
        
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "already running" in data["error"].lower()


class TestBestOptionsTriggerVsRefresh:
    """Test the difference between /trigger and /refresh endpoints."""
    
    def test_trigger_runs_full_cycle(self):
        """POST /api/trigger/best_options triggers full precompute cycle."""
        client = TestClient(app)
        
        mock_scheduler = MagicMock()
        mock_task = MagicMock()
        mock_registry = MagicMock()
        mock_registry.get_task.return_value = mock_task
        mock_registry.trigger_task_now.return_value = {
            "success": True,
            "message": "Best Options precompute queued"
        }
        mock_scheduler.registry = mock_registry
        app.state.scheduler = mock_scheduler
        
        response = client.post("/api/trigger/best_options")
        
        assert response.status_code == 200
        # Verify trigger_task_now was called (not a symbol-specific refresh)
        mock_registry.trigger_task_now.assert_called_once()
        call_args = mock_registry.trigger_task_now.call_args
        assert call_args[0][0] == "best_options"
        assert call_args[1].get("trigger") == "manual"
    
    def test_refresh_is_symbol_specific(self):
        """POST /api/symbols/{symbol}/best-options/refresh is symbol-specific."""
        # This test just documents the difference - the refresh endpoint
        # is tested in test_best_options_endpoint.py
        # Key difference:
        # - /trigger/best_options: full cycle for all symbols
        # - /symbols/{symbol}/best-options/refresh: single symbol only
        pass
