"""Livingston's Cosmos round-trip/correlation lock for the Supervisor/Alpha
trace design (`.squad/decisions/inbox/danny-supervisor-alpha-traces-design.md`,
ACCEPTED), owned item #2/#7 of that design's file table:
`cosmos_db.py::write_agent_trace` honoring a caller-supplied `id`,
`list_agent_traces`'s lightweight projection exposing `run_id`/
`parent_trace_id`, and a real round-trip proving a parent
(analysis/assessment/roll) trace plus its Supervisor/Alpha children survive
storage correlated by `run_id` and chained by `parent_trace_id`.

Follows the `test_cosmos_close.py` fake-container pattern
(`CosmosDBService.__new__` + a fake in place of the real Cosmos SDK
container) but goes one step further: `FakeAgentTracesContainer` below
actually stores documents and evaluates the real SQL-ish query text
`cosmos_db.py` issues (WHERE/ORDER BY/LIMIT), so this is a genuine
write-then-read round trip through the production query strings, not an
assertion against a mock's call arguments.

No DDL/retention/settings change: `AGENT_TRACE_TTL_SECONDS` (90 days) and
the `agent_traces` container/partition are reused completely unchanged --
asserted below, not just claimed. Does not touch `agent_runner.py` (Rusty's
runner instrumentation, not yet landed at the time of this test) or any
refresh_all/watchdog surface -- this file exercises `CosmosDBService`
directly, at the write/list/read seam only.
"""
from __future__ import annotations

import re

from src.cosmos_db import CosmosDBService


class FakeAgentTracesContainer:
    """Minimal in-memory Cosmos-like container. Not a general SQL engine --
    it understands exactly the query shapes `cosmos_db.py`'s agent-trace
    methods issue: `SELECT <cols|*|VALUE COUNT(1)> FROM c WHERE <ANDed
    equality/comparison clauses> [ORDER BY c.timestamp DESC] [OFFSET 0 LIMIT
    @limit]`. Deliberately generic (parses the real query text rather than
    hardcoding expected results) so this test proves the *production*
    projection/filter logic behaves correctly, not a restatement of it.
    """

    def __init__(self):
        self.docs: list[dict] = []

    def create_item(self, doc):
        stored = dict(doc)
        self.docs.append(stored)
        return dict(stored)

    def query_items(self, query, parameters=None, enable_cross_partition_query=True):
        params = {p["name"]: p["value"] for p in (parameters or [])}

        select_clause = re.search(r"SELECT\s+(.*?)\s+FROM\s+c\b", query, re.IGNORECASE).group(1).strip()
        where_match = re.search(r"WHERE\s+(.*?)(?:\s+ORDER BY|\s+OFFSET|$)", query, re.IGNORECASE)
        where_clause = where_match.group(1).strip() if where_match else None

        rows = [d for d in self.docs if self._matches(d, where_clause, params)]

        if re.search(r"ORDER BY\s+c\.timestamp\s+DESC", query, re.IGNORECASE):
            rows = sorted(rows, key=lambda d: d.get("timestamp", ""), reverse=True)

        limit_match = re.search(r"LIMIT\s+@(\w+)", query, re.IGNORECASE)
        if limit_match:
            rows = rows[: int(params[f"@{limit_match.group(1)}"])]

        if select_clause.upper().startswith("VALUE COUNT"):
            return [len(rows)]
        if select_clause.strip() == "*":
            return [dict(r) for r in rows]

        fields = [c.strip().split(".", 1)[1] for c in select_clause.split(",")]
        return [{f: r.get(f) for f in fields} for r in rows]

    @staticmethod
    def _matches(doc, where_clause, params):
        if not where_clause:
            return True
        for clause in re.split(r"\s+AND\s+", where_clause, flags=re.IGNORECASE):
            clause = clause.strip()
            m = re.match(r"c\.(\w+)\s*=\s*'([^']*)'", clause)
            if m:
                field, literal = m.groups()
                if doc.get(field) != literal:
                    return False
                continue
            m = re.match(r"c\.(\w+)\s*=\s*true", clause, re.IGNORECASE)
            if m:
                if doc.get(m.group(1)) is not True:
                    return False
                continue
            m = re.match(r"c\.(\w+)\s*=\s*@(\w+)", clause)
            if m:
                field, param_name = m.groups()
                if doc.get(field) != params.get(f"@{param_name}"):
                    return False
                continue
            m = re.match(r"c\.(\w+)\s*<\s*@(\w+)", clause)
            if m:
                field, param_name = m.groups()
                if not (doc.get(field, "") < params.get(f"@{param_name}", "")):
                    return False
                continue
            raise AssertionError(f"FakeAgentTracesContainer: unsupported WHERE clause: {clause!r}")
        return True


