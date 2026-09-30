from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from src.agent_runner import AgentRunner
from src.open_call_monitor_agent import run_open_call_monitor
from src.position_monitor_selection import (
    POSITION_CONSTRAINT_ALIASES,
    PositionSelectionError,
    parse_monitor_position_constraints,
    resolve_active_monitor_position,
)
from web import app as web_app
from web.app import _DashboardProcessState


def _position(
    position_id: str,
    *,
    account_id: str,
    is_paper: bool = False,
    status: str = "active",
    strike: float = 500,
    expiration: str = "2026-10-16",
) -> dict:
    return {
        "position_id": position_id,
        "type": "call",
        "strike": strike,
        "expiration": expiration,
        "status": status,
        "quantity": 2 if position_id == "pos-a" else 7,
        "account_id": account_id,
        "is_paper": is_paper,
        "source": {
            "contract_id": "MSFT-identical-contract",
            "instrument_id": "instrument-msft",
            "premium": 3.25,
        },
    }


def _symbol(*positions: dict) -> dict:
    return {"symbol": "MSFT", "exchange": "NASDAQ", "positions": list(positions)}


@pytest.mark.parametrize("reverse", [False, True])
def test_position_id_selects_exact_same_contract_position_regardless_of_order(reverse):
    positions = [
        _position("pos-a", account_id="acct-a"),
        _position("pos-b", account_id="acct-b", is_paper=True),
    ]
    if reverse:
        positions.reverse()

    selected = resolve_active_monitor_position(
        _symbol(*positions),
        symbol="MSFT",
        option_type="call",
        position_id="pos-b",
        constraints={
            "account_id": "acct-b",
            "contract_id": "MSFT-identical-contract",
            "is_paper": True,
        },
    )

    assert selected["position_id"] == "pos-b"
    assert selected["quantity"] == 7


def test_symbol_only_is_ambiguous_but_unique_legacy_request_still_resolves():
    with pytest.raises(PositionSelectionError, match="position_id is required"):
        resolve_active_monitor_position(
            _symbol(
                _position("pos-a", account_id="acct-a"),
                _position("pos-b", account_id="acct-b"),
            ),
            symbol="MSFT",
            option_type="call",
        )

    selected = resolve_active_monitor_position(
        _symbol(_position("pos-a", account_id="acct-a")),
        symbol="MSFT",
        option_type="call",
    )
    assert selected["position_id"] == "pos-a"


