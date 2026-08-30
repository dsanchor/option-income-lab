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


class TestDictCompatibility:
    """Tests for dict-to-LlmConfig normalization (backward compatibility)."""

    def test_constructor_llm_dict_normalized(self, mock_cosmos, mock_context_provider):
        """Constructor accepts plain dict for llm= and normalizes to LlmConfig."""
        llm_dict = {"provider": "azure", "api_key": "test", "endpoint": "https://test"}
        runner = AgentRunner(
            llm=llm_dict,
            model="gpt-5.4-mini",
        )
        # Internal _llm should be LlmConfig with .provider accessible
        assert hasattr(runner._llm, "provider")
        assert runner._llm.provider == "azure"
        assert runner._llm.api_key == "test"
        assert runner._llm.endpoint == "https://test"

    def test_constructor_function_llms_dict_normalized(self, mock_cosmos, mock_context_provider):
        """Constructor normalizes function_llms dict values to LlmConfig."""
        func_dict = {"validate_contract": {"provider": "gemini", "api_key": "key1"}}
        runner = AgentRunner(
            llm={"provider": "azure", "api_key": "test", "endpoint": "https://test"},
            model="gpt-5.4-mini",
            function_llms=func_dict,
        )
        # Internal _function_llms values should be LlmConfig
        assert "validate_contract" in runner._function_llms
        assert hasattr(runner._function_llms["validate_contract"], "provider")
        assert runner._function_llms["validate_contract"].provider == "gemini"

    def test_set_function_llms_dict_normalized(self, runner):
        """set_function_llms accepts dict values and normalizes to LlmConfig."""
        new_dict = {"alpha": {"provider": "azure", "api_key": "alpha_key", "endpoint": "https://alpha"}}
        runner.set_function_llms(new_dict)
        assert "alpha" in runner._function_llms
        assert hasattr(runner._function_llms["alpha"], "provider")
        assert runner._function_llms["alpha"].provider == "azure"
        assert runner._function_llms["alpha"].api_key == "alpha_key"

    def test_normalize_preserves_all_fields(self):
        """_normalize_llm_config preserves all LlmConfig fields including optional endpoint."""
        input_dict = {
            "provider": "gemini",
            "api_key": "test_key",
            "endpoint": "https://endpoint",
        }
        result = AgentRunner._normalize_llm_config(input_dict)
        assert result.provider == "gemini"
        assert result.api_key == "test_key"
        assert result.endpoint == "https://endpoint"

    def test_normalize_llmconfig_instance_passthrough(self):
        """_normalize_llm_config passes through existing LlmConfig unchanged."""
        from src.llm import LlmConfig
        original = LlmConfig(provider="azure", api_key="test", endpoint="https://test")
        result = AgentRunner._normalize_llm_config(original)
        assert result is original


class TestAlphaReviewContractRegression:
    """Regression test for Alpha review in contract validation.

    Production failure (2026-08-30):
    TypeError: AgentRunner._run_alpha_review() got an unexpected keyword argument 'supervisor_view'

    Root cause: run_contract_validation incorrectly passed supervisor_view to _run_alpha_review,
    but Alpha is designed to independently review the primary decision, not the Supervisor output.
    """

    @pytest.mark.asyncio
    async def test_alpha_review_receives_correct_arguments(self, runner, mock_cosmos, mock_context_provider, valid_call_evidence):
        """Alpha review should be called with correct arguments (no supervisor_view)."""
        with patch.object(runner, '_get_client'):
            mock_agent_instance = AsyncMock()
            # Primary returns SELL
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='```json\n{"activity": "SELL", "timestamp": "2026-08-29T10:00:00Z"}\n```'
            ))

            mock_supervisor_view = {
                "net_assessment": "APPROVE",
                "supervisor_strength": "STRONG"
            }

            mock_alpha_view = {
                "recommendation": "APPROVE",
                "opportunity_strength": "STRONG"
            }

            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                with patch.object(runner, '_run_supervisor_review', return_value=mock_supervisor_view):
                    with patch.object(runner, '_run_alpha_review', return_value=mock_alpha_view) as mock_alpha:
                        with patch.object(runner, '_record_trace', return_value="trace-regression"):
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

        # Verify _run_alpha_review was called
        assert mock_alpha.called, "_run_alpha_review should be called"

        # Verify the call arguments - should NOT include supervisor_view
        call_kwargs = mock_alpha.call_args.kwargs
        assert "supervisor_view" not in call_kwargs, \
            "supervisor_view should NOT be passed to _run_alpha_review"

        # Verify expected arguments ARE present
        assert "activity_payload" in call_kwargs
        assert "market_data" in call_kwargs
        assert "previous_context" in call_kwargs
        assert "agent_type" in call_kwargs
        assert "cosmos" in call_kwargs
        assert "run_id" in call_kwargs
        assert "parent_trace_id" in call_kwargs

        # Verify result includes both supervisor and alpha views
        assert result["supervisor_view"] == mock_supervisor_view
        assert result["alpha_view"] == mock_alpha_view
        assert result["validation_status"] == "approved"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
