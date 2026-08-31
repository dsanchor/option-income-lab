"""Tests for full-context parity in contract validation.

Focused tests for the binding design changes (Danny's full-context parity design):
- T3: Context Parity — Primary prompt contains all 4 pages + enrichment + volatility + contract evidence
- T7: Rule Evaluation Enrichment Parity — enrichment_data passed to rule evaluator

Tests verify that the AgentRunner portion of the binding design correctly:
1. Builds validation messages with full market context (4-page block + enrichment + volatility + labeled contract evidence)
2. Passes enrichment_data from evidence_snapshot to build_rule_evaluation
3. Ensures Alpha receives full context + chain
4. Ensures Supervisor receives full context
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timezone

from src.agent_runner import AgentRunner


@pytest.fixture
def mock_cosmos():
    """Mock CosmosDBService."""
    cosmos = MagicMock()
    cosmos.get_symbols.return_value = []
    cosmos.insert_agent_trace.return_value = "trace-123"
    return cosmos


@pytest.fixture
def mock_context_provider():
    """Mock ContextProvider."""
    provider = MagicMock()
    provider.get_context.return_value = "Previous: WAIT on 2026-08-25"
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
def full_context_evidence():
    """Valid evidence snapshot with full-context parity fields.
    
    After Livingston's changes, evidence_snapshot contains:
    - market_data_text: full 4-page block + enrichment + volatility + labeled contract evidence
    - enrichment_data: structured dict for rule_evaluator
    """
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
        "next_earnings_date": "2026-09-15",
        "ex_dividend_date": "2026-09-05",
        "atm_iv": 0.28,
        "iv_rank": 45,
        # Full market context block — canonical 4-page block + enrichment + volatility + contract evidence
        "market_data_text": """--- OVERVIEW PAGE (NASDAQ:AAPL) ---
{"symbol": "AAPL", "companyName": "Apple Inc.", "marketCap": 2800000000000}

--- TECHNICALS PAGE (NASDAQ:AAPL) ---
{"sma_20": 148.5, "sma_50": 145.2, "rsi": 58}

--- FORECAST PAGE (NASDAQ:AAPL) ---
{"consensus": "Buy", "target_price": 160.0}

--- DIVIDENDS PAGE (NASDAQ:AAPL) ---
{"ex_dividend_date": "2026-09-05", "amount": 0.25, "yield": 0.67}

--- ENRICHMENT (AAPL) ---
SYMBOL ENRICHMENT (pre-computed watchlist signals — use as a confluence cross-check for your decision):
- Tech Timing score: 68/100
- Momentum: bullish
- Entry tag: strong
- DGI quality score: 85/100

--- VOLATILITY (AAPL) ---
IV Rank: 45/100
HV 30-day: 24.5%
ATM IV: 28.0%
Premium Richness: moderate