@pytest.mark.parametrize(
    ("position_id", "constraints", "message"),
    [
        ("stale", {}, "was not found"),
        ("pos-closed", {}, "is not active"),
        ("pos-a", {"account_id": "acct-b"}, "account_id does not match"),
        ("pos-a", {"is_paper": True}, "is_paper does not match"),
        ("pos-a", {"strike": 510}, "strike does not match"),
    ],
)
def test_stale_closed_and_mismatched_identity_fail_closed(
    position_id, constraints, message
):
    with pytest.raises(PositionSelectionError, match=message):
        resolve_active_monitor_position(
            _symbol(
                _position("pos-a", account_id="acct-a"),
                _position("pos-closed", account_id="acct-a", status="closed"),
            ),
            symbol="MSFT",
            option_type="call",
            position_id=position_id,
            constraints=constraints,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("option_type", None, "option_type must be a non-empty string"),
        ("option_type", "", "option_type must be a non-empty string"),
        ("option_type", "   ", "option_type must be a non-empty string"),
        ("strike", None, "strike must be a finite number"),
        ("strike", "", "strike must be a finite number"),
        ("strike", "   ", "strike must be a finite number"),
        ("expiration", None, "expiration must be a non-empty string"),
        ("expiration", "", "expiration must be a non-empty string"),
        ("expiration", "   ", "expiration must be a non-empty string"),
        ("account_id", None, "account_id must be a non-empty string"),
        ("account_id", "", "account_id must be a non-empty string"),
        ("account_id", "   ", "account_id must be a non-empty string"),
        ("contract_id", None, "contract_id must be a non-empty string"),
        ("contract_id", "", "contract_id must be a non-empty string"),
        ("contract_id", "   ", "contract_id must be a non-empty string"),
        ("instrument_id", None, "instrument_id must be a non-empty string"),
        ("instrument_id", "", "instrument_id must be a non-empty string"),
        ("instrument_id", "   ", "instrument_id must be a non-empty string"),
        ("is_paper", None, "is_paper must be a boolean"),
        ("is_paper", "", "is_paper must be a boolean"),
        ("is_paper", "   ", "is_paper must be a boolean"),
    ],
)
def test_supplied_null_blank_and_whitespace_constraints_fail_closed(
    field, value, message
):
    with pytest.raises(PositionSelectionError, match=message) as exc_info:
        resolve_active_monitor_position(
            _symbol(_position("pos-a", account_id="acct-a")),
            symbol="MSFT",
            option_type="call",
            position_id="pos-a",
            constraints={field: value},
        )
    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("option_type", "calls", "option_type must be either call or put"),
        ("strike", True, "strike must be a finite number"),
        ("strike", "not-a-number", "strike must be a finite number"),
        ("expiration", "16-10-2026", "expiration must be a valid ISO date"),
        ("account_id", ["acct-a"], "account_id must be a non-empty string"),
        ("contract_id", {"id": "x"}, "contract_id must be a non-empty string"),
        ("instrument_id", 123, "instrument_id must be a non-empty string"),
        ("is_paper", 1, "is_paper must be a boolean"),
    ],
)
def test_malformed_constraints_fail_closed(field, value, message):
    with pytest.raises(PositionSelectionError, match=message) as exc_info:
        resolve_active_monitor_position(
            _symbol(_position("pos-a", account_id="acct-a")),
            symbol="MSFT",
            option_type="call",
            position_id="pos-a",
            constraints={field: value},
        )
    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    ("field", "aliases"),
    POSITION_CONSTRAINT_ALIASES.items(),
)
@pytest.mark.parametrize("nested", [False, True])
def test_every_constraint_alias_is_accepted_top_level_and_in_source(
    field, aliases, nested
):
    valid = {
        "option_type": "call",
        "strike": "500.00",
        "expiration": "2026-10-16",
        "account_id": "Acct-Exact",
        "contract_id": "MSFT261016C00500000",
        "instrument_id": "Instrument-Exact",
        "is_paper": False,
    }[field]
    for alias in aliases:
        body = {"source": {alias: valid}} if nested else {alias: valid}
        constraints, supplied = parse_monitor_position_constraints(body)
        assert supplied is True
        assert constraints == {field: valid}


@pytest.mark.parametrize("value", [None, "", "   "])
@pytest.mark.parametrize(
    "alias",
    [
        alias
        for aliases in POSITION_CONSTRAINT_ALIASES.values()
        for alias in aliases
    ],
)
def test_every_explicit_sparse_alias_fails_closed(alias, value):
    with pytest.raises(PositionSelectionError) as exc_info:
        parse_monitor_position_constraints({alias: value})
    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    ("field", "alias", "invalid"),
    [
        (
            field,
            alias,
            {
                "option_type": "calls",
                "strike": True,
                "expiration": "16-10-2026",
                "account_id": ["acct-a"],
                "contract_id": {"id": "contract"},
                "instrument_id": 42,
                "is_paper": 1,
            }[field],
        )
        for field, aliases in POSITION_CONSTRAINT_ALIASES.items()
        for alias in aliases
    ],
)
def test_every_alias_preserves_malformed_non_empty_values_for_validation(
    field, alias, invalid
):
    with pytest.raises(PositionSelectionError) as exc_info:
        parse_monitor_position_constraints({alias: invalid})
    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    ("field", "left", "right"),
    [
        ("option_type", "call", "put"),
        ("strike", 500, 501),
        ("expiration", "2026-10-16", "2026-11-20"),
        ("account_id", "acct-a", "acct-b"),
        ("contract_id", "contract-a", "contract-b"),
        ("instrument_id", "instrument-a", "instrument-b"),
        ("is_paper", False, True),
    ],
)
def test_top_level_and_source_constraint_conflicts_fail_closed(field, left, right):
    aliases = POSITION_CONSTRAINT_ALIASES[field]
    with pytest.raises(PositionSelectionError, match="Conflicting aliases") as exc_info:
        parse_monitor_position_constraints(
            {aliases[0]: left, "source": {aliases[-1]: right}}
        )
    assert exc_info.value.status_code == 400


class _Cosmos:
    def __init__(self, doc):
        self.doc = doc

    def get_symbol(self, symbol):
        assert symbol == "MSFT"
        return self.doc

    def get_symbols_with_active_positions(self, option_type):
        positions = [
            position for position in self.doc["positions"]
            if position.get("type") == option_type
            and position.get("status") == "active"
        ]
        return [{**self.doc, "_active_positions": positions}]


