"""
Platform / Admin / HITL action tests (Adel).

Verifies:
  - Safety search strategy ranking
  - Node pure logic (evidence validation, HITL decisions)
  - Required files exist
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    """Load a module file without importing the full package tree."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_safety_search_strategy_ranks_critical():
    mod = _load_module(
        "safety_search_strategy",
        ROOT / "state_graph" / "safety" / "search_strategy.py",
    )
    result = mod.explore_reporting_paths(
        severity="critical",
        incident_type="smoke_or_fire",
        has_passenger_impact=True,
    )
    assert result["recommended_path_id"] in {
        "national_authority",
        "authority_and_icao",
    }
    assert "candidates" in result


def test_safety_search_strategy_low():
    mod = _load_module(
        "safety_search_strategy",
        ROOT / "state_graph" / "safety" / "search_strategy.py",
    )
    result = mod.explore_reporting_paths(
        severity="low",
        incident_type="other",
        has_passenger_impact=False,
    )
    assert result["recommended_path_id"] == "internal_ops_only"


def test_safety_sql_exists():
    sql = ROOT / "database" / "004_safety_incidents.sql"
    assert sql.is_file()
    text = sql.read_text()
    assert "safety_incidents" in text
    assert "safety_authority_submissions" in text
    assert "safety_incident" in text

def test_platform_frontend_files_exist():
    base = ROOT / "web_platform" / "frontend"
    assert (base / "index.html").is_file()
    assert (base / "app.js").is_file()
    assert (base / "styles.css").is_file()

def test_platform_backend_files_exist():
    base = ROOT / "web_platform" / "backend"
    for name in ("app.py", "routes_agents.py", "routes_admin.py", "services.py"):
        assert (base / name).is_file(), name


def test_safety_package_files_exist():
    base = ROOT / "state_graph" / "safety"
    for name in ("__init__.py", "graph.py", "nodes.py", "search_strategy.py"):
        assert (base / name).is_file(), name


def test_rag_has_safety_policies():
    text = (ROOT / "rag" / "policy_corpus.py").read_text()
    assert "IROPS-SAFE-1" in text
    assert 'category="safety"' in text


def test_node_logic_with_stubs():
    """Exercise validate_evidence / safety_manager_review with lightweight stubs."""
    models = types.ModuleType("state_graph.models")

    class RunStatus:
        RUNNING = type("E", (), {"value": "running"})()
        WAITING_EXTERNAL = type("E", (), {"value": "waiting_external"})()
        WAITING_ADMIN = type("E", (), {"value": "waiting_admin"})()
        FAILED = type("E", (), {"value": "failed"})()
        COMPLETED = type("E", (), {"value": "completed"})()
        CANCELLED = type("E", (), {"value": "cancelled"})()

        def __init__(self, value):
            self.value = value

    class WorkflowState:
        def __init__(self, **kw):
            self.run_id = kw.get("run_id", "test-run")
            self.workflow_type = kw.get("workflow_type", "safety_incident")
            self.current_node = kw.get("current_node", "validate_evidence")
            self.status = kw.get("status", RunStatus.RUNNING)
            self.flight_number = kw.get("flight_number", "BH202")
            self.data = kw.get("data", {})
            self.context = {}
            self.waiting_for = None
            self.admin_task_id = None
            self.last_error = None
            self.checkpoint_number = 0

        @classmethod
        def create(cls, **kw):
            return cls(**kw)

        def to_dict(self):
            return {"run_id": self.run_id, "data": self.data}

    models.RunStatus = RunStatus
    models.WorkflowState = WorkflowState

    runner = types.ModuleType("state_graph.runner")

    class NodeResult:
        def __init__(self, next_node, transition_name, status=None, waiting_for=None):
            self.next_node = next_node
            self.transition_name = transition_name
            self.status = status or RunStatus.RUNNING
            self.waiting_for = waiting_for

    runner.NodeResult = NodeResult

    search = types.ModuleType("state_graph.safety.search_strategy")
    search.explore_reporting_paths = lambda **kw: {
        "recommended_path_id": "national_authority",
        "candidates": [],
        "method": "test",
    }

    sys.modules["state_graph"] = types.ModuleType("state_graph")
    sys.modules["state_graph.models"] = models
    sys.modules["state_graph.runner"] = runner
    sys.modules["state_graph.safety"] = types.ModuleType("state_graph.safety")
    sys.modules["state_graph.safety.search_strategy"] = search

    nodes = _load_module(
        "safety_nodes_under_test",
        ROOT / "state_graph" / "safety" / "nodes.py",
    )

    st = WorkflowState.create(
        current_node="validate_evidence",
        data={"incident_type": "bird_strike", "severity": "medium"},
    )
    res = nodes.validate_evidence(st)
    assert res.status == RunStatus.FAILED
    assert st.data.get("failure_ticket_id")

    st2 = WorkflowState.create(
        current_node="validate_evidence",
        data={
            "incident_type": "bird_strike",
            "severity": "medium",
            "ground_report": {"location": "rwy", "timestamp": "t1"},
            "crew_report": {"location": "rwy", "timestamp": "t1"},
            "flight_status": {"status": "diverted"},
            "recommended_path_id": "national_authority",
        },
    )
    res2 = nodes.validate_evidence(st2)
    assert res2.status == RunStatus.RUNNING
    assert res2.next_node == "draft_regulatory_report"

    st3 = WorkflowState.create(
        current_node="safety_manager_review",
        data={"draft_report": "# Draft"},
    )
    res3 = nodes.safety_manager_review(st3)
    assert res3.status == RunStatus.WAITING_ADMIN

    st4 = WorkflowState.create(
        current_node="safety_manager_review",
        data={"draft_report": "# Draft", "admin_decision": "approved"},
    )
    res4 = nodes.safety_manager_review(st4)
    assert res4.next_node == "submit_report"

    st5 = WorkflowState.create(
        current_node="safety_manager_review",
        data={
            "draft_report": "# Draft",
            "admin_decision": "changes_requested",
            "admin_comment": "Add detail",
        },
    )
    res5 = nodes.safety_manager_review(st5)
    assert res5.next_node == "revise_report"


if __name__ == "__main__":
    import traceback

    tests = [
        name
        for name, obj in list(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    passed = failed = 0
    for name in tests:
        try:
            globals()[name]()
            print(f"PASS  {name}")
            passed += 1
        except Exception:
            print(f"FAIL  {name}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