--- VALIDATED CONTRACT EVIDENCE (AAPL 2026-09-20 155.0 CALL) ---
Contract: AAPL 2026-09-20 $155 CALL
Underlying: $150.00
Premium: $2.50 bid / $2.55 ask
Delta: 0.25
IV: 30.0%
Open Interest: 1000
Chain timestamp: 2026-08-29T10:00:00Z
Next earnings: 2026-09-15
Ex-dividend: 2026-09-05""",
        # Structured enrichment data for rule_evaluator
        "enrichment_data": {
            "tech_timing": 68,
            "momentum": "bullish",
            "entry_tag": "strong",
            "dgi_quality": 85,
        },
        # Calendar provenance (for auditability)
        "calendar_provenance": {
            "next_earnings": {"date": "2026-09-15", "source": "yfinance"},
            "ex_dividend": {"date": "2026-09-05", "source": "yfinance"},
        },
    }


class TestT3ContextParity:
    """Test T3: Context Parity — Primary prompt contains all 4 pages + enrichment + volatility + contract evidence."""

    @pytest.mark.asyncio
    async def test_primary_prompt_contains_all_pages(
        self, runner, mock_cosmos, mock_context_provider, full_context_evidence
    ):
        """Verify Primary agent prompt contains OVERVIEW, TECHNICALS, FORECAST, DIVIDENDS, ENRICHMENT, VOLATILITY, and VALIDATED CONTRACT EVIDENCE."""
        
        # Mock agent.run() to return a valid SELL response
        mock_agent_response = MagicMock()
        mock_agent_response.text = """
{
  "activity": "SELL",
  "symbol": "AAPL",
  "timestamp": "2026-08-29T10:00:00Z",
  "reasoning": "Strong fundamentals and technical setup",
  "risk_flags": []
}
"""
        
        # Mock Supervisor and Alpha to return valid responses
        with patch.object(runner, "_run_supervisor_review", new_callable=AsyncMock) as mock_supervisor, \
             patch.object(runner, "_run_alpha_review", new_callable=AsyncMock) as mock_alpha, \
             patch("src.agent_runner.Agent") as mock_agent_class:
            
            # Configure agent mock
            mock_agent_instance = MagicMock()
            mock_agent_instance.run = AsyncMock(return_value=mock_agent_response)
            mock_agent_class.return_value = mock_agent_instance
            
            # Configure Supervisor to approve
            mock_supervisor.return_value = {
                "net_assessment": "ORIGINAL_HOLDS",
                "trace_id": "supervisor-trace-123"
            }
            
            # Configure Alpha to not find alternatives
            mock_alpha.return_value = {
                "opportunity_strength": "NONE",
                "alternative": {},
                "relaxed_parameter": "none",
                "trace_id": "alpha-trace-123"
            }
            
            # Run validation
            result = await runner.run_contract_validation(
                symbol="AAPL",
                side="call",
                strike=155.0,
                expiration="2026-09-20",
                evidence_snapshot=full_context_evidence,
                cosmos=mock_cosmos,
                context_provider=mock_context_provider,
            )
            
            # Extract the user message passed to primary agent
            assert mock_agent_instance.run.called
            primary_message = mock_agent_instance.run.call_args[0][0]
            
            # Assert all canonical page headers are present
            assert "--- OVERVIEW PAGE (NASDAQ:AAPL) ---" in primary_message
            assert "--- TECHNICALS PAGE (NASDAQ:AAPL) ---" in primary_message
            assert "--- FORECAST PAGE (NASDAQ:AAPL) ---" in primary_message
            assert "--- DIVIDENDS PAGE (NASDAQ:AAPL) ---" in primary_message
            
            # Assert enrichment and volatility sections are present
            assert "--- ENRICHMENT (AAPL) ---" in primary_message
            assert "--- VOLATILITY (AAPL) ---" in primary_message
            
            # Assert contract evidence section is present and labeled
            assert "--- VALIDATED CONTRACT EVIDENCE (AAPL 2026-09-20 155.0 CALL) ---" in primary_message
            
            # Assert structured boundaries (from design §3.3)
            assert "=== PRE-FETCHED MARKET DATA ===" in primary_message
            assert "=== END OF DATA ===" in primary_message
            
            # Assert specific data points are visible
            assert "Tech Timing score: 68/100" in primary_message
            assert "IV Rank: 45/100" in primary_message
            assert "Ex-dividend: 2026-09-05" in primary_message
            assert "Next earnings: 2026-09-15" in primary_message

    @pytest.mark.asyncio
    async def test_supervisor_receives_full_context(
        self, runner, mock_cosmos, mock_context_provider, full_context_evidence
    ):
        """Verify Supervisor agent receives full market data block (same as Primary)."""
        
        # Mock agent.run() to return a valid SELL response
        mock_agent_response = MagicMock()
        mock_agent_response.text = """
{
  "activity": "SELL",
  "symbol": "AAPL",
  "timestamp": "2026-08-29T10:00:00Z",
  "reasoning": "Strong setup",
  "risk_flags": []
}
"""
        
        with patch.object(runner, "_run_supervisor_review", new_callable=AsyncMock) as mock_supervisor, \
             patch.object(runner, "_run_alpha_review", new_callable=AsyncMock) as mock_alpha, \
             patch("src.agent_runner.Agent") as mock_agent_class:
            
            mock_agent_instance = MagicMock()
            mock_agent_instance.run = AsyncMock(return_value=mock_agent_response)
            mock_agent_class.return_value = mock_agent_instance
            
            mock_supervisor.return_value = {
                "net_assessment": "ORIGINAL_HOLDS",
                "trace_id": "supervisor-trace-123"
            }
            
            mock_alpha.return_value = {
                "opportunity_strength": "NONE",
                "alternative": {},
                "trace_id": "alpha-trace-123"
            }
            
            # Run validation
            await runner.run_contract_validation(
                symbol="AAPL",
                side="call",
                strike=155.0,
                expiration="2026-09-20",
                evidence_snapshot=full_context_evidence,
                cosmos=mock_cosmos,
                context_provider=mock_context_provider,
            )
            
            # Assert Supervisor was called with full market_data
            assert mock_supervisor.called
            supervisor_call_kwargs = mock_supervisor.call_args[1]
            supervisor_market_data = supervisor_call_kwargs["market_data"]
            
            # Supervisor should receive the same full context as Primary
            assert "--- OVERVIEW PAGE (NASDAQ:AAPL) ---" in supervisor_market_data
            assert "--- TECHNICALS PAGE (NASDAQ:AAPL) ---" in supervisor_market_data
            assert "--- FORECAST PAGE (NASDAQ:AAPL) ---" in supervisor_market_data
            assert "--- DIVIDENDS PAGE (NASDAQ:AAPL) ---" in supervisor_market_data
            assert "--- ENRICHMENT (AAPL) ---" in supervisor_market_data
            assert "--- VOLATILITY (AAPL) ---" in supervisor_market_data
            assert "--- VALIDATED CONTRACT EVIDENCE" in supervisor_market_data

    @pytest.mark.asyncio
    async def test_alpha_receives_full_context_plus_chain(
        self, runner, mock_cosmos, mock_context_provider, full_context_evidence
    ):
        """Verify Alpha agent receives full market data block + filtered chain context."""
        
        # Mock agent.run() to return a valid SELL response
        mock_agent_response = MagicMock()
        mock_agent_response.text = """
{
  "activity": "SELL",
  "symbol": "AAPL",
  "timestamp": "2026-08-29T10:00:00Z",
  "reasoning": "Strong setup",
  "risk_flags": []
}
"""
        
        chain_context = """--- OPTIONS CHAIN (filtered subset for alternatives) ---
{"calls": [{"strike": 160, "expiration": "2026-09-20", "bid": 1.50}]}"""
        
        with patch.object(runner, "_run_supervisor_review", new_callable=AsyncMock) as mock_supervisor, \
             patch.object(runner, "_run_alpha_review", new_callable=AsyncMock) as mock_alpha, \
             patch("src.agent_runner.Agent") as mock_agent_class:
            
            mock_agent_instance = MagicMock()
            mock_agent_instance.run = AsyncMock(return_value=mock_agent_response)
            mock_agent_class.return_value = mock_agent_instance
            
            mock_supervisor.return_value = {
                "net_assessment": "ORIGINAL_HOLDS",
                "trace_id": "supervisor-trace-123"
            }
            
            mock_alpha.return_value = {
                "opportunity_strength": "NONE",
                "alternative": {},
                "trace_id": "alpha-trace-123"
            }
            
            # Run validation WITH chain context
            await runner.run_contract_validation(
                symbol="AAPL",
                side="call",
                strike=155.0,
                expiration="2026-09-20",
                evidence_snapshot=full_context_evidence,
                cosmos=mock_cosmos,
                context_provider=mock_context_provider,
                chain_context_text=chain_context,
            )
            
            # Assert Alpha was called with full market_data + chain
            assert mock_alpha.called
            alpha_call_kwargs = mock_alpha.call_args[1]
            alpha_market_data = alpha_call_kwargs["market_data"]
            
            # Alpha should receive full context PLUS chain
            assert "--- OVERVIEW PAGE (NASDAQ:AAPL) ---" in alpha_market_data
            assert "--- TECHNICALS PAGE (NASDAQ:AAPL) ---" in alpha_market_data
            assert "--- FORECAST PAGE (NASDAQ:AAPL) ---" in alpha_market_data
            assert "--- DIVIDENDS PAGE (NASDAQ:AAPL) ---" in alpha_market_data
            assert "--- ENRICHMENT (AAPL) ---" in alpha_market_data
            assert "--- VOLATILITY (AAPL) ---" in alpha_market_data
            assert "--- VALIDATED CONTRACT EVIDENCE" in alpha_market_data
            
            # Alpha should also have chain context appended
            assert "--- OPTIONS CHAIN (filtered subset for alternatives) ---" in alpha_market_data


class TestT7RuleEvaluationEnrichmentParity:
    """Test T7: Rule Evaluation Enrichment Parity — enrichment_data passed to rule evaluator."""

    @pytest.mark.asyncio
    async def test_enrichment_data_passed_to_rule_evaluator(
        self, runner, mock_cosmos, mock_context_provider, full_context_evidence
    ):
        """Verify build_rule_evaluation receives non-None enrichment_data from evidence_snapshot."""
        
        # Mock agent.run() to return a valid SELL response
        mock_agent_response = MagicMock()
        mock_agent_response.text = """
{
  "activity": "SELL",
  "symbol": "AAPL",
  "timestamp": "2026-08-29T10:00:00Z",
  "reasoning": "Strong setup",
  "risk_flags": []
}
"""
        
        with patch.object(runner, "_run_supervisor_review", new_callable=AsyncMock) as mock_supervisor, \
             patch.object(runner, "_run_alpha_review", new_callable=AsyncMock) as mock_alpha, \
             patch("src.agent_runner.Agent") as mock_agent_class, \
             patch("src.agent_runner.build_rule_evaluation") as mock_build_rule_eval:
            
            mock_agent_instance = MagicMock()
            mock_agent_instance.run = AsyncMock(return_value=mock_agent_response)
            mock_agent_class.return_value = mock_agent_instance
            
            mock_supervisor.return_value = {
                "net_assessment": "ORIGINAL_HOLDS",
                "trace_id": "supervisor-trace-123"
            }
            
            mock_alpha.return_value = {
                "opportunity_strength": "NONE",
                "alternative": {},
                "trace_id": "alpha-trace-123"
            }
            
            # Configure mock_build_rule_evaluation to return a valid result
            mock_build_rule_eval.return_value = {
                "earnings_gate": "pass",
                "category_gate": "pass",
                "enrichment_checks": {"tech_timing": "pass"},
            }
            
            # Run validation
            await runner.run_contract_validation(
                symbol="AAPL",
                side="call",
                strike=155.0,
                expiration="2026-09-20",
                evidence_snapshot=full_context_evidence,
                cosmos=mock_cosmos,
                context_provider=mock_context_provider,
            )
            
            # Assert build_rule_evaluation was called
            assert mock_build_rule_eval.called
            
            # Extract the enrichment_data argument
            call_kwargs = mock_build_rule_eval.call_args[1]
            enrichment_data_arg = call_kwargs.get("enrichment_data")
            
            # Assert enrichment_data is NOT None (parity with normal Following)
            assert enrichment_data_arg is not None
            
            # Assert enrichment_data matches the snapshot structure
            assert enrichment_data_arg == {
                "tech_timing": 68,
                "momentum": "bullish",
                "entry_tag": "strong",
                "dgi_quality": 85,
            }
            
            # Assert other expected arguments
            assert call_kwargs["agent_type"] == "covered_call"
            assert call_kwargs["phase"] == "contract_validation"
            assert call_kwargs["category"] == "balanced"

    @pytest.mark.asyncio
    async def test_missing_enrichment_data_graceful_fallback(
        self, runner, mock_cosmos, mock_context_provider, full_context_evidence
    ):
        """Verify graceful handling when enrichment_data is missing (backward compatibility)."""
        
        # Create evidence WITHOUT enrichment_data (legacy snapshot)
        legacy_evidence = full_context_evidence.copy()
        del legacy_evidence["enrichment_data"]
        
        # Mock agent.run() to return a valid SELL response
        mock_agent_response = MagicMock()
        mock_agent_response.text = """
{
  "activity": "SELL",
  "symbol": "AAPL",
  "timestamp": "2026-08-29T10:00:00Z",
  "reasoning": "Strong setup",
  "risk_flags": []
}
"""
        
        with patch.object(runner, "_run_supervisor_review", new_callable=AsyncMock) as mock_supervisor, \
             patch.object(runner, "_run_alpha_review", new_callable=AsyncMock) as mock_alpha, \
             patch("src.agent_runner.Agent") as mock_agent_class, \
             patch("src.agent_runner.build_rule_evaluation") as mock_build_rule_eval:
            
            mock_agent_instance = MagicMock()
            mock_agent_instance.run = AsyncMock(return_value=mock_agent_response)
            mock_agent_class.return_value = mock_agent_instance
            
            mock_supervisor.return_value = {
                "net_assessment": "ORIGINAL_HOLDS",
                "trace_id": "supervisor-trace-123"
            }
            
            mock_alpha.return_value = {
                "opportunity_strength": "NONE",
                "alternative": {},
                "trace_id": "alpha-trace-123"
            }
            
            mock_build_rule_eval.return_value = {
                "earnings_gate": "pass",
                "category_gate": "pass",
            }
            
            # Run validation with legacy evidence
            result = await runner.run_contract_validation(
                symbol="AAPL",
                side="call",
                strike=155.0,
                expiration="2026-09-20",
                evidence_snapshot=legacy_evidence,
                cosmos=mock_cosmos,
                context_provider=mock_context_provider,
            )
            
            # Assert validation still completes successfully
            assert result["activity"] == "SELL"
            assert result["validation_status"] == "approved"
            
            # Assert build_rule_evaluation was called with None (backward compatible)
            assert mock_build_rule_eval.called
            call_kwargs = mock_build_rule_eval.call_args[1]
            enrichment_data_arg = call_kwargs.get("enrichment_data")
            
            # Should be None when missing from snapshot (backward compatibility)
            assert enrichment_data_arg is None


class TestExDividendVisibility:
    """Test that ex-dividend and earnings dates are visible in agent prompts."""

    @pytest.mark.asyncio
    async def test_exdiv_from_dividends_appears_in_prompt(
        self, runner, mock_cosmos, mock_context_provider, full_context_evidence
    ):
        """Reproduce the ex-dividend omission bug fix: verify ex-div date appears in prompt."""
        
        # Mock agent.run() to return a valid SELL response
        mock_agent_response = MagicMock()
        mock_agent_response.text = """
{
  "activity": "SELL",
  "symbol": "AAPL",
  "timestamp": "2026-08-29T10:00:00Z",
  "reasoning": "Ex-div date is visible",
  "risk_flags": []
}
"""
        
        with patch.object(runner, "_run_supervisor_review", new_callable=AsyncMock) as mock_supervisor, \
             patch.object(runner, "_run_alpha_review", new_callable=AsyncMock) as mock_alpha, \
             patch("src.agent_runner.Agent") as mock_agent_class:
            
            mock_agent_instance = MagicMock()
            mock_agent_instance.run = AsyncMock(return_value=mock_agent_response)
            mock_agent_class.return_value = mock_agent_instance
            
            mock_supervisor.return_value = {
                "net_assessment": "ORIGINAL_HOLDS",
                "trace_id": "supervisor-trace-123"
            }
            
            mock_alpha.return_value = {
                "opportunity_strength": "NONE",
                "alternative": {},
                "trace_id": "alpha-trace-123"
            }
            
            # Run validation
            await runner.run_contract_validation(
                symbol="AAPL",
                side="call",
                strike=155.0,
                expiration="2026-09-20",
                evidence_snapshot=full_context_evidence,
                cosmos=mock_cosmos,
                context_provider=mock_context_provider,
            )
            
            # Extract the user message passed to primary agent
            assert mock_agent_instance.run.called
            primary_message = mock_agent_instance.run.call_args[0][0]
            
            # Assert ex-dividend date from dividends page is visible
            assert "ex_dividend_date" in primary_message or "Ex-dividend: 2026-09-05" in primary_message
            
            # Assert earnings date is also visible
            assert "Next earnings: 2026-09-15" in primary_message