class _Runner:
    def __init__(self):
        self.positions = []

    async def run_position_monitor(self, **kwargs):
        self.positions.append(kwargs["position"])


class _Config:
    max_activity_entries = 2
    yfinance_config = None
    yfinance_randomize_symbols = False

    @staticmethod
    def model_for(_name):
        return "test"


def test_monitor_wrapper_runs_only_requested_position_and_scheduler_runs_both(monkeypatch):
    positions = [
        _position("pos-a", account_id="acct-a"),
        _position("pos-b", account_id="acct-b", is_paper=True),
    ]
    cosmos = _Cosmos(_symbol(*positions))
    runner = _Runner()
    monkeypatch.setattr(
        "src.yfinance_data_provider.get_shared_provider", lambda _config: object()
    )

    asyncio.run(run_open_call_monitor(
        _Config(), runner, cosmos, object(),
        symbol="MSFT",
        position_id="pos-b",
        position_constraints={"account_id": "acct-b", "is_paper": True},
    ))
    assert [position["position_id"] for position in runner.positions] == ["pos-b"]

    runner.positions.clear()
    asyncio.run(run_open_call_monitor(_Config(), runner, cosmos, object()))
    assert [position["position_id"] for position in runner.positions] == [
        "pos-a", "pos-b",
    ]


def test_assessment_prompt_contains_complete_authoritative_position_identity(monkeypatch):
    captured = {}

    class _Result:
        text = '{"activity":"WAIT"}'

    class _Agent:
        def __init__(self, **_kwargs):
            pass

        async def run(self, message):
            captured["message"] = message
            return _Result()

    runner = AgentRunner.__new__(AgentRunner)
    monkeypatch.setattr("src.agent_runner.Agent", _Agent)
    monkeypatch.setattr(runner, "_get_client", lambda *_args: object())
    monkeypatch.setattr(runner, "_get_skills_provider", lambda *_args: None)
    monkeypatch.setattr(runner, "_record_trace", lambda *_args, **_kwargs: None)

    asyncio.run(runner._run_position_assessment(
        name="OpenCallMonitor",
        instructions="test",
        symbol="MSFT",
        exchange="NASDAQ",
        position_type="call",
        strike=500,
        expiration="2026-10-16",
        data={"overview": "o", "technicals": "t", "forecast": "f"},
        previous_context="none",
        analysis_ts="2026-09-24T22:00:00Z",
        position_identity={
            "position_id": "pos-b",
            "option_type": "call",
            "strike": 500,
            "expiration": "2026-10-16",
            "quantity": 7,
            "account_id": "acct-b",
            "is_paper": True,
        },
    ))

    message = captured["message"]
    for expected in (
        '"position_id": "pos-b"',
        '"option_type": "call"',
        '"strike": 500',
        '"expiration": "2026-10-16"',
        '"quantity": 7',
        '"account_id": "acct-b"',
        '"is_paper": true',
    ):
        assert expected in message


def _client(monkeypatch, positions):
    scheduler = SimpleNamespace(
        config=object(),
        runner=object(),
        cosmos=_Cosmos(_symbol(*positions)),
        context_provider=object(),
    )
    monkeypatch.setattr(web_app.app.state, "scheduler", scheduler, raising=False)
    monkeypatch.setattr(
        web_app.app.state,
        "_dashboard_process_state",
        _DashboardProcessState(),
        raising=False,
    )
    return TestClient(web_app.app)


def test_manual_endpoint_rejects_ambiguous_symbol_only_and_mismatched_id(monkeypatch):
    positions = [
        _position("pos-a", account_id="acct-a"),
        _position("pos-b", account_id="acct-b", is_paper=True),
    ]
    client = _client(monkeypatch, positions)

    ambiguous = client.post(
        "/api/trigger/open_call_monitor", json={"symbol": "MSFT"}
    )
    assert ambiguous.status_code == 409
    assert "position_id is required" in ambiguous.json()["error"]

    mismatch = client.post(
        "/api/trigger/open_call_monitor",
        json={
            "symbol": "MSFT",
            "position_id": "pos-a",
            "account_id": "acct-b",
        },
    )
    assert mismatch.status_code == 409
    assert "does not match" in mismatch.json()["error"]

    missing_symbol = client.post(
        "/api/trigger/open_call_monitor", json={"position_id": "pos-a"}
    )
    assert missing_symbol.status_code == 400
    assert "symbol is required" in missing_symbol.json()["error"]


