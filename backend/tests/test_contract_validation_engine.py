"""Tests for run_contract_validation engine method.

Focused tests for the exact-contract validation engine covering:
- Side-to-agent-type mapping (call → covered_call, put → cash_secured_put)
- Skill reuse (category-params)
- Same-snapshot evidence passing
- Forced Alpha review requirement
- Run_id and trace lineage
- Fail-closed reviewer failures
- Invalid evidence handling
- No order side effects
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from src.agent_runner import AgentRunner


@pytest.fixture
def mock_cosmos():
    """Mock CosmosDBService."""
    cosmos = MagicMock()
    cosmos.get_symbols.return_value = []
    return cosmos


@pytest.fixture
def mock_context_provider():
    """Mock ContextProvider."""
    provider = MagicMock()
    provider.get_context.return_value = "No previous activities."
    return provider


@pytest.fixture
def runner():
    """Create AgentRunner instance with mocked LLM."""
    llm_config = {"provider": "azure", "api_key": "test", "endpoint": "test"}
    runner = AgentRunner(
        llm=llm_config,
        model="gpt-5.4-mini",
        telegram_notifier=None,
        plan_monitor_model="gpt-5.4-mini",
        function_llms={},
    )
    return runner


@pytest.fixture
def valid_call_evidence():
    """Valid evidence snapshot for a covered call."""
    return {
        "category": "balanced",
        "underlying_price": 150.00,
        "total_shares": 500,
        "contract_data": {
            "strike": 155.0,
            "bid": 2.50,
            "ask": 2.55,
            "delta": 0.25,
            "iv": 0.30,
            "open_interest": 1000,
        },
        "chain_timestamp": "2026-08-29T10:00:00Z",
        "next_earnings_date": None,
        "ex_dividend_date": None,
        "atm_iv": 0.28,
        "iv_rank": 45,
        "market_data_text": "Market data snapshot...",
    }


@pytest.fixture
def valid_put_evidence():
    """Valid evidence snapshot for a cash-secured put."""
    return {
        "category": "balanced",
        "underlying_price": 150.00,
        "contract_data": {
            "strike": 145.0,
            "bid": 2.00,
            "ask": 2.05,
            "delta": -0.30,
            "iv": 0.32,
            "open_interest": 800,
        },
        "chain_timestamp": "2026-08-29T10:00:00Z",
        "next_earnings_date": None,
        "ex_dividend_date": None,
        "atm_iv": 0.28,
        "iv_rank": 45,
        "market_data_text": "Market data snapshot...",
    }


class TestSideToAgentTypeMapping:
    """Test that side correctly maps to agent_type."""

    @pytest.mark.asyncio
    async def test_call_maps_to_covered_call(self, runner, mock_cosmos, mock_context_provider, valid_call_evidence):
        """Call side should map to covered_call agent_type."""
        with patch.object(runner, '_get_client') as mock_client:
            # Mock Agent.run to return valid JSON
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='```json\n{"activity": "WAIT", "timestamp": "2026-08-29T10:00:00Z"}\n```'
            ))
            
            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                with patch.object(runner, '_run_supervisor_review', return_value=None):
                    with patch.object(runner, '_record_trace', return_value="trace-123"):
                        result = await runner.run_contract_validation(
                            symbol="AAPL",
                            side="call",
                            strike=155.0,
                            expiration="2026-10-16",
                            evidence_snapshot=valid_call_evidence,
                            cosmos=mock_cosmos,
                            context_provider=mock_context_provider,
                        )
        
        assert result["agent_type"] == "covered_call"
        assert result["side"] == "call"

    @pytest.mark.asyncio
    async def test_put_maps_to_cash_secured_put(self, runner, mock_cosmos, mock_context_provider, valid_put_evidence):
        """Put side should map to cash_secured_put agent_type."""
        with patch.object(runner, '_get_client') as mock_client:
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='```json\n{"activity": "WAIT", "timestamp": "2026-08-29T10:00:00Z"}\n```'
            ))
            
            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                with patch.object(runner, '_run_supervisor_review', return_value=None):
                    with patch.object(runner, '_record_trace', return_value="trace-456"):
                        result = await runner.run_contract_validation(
                            symbol="MSFT",
                            side="put",
                            strike=145.0,
                            expiration="2026-10-16",
                            evidence_snapshot=valid_put_evidence,
                            cosmos=mock_cosmos,
                            context_provider=mock_context_provider,
                        )
        
        assert result["agent_type"] == "cash_secured_put"
        assert result["side"] == "put"

    def test_invalid_side_raises_error(self, runner, mock_cosmos, mock_context_provider, valid_call_evidence):
        """Invalid side should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid side"):
            import asyncio
            asyncio.run(runner.run_contract_validation(
                symbol="AAPL",
                side="invalid",
                strike=155.0,
                expiration="2026-10-16",
                evidence_snapshot=valid_call_evidence,
                cosmos=mock_cosmos,
                context_provider=mock_context_provider,
            ))