def _service():
    """A `CosmosDBService` with only `agent_traces_container` wired up --
    `_ensure_agent_traces_container` returns it directly without ever
    touching `self.database` (mirrors the already-acquired-at-startup path)."""
    service = CosmosDBService.__new__(CosmosDBService)
    service.agent_traces_container = FakeAgentTracesContainer()
    return service


class TestWriteAgentTraceIdHandling:
    """§2 of the design: honor a caller-supplied `id`, retain the generated
    UUID fallback -- fully backward compatible with any caller that never
    passes one."""

    def test_honors_caller_supplied_id(self):
        service = _service()
        written = service.write_agent_trace(
            {"id": "caller-id-123", "symbol": "AAPL", "agent_type": "covered_call"}
        )
        assert written["id"] == "caller-id-123"
        assert service.agent_traces_container.docs[0]["id"] == "caller-id-123"

    def test_generates_uuid_when_id_absent(self):
        service = _service()
        first = service.write_agent_trace({"symbol": "AAPL", "agent_type": "covered_call"})
        second = service.write_agent_trace({"symbol": "AAPL", "agent_type": "covered_call"})
        assert first["id"] and second["id"]
        assert first["id"] != second["id"]  # never collide across two auto-generated writes
        assert first["id"] != "caller-id-123"

    def test_ttl_and_doc_shape_unchanged(self):
        """No retention/DDL change: the 90-day TTL and container/doc_type
        contract are reused exactly as before caller-supplied ids existed."""
        service = _service()
        written = service.write_agent_trace({"id": "trace-ttl-check", "symbol": "AAPL"})
        assert written["ttl"] == CosmosDBService.AGENT_TRACE_TTL_SECONDS == 7776000
        assert written["doc_type"] == "agent_trace"


class TestListAgentTracesProjection:
    """§7 of the design: the lightweight `list_agent_traces` projection must
    expose `run_id`/`parent_trace_id`; `get_agent_trace`'s `SELECT *` already
    returns everything and needs no change, verified here anyway as the
    round-trip's other half."""

    def test_run_id_and_parent_trace_id_in_lightweight_projection(self):
        service = _service()
        service.write_agent_trace({
            "id": "parent-1", "symbol": "AAPL", "agent_type": "covered_call",
            "phase": "analysis", "run_id": "run-abc", "parent_trace_id": None,
        })
        rows = service.list_agent_traces(symbol="AAPL")
        assert len(rows) == 1
        assert rows[0]["id"] == "parent-1"
        assert rows[0]["run_id"] == "run-abc"
        assert rows[0]["parent_trace_id"] is None

    def test_get_agent_trace_surfaces_full_detail_untruncated(self):
        service = _service()
        long_prompt = "x" * 5000  # design §6: no truncation of prompt/response fields
        service.write_agent_trace({
            "id": "trace-full", "symbol": "AAPL", "agent_type": "covered_call",
            "phase": "supervisor", "run_id": "run-xyz", "parent_trace_id": "parent-1",
            "system_prompt": long_prompt, "response_text": "raw response",
            "error": "no_parseable_json",
        })
        full = service.get_agent_trace("trace-full")
        assert full["run_id"] == "run-xyz"
        assert full["parent_trace_id"] == "parent-1"
        assert full["error"] == "no_parseable_json"
        assert full["system_prompt"] == long_prompt
        assert len(full["system_prompt"]) == 5000