@pytest.mark.parametrize("value", [None, "", "   "])
def test_manual_endpoint_rejects_explicit_invalid_position_id(monkeypatch, value):
    client = _client(monkeypatch, [_position("pos-a", account_id="acct-a")])
    response = client.post(
        "/api/trigger/open_call_monitor",
        json={"symbol": "MSFT", "position_id": value},
    )
    assert response.status_code == 400
    assert "position_id must be a non-empty string" in response.json()["error"]


@pytest.mark.parametrize(
    "field",
    [
        "option_type",
        "strike",
        "expiration",
        "account_id",
        "contract_id",
        "instrument_id",
        "is_paper",
    ],
)
@pytest.mark.parametrize("value", [None, "", "   "])
def test_manual_endpoint_rejects_supplied_null_blank_and_whitespace_constraints(
    monkeypatch, field, value
):
    client = _client(monkeypatch, [_position("pos-a", account_id="acct-a")])
    response = client.post(
        "/api/trigger/open_call_monitor",
        json={
            "symbol": "MSFT",
            "position_id": "pos-a",
            field: value,
        },
    )
    assert response.status_code == 400


def test_manual_endpoint_requires_position_id_when_any_constraint_is_present(
    monkeypatch,
):
    client = _client(monkeypatch, [_position("pos-a", account_id="acct-a")])
    response = client.post(
        "/api/trigger/open_call_monitor",
        json={"symbol": "MSFT", "account_id": "acct-a"},
    )
    assert response.status_code == 400
    assert "position_id is required" in response.json()["error"]

    alias_response = client.post(
        "/api/trigger/open_call_monitor",
        json={"symbol": "MSFT", "source": {"account": "acct-a"}},
    )
    assert alias_response.status_code == 400
    assert "position_id is required" in alias_response.json()["error"]

    symbol_response = client.post(
        "/api/trigger/open_call_monitor",
        json={"position_id": "pos-a", "contract_symbol": "contract-a"},
    )
    assert symbol_response.status_code == 400
    assert "symbol is required" in symbol_response.json()["error"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("option_type", "calls"),
        ("strike", True),
        ("expiration", "October 16"),
        ("account_id", ["acct-a"]),
        ("contract_id", {"id": "contract"}),
        ("instrument_id", 42),
        ("is_paper", 1),
    ],
)
def test_manual_endpoint_rejects_malformed_constraints(monkeypatch, field, value):
    client = _client(monkeypatch, [_position("pos-a", account_id="acct-a")])
    response = client.post(
        "/api/trigger/open_call_monitor",
        json={
            "symbol": "MSFT",
            "position_id": "pos-a",
            field: value,
        },
    )
    assert response.status_code == 400


def test_manual_endpoint_rejects_option_type_mismatch(monkeypatch):
    client = _client(monkeypatch, [_position("pos-a", account_id="acct-a")])
    response = client.post(
        "/api/trigger/open_call_monitor",
        json={
            "symbol": "MSFT",
            "position_id": "pos-a",
            "option_type": " PUT ",
        },
    )
    assert response.status_code == 409
    assert "option_type does not match" in response.json()["error"]


def test_manual_endpoint_unique_legacy_symbol_request_resolves_position(monkeypatch):
    client = _client(
        monkeypatch, [_position("pos-a", account_id="acct-a")]
    )
    completed = threading.Event()
    calls = []

    async def fake_monitor(
        config, runner, cosmos, context_provider, symbol=None,
        position_id=None, **_kwargs,
    ):
        calls.append((symbol, position_id))
        completed.set()

    monkeypatch.setattr(
        "src.open_call_monitor_agent.run_open_call_monitor", fake_monitor
    )
    response = client.post(
        "/api/trigger/open_call_monitor", json={"symbol": "MSFT"}
    )
    assert response.status_code == 200
    assert response.json()["position_id"] == "pos-a"
    assert completed.wait(timeout=2)
    assert calls == [("MSFT", "pos-a")]


