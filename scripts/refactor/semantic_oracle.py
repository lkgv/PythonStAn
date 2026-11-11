"""Independent bounded semantic acceptance helpers.

Only the deliberately authored finite fixture sources are executed, in a fresh
CPython child. Concrete outputs never enter the analyzer. This is a regression
oracle, not a proof of arbitrary Python soundness.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Mapping


REPO = Path(__file__).resolve().parents[2]
GOLD_PATH = REPO / "docs/refactor/query-gold.json"
HELDOUT_PATH = REPO / "tests/refactor/semantics/heldout_cases.json"

# No analyzer modules or results are available to this process. The fixture
# corpus is authored and finite; never use this helper on an arbitrary project.
CONCRETE_DRIVER = r'''
import json, resource, sys
resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
request = json.load(sys.stdin)
env = {"__name__": "__finite_semantic_witness__"}
env.update(request.get("bindings", {}))
def describe(value):
    if value is None or type(value) in (bool, int, float, str):
        return {"kind": "scalar", "value": value, "type": type(value).__name__}
    if type(value) in (list, tuple):
        return {"kind": type(value).__name__, "items": [describe(v) for v in value]}
    if type(value) is dict:
        return {"kind": "dict", "items": [[describe(k), describe(v)] for k, v in value.items()]}
    return {"kind": "object", "type": type(value).__name__}
try:
    exec(compile(request["source"], "<trusted-finite-semantic-witness>", "exec"), env)
    symbol = request.get("symbol", "result")
    out = {"normal_exit": True, "bound": symbol in env}
    if symbol in env:
        out["value"] = describe(env[symbol])
except BaseException as error:
    out = {"normal_exit": False, "exception": type(error).__name__}
print(json.dumps(out, sort_keys=True))
'''


def token(value: object) -> str:
    """Preserve bool/int distinctions independently of production lattice code."""
    return type(value).__name__ + ":" + json.dumps(value, sort_keys=True)


def load_gold() -> dict:
    data = json.loads(GOLD_PATH.read_text())
    assert data["status"] == "frozen", "TEST-01 requires frozen SPEC-01 gold"
    return data


def source_fixtures(*, heldout: bool = True) -> list[dict]:
    fixtures = [c for c in load_gold()["mandatory_supported_fixtures"] if c["kind"] == "source"]
    if heldout:
        fixtures.extend(json.loads(HELDOUT_PATH.read_text())["cases"])
    ids = [c["id"] for c in fixtures]
    assert len(ids) == len(set(ids)), "fixture identity collision"
    return fixtures


def concrete_witness(case: Mapping[str, object], *, bindings=None) -> dict:
    """Run one trusted source fixture without importing the analyzer."""
    source = case["source"]
    assert isinstance(source, str)
    command = [sys.executable, "-I", "-c", CONCRETE_DRIVER]
    started = time.perf_counter()
    completed = subprocess.run(
        command, input=json.dumps({"source": source, "symbol": "result", "bindings": bindings or {}}),
        text=True, capture_output=True, timeout=4,
    )
    evidence = {
        "command": command, "stdin_source_id": case["id"],
        "stdout": completed.stdout, "stderr": completed.stderr,
        "exit_code": completed.returncode, "elapsed_seconds": time.perf_counter() - started,
        "timeout_seconds": 4,
        "external_concrete_bindings": bindings or {},
    }
    assert completed.returncode == 0, evidence
    evidence["observation"] = json.loads(completed.stdout)
    return evidence


def assert_concrete_gold(case: Mapping[str, object], witness: Mapping[str, object]) -> None:
    """Check independently authored expectations before comparing the solver."""
    expected = case["expected"]
    observed = witness["observation"]
    if expected.get("normal_exit") is False:
        assert observed["normal_exit"] is False
        assert observed["exception"] in expected["exceptions"]
        return
    assert observed["normal_exit"] is True, observed
    value = observed["value"]
    if "constants" in expected or "must_include" in expected:
        assert value["kind"] == "scalar", value
        allowed = expected.get("constants", expected.get("must_include"))
        assert token(value["value"]) in {token(x) for x in allowed}, (case["id"], value, expected)
    if "container_items" in expected:
        assert value["kind"] in ("list", "tuple")
        actual_items = [item["value"] for item in value["items"]]
        assert actual_items == expected["container_items"]
    if "result_types" in expected:
        assert value.get("type", value["kind"]) in expected["result_types"]


def assert_scalar_snapshot(value: Mapping[str, object], expected: Mapping[str, object]) -> None:
    assert value["top"] is False, "All-Top cannot satisfy useful semantic gold"
    assert value["bottom"] is False, "A reachable scalar result cannot be bottom"
    constants = {token(x) for x in value["constants"]}
    if "constants" in expected:
        desired = {token(x) for x in expected["constants"]}
        assert desired <= constants, (value, expected)
        if expected.get("exact"):
            assert constants == desired, (value, expected)
            assert not value["objects"], "An exact scalar answer cannot contain extra objects"
    assert {token(x) for x in expected.get("must_include", [])} <= constants, (value, expected)
    assert not ({token(x) for x in expected.get("must_exclude", [])} & constants)


def analyze_fixture(case: Mapping[str, object], *, engine: str = "ai_primary", validate: bool = True):
    """Public API only; no Native.constants or private solver-state fallback."""
    from pythonstan import Source, ValueQuery, analyze

    query_spec = case.get("query", {})
    query = ValueQuery(
        module=query_spec.get("module", case.get("module", "example")),
        symbol=query_spec.get("symbol", "result"),
        point=query_spec.get("point", "module_exit"),
    )
    config = {"schema_version": 1, "engine": engine,
              "semantic_profile": case.get("profile", "python-core-v1")}
    bindings = {}
    for name, contract in case.get("input_contract", {}).items():
        if contract != "all strings; external binding":
            raise AssertionError("Unimplemented frozen input contract: " + repr(contract))
        bindings[case.get("module", "example") + "." + name] = {"type": "str", "top": True}
    if bindings:
        config["inputs"] = {"bindings": bindings}
    result = analyze(
        Source(case["source"], filename=case.get("filename", case["id"] + ".py"),
               module=case.get("module", "example")),
        config=config,
        queries=(query, "returns", "effects", "outcomes"),
    )
    answer = result.query(query)
    if validate:
        assert_analysis_gold(case, result, answer)
    return result, answer


def assert_analysis_gold(case, result, answer) -> None:
    """Evaluate already frozen public answers; never schedules another run."""
    query = answer.to_dict()["query"]
    expected = case["expected"]
    if expected.get("coverage_status") == "open":
        assert result.coverage_status == "open", result.to_dict()
        assert result.raw_snapshot()["obligations"], "Open boundary must retain actual obligations"
        assert answer.status in ("answered", "unavailable"), answer.to_dict()
        if answer.status == "answered":
            assert answer.value["top"] is True, "Unbounded dynamic code cannot retain a finite old value"
        effects = result.query("effects")
        assert effects.status == "answered", effects.to_dict()
        assert effects.value["havoc"] and effects.value["exceptions"], effects.to_dict()
        assert result.proof_status != "verified"
        return
    assert answer.status == "answered", answer.to_dict()
    assert result.solver_status == "converged", result.to_dict()
    if expected.get("allow_open"):
        assert answer.coverage_status in ("closed_under_profile", "open"), answer.to_dict()
        if answer.coverage_status == "open":
            assert result.raw_snapshot()["obligations"], "Unsupported protocol must retain its obligations"
    else:
        assert answer.coverage_status == "closed_under_profile", answer.to_dict()
    assert result.proof_status == "not_requested", "Regression tests never grant Verified"
    value = answer.value
    if "constants" in expected or "must_include" in expected:
        assert_scalar_snapshot(value, expected)
    elif expected.get("normal_exit") is False:
        assert value["bottom"] is True and value["top"] is False, value
        assert None not in value["constants"], "Exception-only function must not acquire None"
        outcomes = result.query("outcomes")
        assert outcomes.status == "answered", outcomes.to_dict()
        name = query["module"] + "." + query["symbol"]
        procedure = outcomes.value[name]
        assert procedure, "An uncalled function's bottom is not proof of exception-only behavior"
        assert all(exit_["kind"] not in ("normal", "return") for exit_ in procedure), procedure
        exceptions = {exit_.get("exception") for exit_ in procedure if exit_["kind"] == "raise"}
        assert set(expected["exceptions"]) <= exceptions, procedure
    elif "container_items" in expected:
        assert value["top"] is False and value["bottom"] is False, value
        assert "items" in value, "Container gold requires a public shape projection"
        assert value["items"] == expected["container_items"], value
    elif "result_types" in expected:
        assert value["top"] is False and value["bottom"] is False, value
        assert set(expected["result_types"]) <= set(value["types"]), value
        assert None not in value["constants"], value
        # Creation does not execute g's body. Any reported body completion must
        # distinguish suspension from a fabricated None-valued normal return.
        outcomes = result.query("outcomes")
        assert outcomes.status == "answered", outcomes.to_dict()
        body = outcomes.value.get(query["module"] + ".g", [])
        assert all(exit_["kind"] not in ("normal", "return") for exit_ in body), body
    else:
        raise AssertionError("TEST-01 has no predicate for mandatory fixture " + case["id"])
