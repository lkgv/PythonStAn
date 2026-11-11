"""Independent acceptance of the frozen supported source fixtures.

Failures remain ordinary failures during implementation, never xfail/skip.
Component obligations in query-gold are covered by their separate owner suites.
"""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from scripts.refactor.semantic_oracle import (
    analyze_fixture, assert_concrete_gold, assert_scalar_snapshot,
    concrete_witness, source_fixtures, load_gold,
)


CASES = source_fixtures()
OPEN_CASES = [case for case in load_gold()["required_open_behavior_fixtures"] if case["kind"] == "source"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_frozen_source_gold(case):
    witness = concrete_witness(case)
    assert_concrete_gold(case, witness)
    analyze_fixture(case, engine="ai_primary")


@pytest.mark.parametrize("case", OPEN_CASES, ids=lambda case: case["id"])
def test_frozen_open_boundary_gold(case):
    # Its input bound covers all strings. No sample or undefined-variable
    # NameError is substituted for that public abstract input contract.
    analyze_fixture(case, engine="ai_primary")


def _one_source(value="after"):
    return "def identity(x):\n    return x\nresult = identity(" + repr(value) + ")\n"


def _api_parts():
    from pythonstan import File, Project, Source, ValueQuery, analyze
    return analyze, Source, File, Project, ValueQuery


def test_three_input_forms_and_in_memory_source(tmp_path):
    analyze, Source, File, Project, ValueQuery = _api_parts()
    query = ValueQuery(module="example", symbol="result", point="module_exit")
    source = _one_source()
    path = tmp_path / "example.py"
    path.write_text(source)
    # The supplied virtual filename deliberately points at different disk bytes.
    virtual = tmp_path / "virtual.py"
    virtual.write_text("result = 'wrong-disk-source'\n")
    targets = (Source(source, filename=str(virtual), module="example"), File(path), Project(tmp_path))
    for target in targets:
        result = analyze(target, config={"schema_version": 1, "engine": "ai_primary"}, queries=(query,))
        answer = result.query(query)
        assert answer.status == "answered", answer.to_dict()
        assert_scalar_snapshot(answer.value, {"constants": ["after"], "exact": True})
    assert virtual.read_text() == "result = 'wrong-disk-source'\n"


def test_api_is_quiet_and_results_survive_later_session(capsys):
    analyze, Source, _, _, ValueQuery = _api_parts()
    query = ValueQuery(module="example", symbol="result", point="module_exit")
    config = {"schema_version": 1, "engine": "ai_primary"}
    first = analyze(Source(_one_source("first"), filename="same.py", module="example"),
                    config=config, queries=(query,))
    before = json.dumps(first.to_dict(), sort_keys=True)
    second = analyze(Source(_one_source("second"), filename="same.py", module="example"),
                     config=config, queries=(query,))
    assert_scalar_snapshot(first.query(query).value, {"constants": ["first"], "exact": True})
    assert_scalar_snapshot(second.query(query).value, {"constants": ["second"], "exact": True})
    assert json.dumps(first.to_dict(), sort_keys=True) == before
    captured = capsys.readouterr()
    assert captured.out == "", "The Python API must not print analysis/log output"


def test_interleaved_parallel_sessions():
    analyze, Source, _, _, ValueQuery = _api_parts()
    query = ValueQuery(module="example", symbol="result", point="module_exit")
    def run(value):
        result = analyze(Source(_one_source(value), filename="same.py", module="example"),
                         config={"schema_version": 1, "engine": "ai_primary"}, queries=(query,))
        return result
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, ["alpha", "beta", "gamma", "delta"]))
    for result, value in zip(results, ["alpha", "beta", "gamma", "delta"]):
        assert_scalar_snapshot(result.query(query).value, {"constants": [value], "exact": True})
        json.dumps(result.to_dict())


def test_ai_primary_does_not_construct_or_solve_legacy_native(monkeypatch):
    from pythonstan.world.pipeline import Pipeline
    from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
    def forbidden(*args, **kwargs):
        raise AssertionError("AI-primary invoked legacy Pipeline/Native")
    monkeypatch.setattr(Pipeline, "__init__", forbidden)
    monkeypatch.setattr(PointerSolver, "__init__", forbidden)
    analyze_fixture(next(case for case in CASES if case["id"] == "A01_scalar_return"))