class TestEvidenceValidation:
    """Test evidence snapshot validation."""

    def test_missing_required_fields_raises_error(self, runner, mock_cosmos, mock_context_provider):
        """Missing required evidence fields should raise ValueError."""
        incomplete_evidence = {
            "category": "balanced",
            "underlying_price": 150.00,
            # Missing contract_data, market_data_text, chain_timestamp
        }
        
        with pytest.raises(ValueError, match="Missing required evidence fields"):
            import asyncio
            asyncio.run(runner.run_contract_validation(
                symbol="AAPL",
                side="call",
                strike=155.0,
                expiration="2026-10-16",
                evidence_snapshot=incomplete_evidence,
                cosmos=mock_cosmos,
                context_provider=mock_context_provider,
            ))

    def test_call_requires_total_shares(self, runner, mock_cosmos, mock_context_provider):
        """Call side requires total_shares in evidence."""
        evidence_without_shares = {
            "category": "balanced",
            "underlying_price": 150.00,
            # Missing total_shares for call
            "contract_data": {},
            "chain_timestamp": "2026-08-29T10:00:00Z",
            "market_data_text": "...",
        }
        
        with pytest.raises(ValueError, match="Missing required evidence fields"):
            import asyncio
            asyncio.run(runner.run_contract_validation(
                symbol="AAPL",
                side="call",
                strike=155.0,
                expiration="2026-10-16",
                evidence_snapshot=evidence_without_shares,
                cosmos=mock_cosmos,
                context_provider=mock_context_provider,
            ))


class TestFailClosedReviewLogic:
    """Test fail-closed logic for incomplete/failed reviews."""

    @pytest.mark.asyncio
    async def test_supervisor_failure_downgrades_to_wait(self, runner, mock_cosmos, mock_context_provider, valid_call_evidence):
        """When Supervisor fails, SELL should downgrade to WAIT."""
        with patch.object(runner, '_get_client'):
            mock_agent_instance = AsyncMock()
            # Primary returns SELL
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='```json\n{"activity": "SELL", "timestamp": "2026-08-29T10:00:00Z"}\n```'
            ))
            
            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                # Supervisor fails (returns None)
                with patch.object(runner, '_run_supervisor_review', return_value=None):
                    with patch.object(runner, '_record_trace', return_value="trace-789"):
                        with patch('src.agent_runner.build_rule_evaluation', return_value={}):
                            result = await runner.run_contract_validation(
                                symbol="AAPL",
                                side="call",
                                strike=155.0,
                                expiration="2026-10-16",
                                evidence_snapshot=valid_call_evidence,
                                cosmos=mock_cosmos,
                                context_provider=mock_context_provider,
                            )
        
        assert result["activity"] == "WAIT", "Should downgrade to WAIT when Supervisor fails"
        assert result["is_alert"] is False
        assert result["validation_status"] == "review_incomplete"
        assert "Supervisor review failed" in result["note"]

    @pytest.mark.asyncio
    async def test_alpha_failure_downgrades_to_wait(self, runner, mock_cosmos, mock_context_provider, valid_call_evidence):
        """When Alpha fails, SELL should downgrade to WAIT."""
        with patch.object(runner, '_get_client'):
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='```json\n{"activity": "SELL", "timestamp": "2026-08-29T10:00:00Z"}\n```'
            ))
            
            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                # Supervisor succeeds
                with patch.object(runner, '_run_supervisor_review', return_value={
                    "net_assessment": "APPROVE",
                    "challenge_strength": "WEAK",
                    "trace_id": "sup-123",
                }):
                    # Alpha fails (returns None)
                    with patch.object(runner, '_run_alpha_review', return_value=None):
                        with patch.object(runner, '_record_trace', return_value="trace-abc"):
                            with patch('src.agent_runner.build_rule_evaluation', return_value={}):
                                result = await runner.run_contract_validation(
                                    symbol="AAPL",
                                    side="call",
                                    strike=155.0,
                                    expiration="2026-10-16",
                                    evidence_snapshot=valid_call_evidence,
                                    cosmos=mock_cosmos,
                                    context_provider=mock_context_provider,
                                )
        
        assert result["activity"] == "WAIT", "Should downgrade to WAIT when Alpha fails"
        assert result["is_alert"] is False
        assert result["validation_status"] == "review_incomplete"
        assert "Alpha review failed" in result["note"]