class TestRunIdCorrelationAcrossParentAndChildTraces:
    """The cross-seam check this file owns (design §10, item #7): a parent
    phase trace plus its Supervisor/Alpha children survive storage together,
    correlated by `run_id` and chained by `parent_trace_id` -- the seam
    between the activity write path (which will carry `run_id`, Rusty's
    side) and the trace read path (this file's side). A reader filters the
    full trace list by `run_id` client-side on the lightweight rows list
    already returns -- exactly as `AgentLogsView.tsx` would -- since
    `list_agent_traces` has no dedicated `run_id` query param of its own
    (out of this decision's scope)."""

    def test_analysis_parent_plus_supervisor_and_alpha_children_share_run_id(self):
        service = _service()
        run_id = "run-2026-08-29-001"

        analysis_id = service.write_agent_trace({
            "id": "trace-analysis", "symbol": "AAPL", "agent_type": "covered_call",
            "phase": "analysis", "run_id": run_id, "parent_trace_id": None,
        })["id"]
        service.write_agent_trace({
            "id": "trace-supervisor", "symbol": "AAPL", "agent_type": "covered_call",
            "phase": "supervisor", "run_id": run_id, "parent_trace_id": analysis_id,
        })
        service.write_agent_trace({
            "id": "trace-alpha", "symbol": "AAPL", "agent_type": "covered_call",
            "phase": "alpha", "run_id": run_id, "parent_trace_id": analysis_id,
        })
        # A trace from an unrelated decision cycle must not leak into the
        # correlated set.
        service.write_agent_trace({
            "id": "trace-other", "symbol": "AAPL", "agent_type": "covered_call",
            "phase": "analysis", "run_id": "run-unrelated", "parent_trace_id": None,
        })

        all_rows = service.list_agent_traces(symbol="AAPL", limit=100)
        this_run = [r for r in all_rows if r["run_id"] == run_id]
        assert {r["phase"] for r in this_run} == {"analysis", "supervisor", "alpha"}
        assert {r["id"] for r in this_run} == {"trace-analysis", "trace-supervisor", "trace-alpha"}

        children = [r for r in this_run if r["phase"] in ("supervisor", "alpha")]
        assert all(r["parent_trace_id"] == analysis_id for r in children)
        parent_row = next(r for r in this_run if r["phase"] == "analysis")
        assert parent_row["parent_trace_id"] is None

    def test_roll_parent_plus_supervisor_alpha_children_share_run_id(self):
        """2-phase monitor path: assessment -> roll -> supervisor/alpha (design
        §2: "parent_trace_id ... the roll phase's trace id if a roll
        happened this cycle" -- Supervisor/Alpha review the decision that
        was actually made, not the assessment that preceded it)."""
        service = _service()
        run_id = "run-2026-08-29-roll-001"

        assessment_id = service.write_agent_trace({
            "id": "trace-assessment", "symbol": "MSFT", "agent_type": "open_call_monitor",
            "phase": "assessment", "run_id": run_id, "parent_trace_id": None,
        })["id"]
        roll_id = service.write_agent_trace({
            "id": "trace-roll", "symbol": "MSFT", "agent_type": "open_call_monitor",
            "phase": "roll", "run_id": run_id, "parent_trace_id": assessment_id,
        })["id"]
        service.write_agent_trace({
            "id": "trace-supervisor-roll", "symbol": "MSFT", "agent_type": "open_call_monitor",
            "phase": "supervisor", "run_id": run_id, "parent_trace_id": roll_id,
        })
        service.write_agent_trace({
            "id": "trace-alpha-roll", "symbol": "MSFT", "agent_type": "open_call_monitor",
            "phase": "alpha", "run_id": run_id, "parent_trace_id": roll_id,
        })

        rows = service.list_agent_traces(symbol="MSFT")
        by_phase = {r["phase"]: r for r in rows}
        assert by_phase["assessment"]["parent_trace_id"] is None
        assert by_phase["roll"]["parent_trace_id"] == assessment_id
        # Supervisor/Alpha point at the ROLL trace, not the assessment trace.
        assert by_phase["supervisor"]["parent_trace_id"] == roll_id
        assert by_phase["alpha"]["parent_trace_id"] == roll_id
        # The original, unmapped agent_type survives the round trip (design
        # §1: open_call_monitor must never leak as its remapped "open_call").
        assert {r["agent_type"] for r in rows} == {"open_call_monitor"}