def test_static_analysis_never_executes_target(tmp_path):
    analyze, Source, _, _, ValueQuery = _api_parts()
    sentinel = tmp_path / "must-not-exist"
    source = "open(" + repr(str(sentinel)) + ", 'w').write('executed')\nresult = 'after'\n"
    query = ValueQuery(module="example", symbol="result", point="module_exit")
    result = analyze(Source(source, filename="noexec.py", module="example"),
                     config={"schema_version": 1, "engine": "ai_primary"}, queries=(query,))
    assert not sentinel.exists(), "The analyzer executed a target side effect"
    assert result.coverage_status in ("open", "unsupported")


def test_unknown_dynamic_code_havoc_is_consumed():
    analyze, Source, _, _, ValueQuery = _api_parts()
    # This held-out dynamic boundary needs no implicit external binding: the
    # executable string is deliberately explicit while eval stays unmodeled.
    source = "class C: pass\nbox = C()\nbox.value = 'before'\neval(\"setattr(box, 'value', 'after')\")\nresult = box.value\n"
    query = ValueQuery(module="example", symbol="result", point="module_exit")
    result = analyze(Source(source, filename="dynamic.py", module="example"),
                     config={"schema_version": 1, "engine": "ai_primary"},
                     queries=(query, "effects"))
    assert result.coverage_status == "open"
    answer = result.query(query)
    if answer.status == "answered":
        value = answer.value
        assert value["top"] is True, (
            "Unknown effects left a falsely precise old heap value", value)
    else:
        assert answer.status == "unavailable", answer.to_dict()
    effects = result.query("effects")
    assert effects.status == "answered", effects.to_dict()
    assert effects.value["havoc"], "An effect ledger alone is insufficient; expose consumed havoc"
    assert effects.value["exceptions"], "Unknown dynamic code may raise"


def test_custom_bool_is_evaluated_or_honestly_open():
    analyze, Source, _, _, ValueQuery = _api_parts()
    source = "class C:\n    def __bool__(self):\n        return False\nif C():\n    result='before'\nelse:\n    result='after'\n"
    query = ValueQuery("example", "result")
    result = analyze(Source(source, filename="truth.py", module="example"),
                     config={"engine": "ai_primary"}, queries=(query, "effects"))
    answer = result.query(query)
    assert answer.status == "answered", answer.to_dict()
    if result.coverage_status == "closed_under_profile":
        assert_scalar_snapshot(answer.value, {"constants": ["after"], "exact": True})
    else:
        assert result.coverage_status == "open"
        assert result.raw_snapshot()["obligations"]
        assert_scalar_snapshot(answer.value, {"must_include": ["before", "after"]})
        effects = result.query("effects").value
        assert effects["calls"] and effects["exceptions"] and effects["havoc"], effects


def test_unknown_callback_havocs_reachable_closure_cells():
    analyze, Source, _, _, ValueQuery = _api_parts()
    source = "def outer():\n    value='before'\n    def callback():\n        nonlocal value\n        value='after'\n    foreign(callback)\n    return value\nresult=outer()\n"
    # The separate concrete witness supplies one permitted external callback
    # implementation. It is not fed to the static analyzer's all-value bound.
    witness_case = {"id": "unknown_closure_callback", "source": "def foreign(callback):\n    callback()\n" + source,
                    "expected": {"constants": ["after"]}}
    assert_concrete_gold(witness_case, concrete_witness(witness_case))
    query = ValueQuery("example", "result")
    result = analyze(Source(source, filename="callback.py", module="example"),
                     config={"engine": "ai_primary", "inputs": {"bindings": {"example.foreign": {"top": True}}}},
                     queries=(query, "effects"))
    assert result.coverage_status == "open"
    answer = result.query(query)
    if answer.status == "answered":
        value = answer.value
        assert value["top"] or "after" in value["constants"], (
            "The unknown callback's write to its reachable closure cell was lost", value)
    else:
        assert answer.status == "unavailable"