class TestApprovedValidation:
    """Test successful validation with all reviews approving."""

    @pytest.mark.asyncio
    async def test_approved_sell_all_reviews_pass(self, runner, mock_cosmos, mock_context_provider, valid_call_evidence):
        """SELL with all reviews approving should result in approved validation."""
        with patch.object(runner, '_get_client'):
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='```json\n{"activity": "SELL", "timestamp": "2026-08-29T10:00:00Z"}\n```'
            ))
            
            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                with patch.object(runner, '_run_supervisor_review', return_value={
                    "net_assessment": "APPROVE",
                    "challenge_strength": "WEAK",
                    "trace_id": "sup-999",
                }):
                    with patch.object(runner, '_run_alpha_review', return_value={
                        "recommendation": "APPROVE",
                        "trace_id": "alpha-999",
                    }):
                        with patch.object(runner, '_record_trace', return_value="trace-primary"):
                            with patch('src.agent_runner.build_rule_evaluation', return_value={}):
                                result = await runner.run_contract_validation(
                                    symbol="AAPL",
                                    side="call",
                                    strike=155.0,
                                    expiration="2026-10-16",
                                    evidence_snapshot=valid_call_evidence,
                                    cosmos=mock_cosmos,
                                    context_provider=mock_context_provider,
                                )
        
        assert result["activity"] == "SELL"
        assert result["is_alert"] is True
        assert result["validation_status"] == "approved"
        assert result["supervisor_view"] is not None
        assert result["alpha_view"] is not None


class TestRunIdAndTraceLineage:
    """Test run_id minting and trace lineage."""

    @pytest.mark.asyncio
    async def test_mints_unique_run_id(self, runner, mock_cosmos, mock_context_provider, valid_call_evidence):
        """Should mint a unique run_id for each validation."""
        with patch.object(runner, '_get_client'):
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='```json\n{"activity": "WAIT", "timestamp": "2026-08-29T10:00:00Z"}\n```'
            ))
            
            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                with patch.object(runner, '_run_supervisor_review', return_value=None):
                    with patch.object(runner, '_record_trace', return_value="trace-1"):
                        with patch('src.agent_runner.build_rule_evaluation', return_value={}):
                            result1 = await runner.run_contract_validation(
                                symbol="AAPL",
                                side="call",
                                strike=155.0,
                                expiration="2026-10-16",
                                evidence_snapshot=valid_call_evidence,
                                cosmos=mock_cosmos,
                                context_provider=mock_context_provider,
                            )
                            
                            result2 = await runner.run_contract_validation(
                                symbol="MSFT",
                                side="call",
                                strike=300.0,
                                expiration="2026-10-16",
                                evidence_snapshot=valid_call_evidence,
                                cosmos=mock_cosmos,
                                context_provider=mock_context_provider,
                            )
        
        assert result1["run_id"] is not None
        assert result2["run_id"] is not None
        assert result1["run_id"] != result2["run_id"], "Each validation should get unique run_id"


class TestNoOrderSideEffects:
    """Test that validation never places orders or creates positions."""

    @pytest.mark.asyncio
    async def test_no_cosmos_position_creation(self, runner, mock_cosmos, mock_context_provider, valid_call_evidence):
        """Validation should never call cosmos.create_position or similar."""
        with patch.object(runner, '_get_client'):
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='```json\n{"activity": "SELL", "timestamp": "2026-08-29T10:00:00Z"}\n```'
            ))
            
            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                with patch.object(runner, '_run_supervisor_review', return_value={
                    "net_assessment": "APPROVE",
                    "challenge_strength": "WEAK",
                    "trace_id": "sup-test",
                }):
                    with patch.object(runner, '_run_alpha_review', return_value={
                        "recommendation": "APPROVE",
                        "trace_id": "alpha-test",
                    }):
                        with patch.object(runner, '_record_trace', return_value="trace-test"):
                            with patch('src.agent_runner.build_rule_evaluation', return_value={}):
                                await runner.run_contract_validation(
                                    symbol="AAPL",
                                    side="call",
                                    strike=155.0,
                                    expiration="2026-10-16",
                                    evidence_snapshot=valid_call_evidence,
                                    cosmos=mock_cosmos,
                                    context_provider=mock_context_provider,
                                )
        
        # Verify no position/order creation methods were called
        assert not mock_cosmos.create_position.called, "Should not create positions"
        assert not mock_cosmos.update_position.called, "Should not update positions"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
