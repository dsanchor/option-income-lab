"""Tests for chain-aware validation state machine (Cases A-F).

Comprehensive coverage of fail-closed WAIT/SELL state machine per design §2.2:
- Case A: Primary=SELL, Supervisor=ORIGINAL_HOLDS, Alpha=NONE → SELL requested
- Case B: Primary=SELL, Alpha alternative → SELL alternative if D4 passes, else requested
- Case C: Primary=WAIT, Alpha alternative → SELL if D4 passes, else WAIT
- Case D: Primary=WAIT, Alpha=NONE → WAIT
- Case E: Supervisor=RECONSIDER → WAIT (terminal veto)
- Case F: Review failure → WAIT
Plus: schema fields, supervisor/alpha approval semantics, prompt partitioning, incomplete alternatives.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from src.agent_runner import AgentRunner


@pytest.fixture
def mock_cosmos():
    cosmos = MagicMock()
    cosmos.get_symbols.return_value = []
    return cosmos


@pytest.fixture
def mock_context_provider():
    provider = MagicMock()
    provider.get_context.return_value = "No previous activities."
    return provider


@pytest.fixture
def runner():
    llm_config = {"provider": "azure", "api_key": "test", "endpoint": "test"}
    return AgentRunner(
        llm=llm_config,
        model="gpt-5.4-mini",
        telegram_notifier=None,
        plan_monitor_model="gpt-5.4-mini",
        function_llms={},
    )


@pytest.fixture
def valid_evidence():
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
        "chain_timestamp": "2026-08-30T10:00:00Z",
        "next_earnings_date": None,
        "ex_dividend_date": None,
        "atm_iv": 0.28,
        "iv_rank": 45,
        "market_data_text": "Market data snapshot...",
    }


class TestCaseA:
    """Case A: Primary=SELL, Supervisor=ORIGINAL_HOLDS, Alpha=NONE → SELL requested."""

    @pytest.mark.asyncio
    async def test_case_a_unanimous_approval(self, runner, mock_cosmos, mock_context_provider, valid_evidence):
        """Case A: All three approve requested contract → SELL."""
        with patch.object(runner, '_get_client'):
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='{"activity": "SELL", "symbol": "AAPL", "strike": 155.0, "expiration": "2026-09-19", "premium": 2.50}'
            ))

            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                with patch.object(runner, '_run_supervisor_review', return_value={
                    "challenge_strength": "WEAK",
                    "net_assessment": "ORIGINAL_HOLDS",
                    "counter_arguments": [],
                    "one_liner": "Holds"
                }):
                    with patch.object(runner, '_run_alpha_review', return_value={
                        "opportunity_strength": "NONE",
                        "relaxed_parameter": "none",
                        "parameter_detail": "Optimal",
                        "alternative": {"action": "N/A", "rationale": "Optimal", "trade_off": "N/A", "premium_comparison": "N/A"},
                        "one_liner": "Optimal"
                    }):
                        with patch.object(runner, '_record_trace', return_value="trace-a"):
                            with patch('src.agent_runner.build_rule_evaluation', return_value={}):
                                result = await runner.run_contract_validation(
                                    symbol="AAPL", side="call", strike=155.0, expiration="2026-09-19",
                                    evidence_snapshot=valid_evidence, cosmos=mock_cosmos, context_provider=mock_context_provider,
                                )

        assert result["activity"] == "SELL"
        assert result["is_alert"] is True
        assert result["validation_status"] == "approved"
        assert result["requested_contract"]["strike"] == 155.0
        assert result["selected_contract"]["strike"] == 155.0
        assert result["relaxed_parameter"] is None
        assert result["selection_source"] == "requested_approved"


class TestCaseB:
    """Case B: Primary=SELL, Alpha alternative → SELL alternative if D4 passes, else requested."""

    @pytest.mark.asyncio
    async def test_case_b_alpha_alternative_d4_passes(self, runner, mock_cosmos, mock_context_provider, valid_evidence):
        """Case B (D4 pass): Primary approved, Alpha alternative validated → SELL alternative."""
        with patch.object(runner, '_get_client'):
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='{"activity": "SELL", "symbol": "AAPL", "strike": 155.0, "expiration": "2026-09-19", "premium": 2.50}'
            ))

            # D4 callback that approves the alternative
            def mock_d4(side, requested_strike, requested_expiration, alternative, category, underlying_price, next_earnings_date):
                return (True, None, {"strike": 157.5, "expiration": "2026-09-19", "bid": 2.10, "delta": 0.28})

            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                with patch.object(runner, '_run_supervisor_review', return_value={
                    "challenge_strength": "WEAK",
                    "net_assessment": "ORIGINAL_HOLDS",
                    "counter_arguments": [],
                    "one_liner": "Holds"
                }):
                    with patch.object(runner, '_run_alpha_review', return_value={
                        "opportunity_strength": "STRONG",
                        "relaxed_parameter": "delta_outside_category_range",
                        "parameter_detail": "Better delta",
                        "alternative": {
                            "action": "SELL at $157.50",
                            "rationale": "Better delta positioning",
                            "trade_off": "Slightly less premium",
                            "premium_comparison": "Requested: $2.50 | Alternative: $2.10",
                            "strike": 157.5,
                            "expiration": "2026-09-19",
                            "premium": 2.10,
                            "delta": 0.28
                        },
                        "one_liner": "Better delta"
                    }):
                        with patch.object(runner, '_record_trace', return_value="trace-b"):
                            with patch('src.agent_runner.build_rule_evaluation', return_value={}):
                                result = await runner.run_contract_validation(
                                    symbol="AAPL", side="call", strike=155.0, expiration="2026-09-19",
                                    evidence_snapshot=valid_evidence, cosmos=mock_cosmos, context_provider=mock_context_provider,
                                    validated_alternative_callback=mock_d4,
                                )

        assert result["activity"] == "SELL"
        assert result["selected_contract"]["strike"] == 157.5
        assert result["relaxed_parameter"] == "delta_outside_category_range"
        assert result["selection_source"] == "alpha_alternative"

    @pytest.mark.asyncio
    async def test_case_b_alpha_alternative_d4_fails(self, runner, mock_cosmos, mock_context_provider, valid_evidence):
        """Case B (D4 fail): Primary approved, Alpha alternative rejected → SELL requested (fallback)."""
        with patch.object(runner, '_get_client'):
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='{"activity": "SELL", "symbol": "AAPL", "strike": 155.0, "expiration": "2026-09-19", "premium": 2.50}'
            ))

            # D4 callback that rejects the alternative
            def mock_d4(side, requested_strike, requested_expiration, alternative, category, underlying_price, next_earnings_date):
                return (False, "both_parameters_changed", None)

            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                with patch.object(runner, '_run_supervisor_review', return_value={
                    "challenge_strength": "WEAK",
                    "net_assessment": "ORIGINAL_HOLDS",
                    "counter_arguments": [],
                    "one_liner": "Holds"
                }):
                    with patch.object(runner, '_run_alpha_review', return_value={
                        "opportunity_strength": "STRONG",
                        "relaxed_parameter": "delta_outside_category_range",
                        "parameter_detail": "Better delta",
                        "alternative": {
                            "action": "SELL at $160.00 Sep-26",
                            "rationale": "Better delta",
                            "trade_off": "Different expiration AND strike",
                            "premium_comparison": "Requested: $2.50 | Alternative: $1.80",
                            "strike": 160.0,
                            "expiration": "2026-09-26",
                            "premium": 1.80,
                            "delta": 0.32
                        },
                        "one_liner": "Better delta"
                    }):
                        with patch.object(runner, '_record_trace', return_value="trace-b-fail"):
                            with patch('src.agent_runner.build_rule_evaluation', return_value={}):
                                result = await runner.run_contract_validation(
                                    symbol="AAPL", side="call", strike=155.0, expiration="2026-09-19",
                                    evidence_snapshot=valid_evidence, cosmos=mock_cosmos, context_provider=mock_context_provider,
                                    validated_alternative_callback=mock_d4,
                                )

        assert result["activity"] == "SELL"
        assert result["selected_contract"]["strike"] == 155.0  # Fallback to requested
        assert result["selection_source"] == "requested_approved"


class TestCaseC:
    """Case C: Primary=WAIT, Alpha alternative → SELL if D4 passes, else WAIT."""

    @pytest.mark.asyncio
    async def test_case_c_alpha_rescues_wait_d4_passes(self, runner, mock_cosmos, mock_context_provider, valid_evidence):
        """Case C (D4 pass): Primary=WAIT, Alpha alternative validated → SELL alternative."""
        with patch.object(runner, '_get_client'):
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='{"activity": "WAIT", "symbol": "AAPL", "strike": 155.0, "expiration": "2026-09-19", "premium": 2.50, "reason": "Delta outside range"}'
            ))

            # D4 callback that approves the alternative
            def mock_d4(side, requested_strike, requested_expiration, alternative, category, underlying_price, next_earnings_date):
                return (True, None, {"strike": 157.5, "expiration": "2026-09-19", "bid": 2.10, "delta": 0.28})

            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                with patch.object(runner, '_run_supervisor_review', return_value={
                    "challenge_strength": "WEAK",
                    "net_assessment": "ORIGINAL_HOLDS",
                    "counter_arguments": [],
                    "one_liner": "Holds"
                }):
                    with patch.object(runner, '_run_alpha_review', return_value={
                        "opportunity_strength": "STRONG",
                        "relaxed_parameter": "delta_outside_category_range",
                        "parameter_detail": "Better delta",
                        "alternative": {
                            "action": "SELL at $157.50",
                            "rationale": "Rescues WAIT with better delta",
                            "trade_off": "Slightly less premium",
                            "premium_comparison": "Requested: $2.50 | Alternative: $2.10",
                            "strike": 157.5,
                            "expiration": "2026-09-19",
                            "premium": 2.10,
                            "delta": 0.28
                        },
                        "one_liner": "Rescues WAIT"
                    }):
                        with patch.object(runner, '_record_trace', return_value="trace-c"):
                            with patch('src.agent_runner.build_rule_evaluation', return_value={}):
                                result = await runner.run_contract_validation(
                                    symbol="AAPL", side="call", strike=155.0, expiration="2026-09-19",
                                    evidence_snapshot=valid_evidence, cosmos=mock_cosmos, context_provider=mock_context_provider,
                                    validated_alternative_callback=mock_d4,
                                )

        assert result["activity"] == "SELL"
        assert result["selected_contract"]["strike"] == 157.5
        assert result["relaxed_parameter"] == "delta_outside_category_range"
        assert result["selection_source"] == "alpha_alternative"

    @pytest.mark.asyncio
    async def test_case_c_alpha_alternative_d4_fails(self, runner, mock_cosmos, mock_context_provider, valid_evidence):
        """Case C (D4 fail): Primary=WAIT, Alpha alternative rejected → WAIT (no fallback)."""
        with patch.object(runner, '_get_client'):
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='{"activity": "WAIT", "symbol": "AAPL", "strike": 155.0, "expiration": "2026-09-19", "premium": 2.50, "reason": "Delta outside range"}'
            ))

            # D4 callback that rejects the alternative
            def mock_d4(side, requested_strike, requested_expiration, alternative, category, underlying_price, next_earnings_date):
                return (False, "not_in_chain", None)

            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                with patch.object(runner, '_run_supervisor_review', return_value={
                    "challenge_strength": "WEAK",
                    "net_assessment": "ORIGINAL_HOLDS",
                    "counter_arguments": [],
                    "one_liner": "Holds"
                }):
                    with patch.object(runner, '_run_alpha_review', return_value={
                        "opportunity_strength": "STRONG",
                        "relaxed_parameter": "delta_outside_category_range",
                        "parameter_detail": "Better delta",
                        "alternative": {
                            "action": "SELL at $999.00",  # Fabricated strike
                            "rationale": "Hallucinated",
                            "trade_off": "N/A",
                            "premium_comparison": "N/A",
                            "strike": 999.0,
                            "expiration": "2026-09-19",
                            "premium": 5.00,
                            "delta": 0.30
                        },
                        "one_liner": "Fabricated"
                    }):
                        with patch.object(runner, '_record_trace', return_value="trace-c-fail"):
                            with patch('src.agent_runner.build_rule_evaluation', return_value={}):
                                result = await runner.run_contract_validation(
                                    symbol="AAPL", side="call", strike=155.0, expiration="2026-09-19",
                                    evidence_snapshot=valid_evidence, cosmos=mock_cosmos, context_provider=mock_context_provider,
                                    validated_alternative_callback=mock_d4,
                                )

        assert result["activity"] == "WAIT"
        assert result["is_alert"] is False
        assert result["validation_status"] == "review_incomplete"


class TestCaseD:
    """Case D: Primary=WAIT, Alpha=NONE → WAIT."""

    @pytest.mark.asyncio
    async def test_case_d_unanimous_wait(self, runner, mock_cosmos, mock_context_provider, valid_evidence):
        """Case D: Primary and Alpha both agree WAIT → WAIT."""
        with patch.object(runner, '_get_client'):
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='{"activity": "WAIT", "symbol": "AAPL", "strike": 155.0, "expiration": "2026-09-19", "premium": 2.50, "reason": "Earnings imminent"}'
            ))

            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                with patch.object(runner, '_run_supervisor_review', return_value={
                    "challenge_strength": "WEAK",
                    "net_assessment": "ORIGINAL_HOLDS",
                    "counter_arguments": [],
                    "one_liner": "Holds"
                }):
                    with patch.object(runner, '_run_alpha_review', return_value={
                        "opportunity_strength": "NONE",
                        "relaxed_parameter": "none",
                        "parameter_detail": "Hard gate prevents entry",
                        "alternative": {"action": "N/A", "rationale": "Earnings gate", "trade_off": "N/A", "premium_comparison": "N/A"},
                        "one_liner": "No alternative"
                    }):
                        with patch.object(runner, '_record_trace', return_value="trace-d"):
                            with patch('src.agent_runner.build_rule_evaluation', return_value={}):
                                result = await runner.run_contract_validation(
                                    symbol="AAPL", side="call", strike=155.0, expiration="2026-09-19",
                                    evidence_snapshot=valid_evidence, cosmos=mock_cosmos, context_provider=mock_context_provider,
                                )

        assert result["activity"] == "WAIT"
        assert result["is_alert"] is False
        assert result["validation_status"] == "approved"
        assert result["selection_source"] is None


class TestCaseE:
    """Case E: Supervisor=RECONSIDER → WAIT (terminal veto)."""

    @pytest.mark.asyncio
    async def test_case_e_supervisor_veto(self, runner, mock_cosmos, mock_context_provider, valid_evidence):
        """Case E: Supervisor RECONSIDER is terminal WAIT regardless of Primary/Alpha."""
        with patch.object(runner, '_get_client'):
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='{"activity": "SELL", "symbol": "AAPL", "strike": 155.0, "expiration": "2026-09-19", "premium": 2.50}'
            ))

            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                with patch.object(runner, '_run_supervisor_review', return_value={
                    "challenge_strength": "STRONG",
                    "net_assessment": "RECONSIDER",
                    "counter_arguments": ["Earnings risk imminent"],
                    "one_liner": "Reconsider"
                }):
                    with patch.object(runner, '_run_alpha_review', return_value={
                        "opportunity_strength": "STRONG",
                        "relaxed_parameter": "delta_outside_category_range",
                        "parameter_detail": "Alternative exists",
                        "alternative": {
                            "action": "SELL at $157.50",
                            "rationale": "Better delta",
                            "trade_off": "N/A",
                            "premium_comparison": "N/A",
                            "strike": 157.5,
                            "expiration": "2026-09-19",
                            "premium": 2.10,
                            "delta": 0.28
                        },
                        "one_liner": "Better delta"
                    }):
                        with patch.object(runner, '_record_trace', return_value="trace-e"):
                            with patch('src.agent_runner.build_rule_evaluation', return_value={}):
                                result = await runner.run_contract_validation(
                                    symbol="AAPL", side="call", strike=155.0, expiration="2026-09-19",
                                    evidence_snapshot=valid_evidence, cosmos=mock_cosmos, context_provider=mock_context_provider,
                                )

        assert result["activity"] == "WAIT"
        assert result["is_alert"] is False
        assert result["validation_status"] == "review_incomplete"
        assert "RECONSIDER" in result["note"]


class TestSchemaFields:
    """Test result schema contains correct chain-aware fields."""

    @pytest.mark.asyncio
    async def test_result_has_chain_aware_fields(self, runner, mock_cosmos, mock_context_provider, valid_evidence):
        """Result must include requested_contract, selected_contract, relaxed_parameter, etc."""
        with patch.object(runner, '_get_client'):
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='{"activity": "SELL", "symbol": "AAPL", "strike": 155.0, "expiration": "2026-09-19", "premium": 2.50}'
            ))

            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                with patch.object(runner, '_run_supervisor_review', return_value={
                    "challenge_strength": "WEAK",
                    "net_assessment": "ORIGINAL_HOLDS",
                    "counter_arguments": [],
                    "one_liner": "Holds"
                }):
                    with patch.object(runner, '_run_alpha_review', return_value={
                        "opportunity_strength": "NONE",
                        "relaxed_parameter": "none",
                        "parameter_detail": "Optimal",
                        "alternative": {"action": "N/A", "rationale": "Optimal", "trade_off": "N/A", "premium_comparison": "N/A"},
                        "one_liner": "Optimal"
                    }):
                        with patch.object(runner, '_record_trace', return_value="trace-schema"):
                            with patch('src.agent_runner.build_rule_evaluation', return_value={}):
                                result = await runner.run_contract_validation(
                                    symbol="AAPL", side="call", strike=155.0, expiration="2026-09-19",
                                    evidence_snapshot=valid_evidence, cosmos=mock_cosmos, context_provider=mock_context_provider,
                                )

        assert "requested_contract" in result
        assert "selected_contract" in result
        assert "relaxed_parameter" in result
        assert "comparison_rationale" in result
        assert "selection_source" in result
        assert result["requested_contract"]["strike"] == 155.0
        assert result["requested_contract"]["premium"] == 2.50
        assert result["requested_contract"]["delta"] == 0.25


class TestPromptPartitioning:
    """Test Alpha receives chain context, Primary/Supervisor do not."""

    @pytest.mark.asyncio
    async def test_alpha_receives_chain_context(self, runner, mock_cosmos, mock_context_provider, valid_evidence):
        """Alpha should receive chain_context_text, Primary and Supervisor should not."""
        chain_context = "OPTIONS_CHAIN: calls 155.0, 157.5, 160.0"

        with patch.object(runner, '_get_client'):
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='{"activity": "SELL", "symbol": "AAPL", "strike": 155.0, "expiration": "2026-09-19", "premium": 2.50}'
            ))

            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                with patch.object(runner, '_run_supervisor_review', return_value={
                    "challenge_strength": "WEAK",
                    "net_assessment": "ORIGINAL_HOLDS",
                    "counter_arguments": [],
                    "one_liner": "Holds"
                }) as mock_supervisor:
                    with patch.object(runner, '_run_alpha_review', return_value={
                        "opportunity_strength": "NONE",
                        "relaxed_parameter": "none",
                        "parameter_detail": "Optimal",
                        "alternative": {"action": "N/A", "rationale": "Optimal", "trade_off": "N/A", "premium_comparison": "N/A"},
                        "one_liner": "Optimal"
                    }) as mock_alpha:
                        with patch.object(runner, '_record_trace', return_value="trace-partition"):
                            with patch('src.agent_runner.build_rule_evaluation', return_value={}):
                                result = await runner.run_contract_validation(
                                    symbol="AAPL", side="call", strike=155.0, expiration="2026-09-19",
                                    evidence_snapshot=valid_evidence, cosmos=mock_cosmos, context_provider=mock_context_provider,
                                    chain_context_text=chain_context,
                                )

        # Verify Alpha received chain context
        alpha_call_kwargs = mock_alpha.call_args.kwargs
        assert "market_data" in alpha_call_kwargs
        assert chain_context in alpha_call_kwargs["market_data"]
        assert alpha_call_kwargs["is_validation_context"] is True

        # Verify Supervisor did NOT receive chain context
        supervisor_call_kwargs = mock_supervisor.call_args.kwargs
        assert "market_data" in supervisor_call_kwargs
        assert chain_context not in supervisor_call_kwargs["market_data"]


class TestIncompleteAlternative:
    """Test incomplete Alpha alternative cannot produce SELL."""

    @pytest.mark.asyncio
    async def test_incomplete_alternative_no_sell(self, runner, mock_cosmos, mock_context_provider, valid_evidence):
        """Alpha alternative missing strike/expiration should not trigger SELL."""
        with patch.object(runner, '_get_client'):
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=MagicMock(
                text='{"activity": "WAIT", "symbol": "AAPL", "strike": 155.0, "expiration": "2026-09-19", "premium": 2.50, "reason": "Delta outside range"}'
            ))

            with patch('src.agent_runner.Agent', return_value=mock_agent_instance):
                with patch.object(runner, '_run_supervisor_review', return_value={
                    "challenge_strength": "WEAK",
                    "net_assessment": "ORIGINAL_HOLDS",
                    "counter_arguments": [],
                    "one_liner": "Holds"
                }):
                    with patch.object(runner, '_run_alpha_review', return_value={
                        "opportunity_strength": "STRONG",  # Claims alternative exists
                        "relaxed_parameter": "delta_outside_category_range",
                        "parameter_detail": "Better delta",
                        "alternative": {
                            "action": "SELL at better delta",
                            "rationale": "Better delta",
                            "trade_off": "N/A",
                            "premium_comparison": "N/A",
                            # Missing strike and expiration!
                        },
                        "one_liner": "Better delta"
                    }):
                        with patch.object(runner, '_record_trace', return_value="trace-incomplete"):
                            with patch('src.agent_runner.build_rule_evaluation', return_value={}):
                                result = await runner.run_contract_validation(
                                    symbol="AAPL", side="call", strike=155.0, expiration="2026-09-19",
                                    evidence_snapshot=valid_evidence, cosmos=mock_cosmos, context_provider=mock_context_provider,
                                )

        # Incomplete alternative should result in WAIT with approved status
        # (unanimous WAIT: Primary=WAIT, Alpha incomplete alternative treated as endorsement)
        assert result["activity"] == "WAIT"
        assert result["validation_status"] == "approved"