def test_manual_endpoint_forwards_exact_position_and_run_status_identity(monkeypatch):
    positions = [
        _position("pos-a", account_id="acct-a"),
        _position("pos-b", account_id="acct-b", is_paper=True),
    ]
    client = _client(monkeypatch, positions)
    calls = []
    completed = threading.Event()

    async def fake_monitor(
        config, runner, cosmos, context_provider, symbol=None,
        position_id=None, position_constraints=None, **_kwargs,
    ):
        calls.append((symbol, position_id, position_constraints))
        completed.set()

    monkeypatch.setattr(
        "src.open_call_monitor_agent.run_open_call_monitor", fake_monitor
    )
    response = client.post(
        "/api/trigger/open_call_monitor",
        json={
            "symbol": "MSFT",
            "position_id": "pos-b",
            "option_type": "call",
            "strike": 500,
            "expiration": "2026-10-16",
            "account_id": "acct-b",
            "contract_id": "MSFT-identical-contract",
            "instrument_id": "instrument-msft",
            "is_paper": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["position_id"] == "pos-b"
    assert completed.wait(timeout=2)
    assert calls[0][0:2] == ("MSFT", "pos-b")
    assert calls[0][2] == {
        "option_type": "call",
        "strike": 500,
        "expiration": "2026-10-16",
        "account_id": "acct-b",
        "contract_id": "MSFT-identical-contract",
        "instrument_id": "instrument-msft",
        "is_paper": True,
    }

    deadline = time.time() + 2
    run = {}
    while time.time() < deadline:
        run = client.get("/api/dashboard/status").json()["runs"][
            response.json()["run_id"]
        ]
        if run["status"] == "succeeded":
            break
        time.sleep(0.02)
    assert run["position_id"] == "pos-b"


@pytest.mark.parametrize(
    ("agent_type", "option_type", "runner_path"),
    [
        (
            "open_call_monitor",
            "call",
            "src.open_call_monitor_agent.run_open_call_monitor",
        ),
        (
            "open_put_monitor",
            "put",
            "src.open_put_monitor_agent.run_open_put_monitor",
        ),
    ],
)
def test_manual_monitor_endpoints_accept_nested_alias_identity(
    monkeypatch, agent_type, option_type, runner_path
):
    position = _position("pos-a", account_id="acct-a")
    position["type"] = option_type
    client = _client(monkeypatch, [position])
    calls = []
    completed = threading.Event()

    async def fake_monitor(
        config, runner, cosmos, context_provider, symbol=None,
        position_id=None, position_constraints=None, **_kwargs,
    ):
        calls.append((symbol, position_id, position_constraints))
        completed.set()

    monkeypatch.setattr(runner_path, fake_monitor)
    response = client.post(
        f"/api/trigger/{agent_type}",
        json={
            "symbol": "MSFT",
            "position_id": "pos-a",
            "source": {
                "account": "acct-a",
                "contract_symbol": "MSFT-identical-contract",
                "instrument_identifier": "instrument-msft",
            },
        },
    )

    assert response.status_code == 200
    assert completed.wait(timeout=2)
    assert calls == [
        (
            "MSFT",
            "pos-a",
            {
                "account_id": "acct-a",
                "contract_id": "MSFT-identical-contract",
                "instrument_id": "instrument-msft",
            },
        )
    ]


def test_manual_endpoint_locks_concurrently_per_position(monkeypatch):
    positions = [
        _position("pos-a", account_id="acct-a"),
        _position("pos-b", account_id="acct-b", is_paper=True),
    ]
    client = _client(monkeypatch, positions)
    started = {"pos-a": threading.Event(), "pos-b": threading.Event()}
    release = threading.Event()

    async def fake_monitor(
        config, runner, cosmos, context_provider, symbol=None,
        position_id=None, **_kwargs,
    ):
        started[position_id].set()
        assert release.wait(timeout=3)

    monkeypatch.setattr(
        "src.open_call_monitor_agent.run_open_call_monitor", fake_monitor
    )

    first = client.post(
        "/api/trigger/open_call_monitor",
        json={"symbol": "MSFT", "position_id": "pos-a"},
    )
    second = client.post(
        "/api/trigger/open_call_monitor",
        json={"symbol": "MSFT", "position_id": "pos-b"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert started["pos-a"].wait(timeout=2)
    assert started["pos-b"].wait(timeout=2)

    duplicate = client.post(
        "/api/trigger/open_call_monitor",
        json={"symbol": "MSFT", "position_id": "pos-a"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["position_id"] == "pos-a"
    release.set()

    deadline = time.time() + 2
    statuses = {}
    while time.time() < deadline:
        runs = client.get("/api/dashboard/status").json()["runs"]
        statuses = {
            response.json()["position_id"]: runs[response.json()["run_id"]]["status"]
            for response in (first, second)
        }
        if set(statuses.values()) == {"succeeded"}:
            break
        time.sleep(0.02)
    assert statuses == {"pos-a": "succeeded", "pos-b": "succeeded"}
