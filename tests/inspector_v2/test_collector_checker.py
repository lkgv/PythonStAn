"""Independent finite source golds and real producer faults, not JSON oracles."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from pythonstan_inspector.collector_checker import (
    CHANNELS, GENERATION, OutsideSpecification, finite_spec,
)
from pythonstan_inspector.contract import CaptureRequest, QueryLimits, QueryRequest, SealedSnapshot
from pythonstan_inspector.storage import Bundle, load_bundle, write_bundle


F1 = "def take(**kw):\n    return next(iter(kw.keys()))\nresult = take(x=1)\n"
CALLBACK = """events = []
def chosen():
    events.append('chosen')
    return 7
def poison():
    events.append('poison')
    return 99
def forward(**kwargs):
    def names():
        yield from kwargs.keys()
    pending = names()
    name = next(pending)
    return kwargs[name]()
result = forward(callback=chosen)
"""


def _check(snapshot, source, claim=GENERATION, limits=None):
    from pythonstan_inspector.checker import check
    return check(Bundle(snapshot, b"{}"), QueryRequest("check", {
        "checker": claim, "source_text": source}, claim=claim, limits=limits or QueryLimits()))


def _run(tmp_path, source=F1):
    from pythonstan_inspector.runner import run_source
    path = tmp_path / "subject.py"
    path.write_text(source)
    return run_source(path, request=CaptureRequest(views=(
        "ctx_points", "call_edges", "pfg_edges", "kwargs_model")))[0]


@pytest.mark.parametrize("signature,arguments,remaining", [
    ("**kw", "x=1", [("x", 1)]),
    ("**kw", "**{'x': 1}", [("x", 1)]),
    ("**kw", "", []),
    ("consumed, **kw", "consumed='poison', x=1", [("x", 1)]),
    ("*, consumed, **kw", "**{'consumed': 'poison', 'x': 1}", [("x", 1)]),
    ("x, /, **kw", "0, x=1", [("x", 1)]),
    ("consumed='default', **kw", "x=1", [("x", 1)]),
])
def test_frozen_independent_literal_binding_gold(signature, arguments, remaining):
    source = f"def take({signature}):\n    return kw\nresult = take({arguments})\n"
    assert finite_spec(source)["calls"][0]["remaining"] == remaining


def test_frozen_function_reference_gold_is_source_definition():
    expected = {"kind": "source_function", "name": "chosen", "definition_line": 2,
                "definition_column": 0}
    assert finite_spec(CALLBACK)["calls"][0]["remaining"] == [("callback", expected)]


@pytest.mark.parametrize("body", ["kw.clear()\n    return kw", "del kw['x']\n    return kw",
                                  "kw = {}\n    return kw", "alias = kw\n    return alias"])
def test_mutating_body_is_rejected_independently_of_candidate(body):
    with pytest.raises(OutsideSpecification, match="channel_preserving"):
        finite_spec("def take(**kw):\n    " + body + "\nresult = take(x=1)\n")


@pytest.mark.parametrize("source", [
    "def take(x, **kw):\n    return kw\nresult=take(0,x=1)\n",
    "def take(*, required, **kw):\n    return kw\nresult=take(x=1)\n",
    "def take(**kw):\n    return kw\nresult=take(**{1: 2})\n",
    "def take(**kw):\n    return kw\nresult=take(**{'x':1, 'x':2})\n",
    "def take(**kw):\n    return kw\nresult=take(x=1, **{'y':2})\n",
])
def test_invalid_or_unsupported_binding_cannot_become_empty_pass(source):
    with pytest.raises(OutsideSpecification):
        finite_spec(source)


@pytest.mark.parametrize("source", [F1, F1.replace("keys()", "values()"),
                                      F1.replace("x=1", "**{'x': 1}"), CALLBACK,
                                      CALLBACK.replace("callback=chosen", "**{'callback': chosen}")])
@pytest.mark.parametrize("claim", [GENERATION, CHANNELS])
def test_production_source_adapter_and_named_checker_pass(tmp_path, source, claim):
    snapshot = _run(tmp_path, source)
    result = _check(snapshot, source, claim)
    assert result["answer"] == "passed", result
    assert result["claim"] == result["requested_claim"] == claim
    assert result["obligations_expected"] == result["obligations_examined"] > 0
    assert result["obligations_unexamined"] == 0
    assert result["selected_invocations"]
    assert result["independence"]["production_transfer_reused"] is False
    if source == F1 and claim == GENERATION:
        write_bundle(snapshot, tmp_path / "green_bundle")
        (tmp_path / "checker_results.json").write_text(json.dumps({
            "evidence_category": "fresh_current_source_positive_control", "generation": result,
            "channels": _check(snapshot, source, CHANNELS)}, indent=2))


@pytest.mark.parametrize("arguments", ["", "**{}"])
def test_empty_channels_pass_only_after_independent_empty_input(tmp_path, arguments):
    source = "def take(**kw):\n    return kw\nresult=take(" + arguments + ")\n"
    snapshot = _run(tmp_path, source)
    generation, channels = (_check(snapshot, source, claim) for claim in (GENERATION, CHANNELS))
    assert generation["answer"] == channels["answer"] == "passed"
    assert generation["obligations_expected"] == generation["obligations_examined"] == 1
    assert channels["obligations_expected"] == channels["obligations_examined"] == 0
    assert channels["selected_invocations"][0]["collector_inputs"] == 0


def _producer_fault(monkeypatch, channel):
    """Omit actual production operations; no frozen DTO is edited here."""
    from pythonstan.analysis.pointer.kcfa.processor.normal_call import NormalCallProcessor
    from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
    from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
    from pythonstan.analysis.pointer.kcfa.variable import FieldAccess
    original_match = NormalCallProcessor._match_parameters
    original_flow = PointerAnalysisState._add_var_points_flow
    original_publish = PointerSolver.handle_new_points_to
    active = []

    def matching(self, *args, **kwargs):
        if channel == "whole_producer":
            return None
        active.append(True)
        try:
            return original_match(self, *args, **kwargs)
        finally:
            active.pop()

    def kind(owner):
        if isinstance(getattr(owner, "content", None), FieldAccess):
            field = owner.content.field
            return (field.kind.value, field.name)
        return None

    def flow(self, source, target):
        if active and ((channel == "aggregate" and kind(target) == ("elem", None))
                       or (channel == "key" and kind(target) == ("key", "x"))):
            return None
        return original_flow(self, source, target)

    def publish(self, owner, *args, **kwargs):
        if active and channel == "native_keys" and kind(owner) == ("class_metadata", "native_mapping_keys"):
            return None
        return original_publish(self, owner, *args, **kwargs)

    monkeypatch.setattr(NormalCallProcessor, "_match_parameters", matching)
    monkeypatch.setattr(PointerAnalysisState, "_add_var_points_flow", flow)
    monkeypatch.setattr(PointerSolver, "handle_new_points_to", publish)


@pytest.mark.parametrize("channel", ["whole_producer", "key", "aggregate", "native_keys"])
@pytest.mark.parametrize("consumer", ["keys", "values"])
def test_real_producer_omissions_have_nonzero_independent_denominator(tmp_path, monkeypatch, channel, consumer):
    _producer_fault(monkeypatch, channel)
    source = F1.replace("keys()", consumer + "()")
    snapshot = _run(tmp_path, source)
    directory = write_bundle(snapshot, tmp_path / ("fault_" + channel))
    loaded = load_bundle(directory)
    generation = _check(loaded.snapshot, source)
    assert generation["answer"] == "failed", generation
    assert generation["obligations_expected"] >= 4
    assert generation["evidence"]
    channels = _check(loaded.snapshot, source, CHANNELS)
    (tmp_path / "checker_results.json").write_text(json.dumps({
        "evidence_category": "controlled_actual_producer_fault", "fault": channel,
        "generation": generation, "channels": channels}, indent=2))
    if channel == "whole_producer":
        assert channels["answer"] == "indeterminate"
        assert channels["obligations_expected"] > 0
    else:
        assert channels["answer"] == "failed", channels
        assert any(e.get("channel") == channel and e["actual"] == "absent_at_snapshot"
                   for e in channels["evidence"])


@pytest.mark.parametrize("coverage_field", ["collector", "channels", "inputs", "signature", "collector_record"])
def test_partial_linkage_preserves_positive_witnesses_but_not_full_named_pass(tmp_path, coverage_field):
    data = _run(tmp_path).data
    row = next(r for r in data["snapshot"]["views"]["kwargs_model"]["rows"] if r.get("collector", {}).get("objects"))
    if coverage_field == "collector_record":
        row["collector"]["coverage"] = "partial"
    else:
        row["coverage"][coverage_field] = "partial"
    for claim in (GENERATION, CHANNELS):
        result = _check(SealedSnapshot.seal(data), F1, claim)
        assert result["answer"] == "indeterminate", result


@pytest.mark.parametrize("signature,arguments", [
    ("consumed, **kw", "consumed='poison', x=1"),
    ("*, consumed, **kw", "**{'consumed': 'poison', 'x': 1}"),
    ("x, /, **kw", "0, x=1"),
])
def test_real_formal_consumption_agrees_with_frozen_binding(tmp_path, signature, arguments):
    source = f"def take({signature}):\n    return next(iter(kw.keys()))\nresult = take({arguments})\n"
    snapshot = _run(tmp_path, source)
    for claim in (GENERATION, CHANNELS):
        result = _check(snapshot, source, claim)
        assert result["answer"] == "passed", result
        assert result["obligations_expected"] == (4 if claim == GENERATION else 3)


def test_source_name_and_distinct_source_calls_remain_separate(tmp_path):
    source = "value = 1\ndef take(**kw):\n    return kw\nleft = take(x=value)\nright = take(y=2)\n"
    snapshot = _run(tmp_path, source)
    for claim in (GENERATION, CHANNELS):
        result = _check(snapshot, source, claim)
        # 0-CFA merges the parameter binding. The finite v1 claim deliberately
        # does not guess which synthetic dictionary belongs to which call.
        assert result["answer"] == "indeterminate", result
        selected = result["selected_invocations"]
        assert len(selected) == 2 and {i["source_line"] for i in selected} == {4, 5}
        assert len({i["callsite_owner"] for i in selected}) == 2


def test_same_named_collector_in_distinct_contexts_uses_actual_scope(tmp_path, monkeypatch):
    from pythonstan_inspector import adapter
    from tests.round14_formal.test_key_context_model import _predict
    original = adapter.capture
    monkeypatch.setattr(adapter, "capture", lambda solver, request=None, run_metadata=None:
        original(solver, CaptureRequest(views=("ctx_points", "call_edges", "pfg_edges", "kwargs_model")), run_metadata))
    source = "def take(**kw):\n    return kw\nleft=take(x=1)\nright=take(y=2)\n"
    bundle, _ = _predict(tmp_path, source, policy="2-cfa", model="none")
    for claim in (GENERATION, CHANNELS):
        result = _check(bundle.snapshot, source, claim)
        assert result["answer"] == "passed", result
        assert len({i["callee_scope"] for i in result["selected_invocations"]}) == 2


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("model", ["none", "receiver_key_v1"])
def test_original_keys_four_configuration_actual_aggregate_fault(tmp_path, monkeypatch, policy, model):
    from pythonstan_inspector import adapter
    from tests.round14_formal.test_key_context_model import _predict
    original = adapter.capture

    def with_view(solver, request=None, run_metadata=None):
        return original(solver, CaptureRequest(views=("ctx_points", "call_edges", "pfg_edges", "kwargs_model")), run_metadata)

    monkeypatch.setattr(adapter, "capture", with_view)
    _producer_fault(monkeypatch, "aggregate")
    bundle, _ = _predict(tmp_path, F1, policy=policy, model=model)
    for claim in (GENERATION, CHANNELS):
        result = _check(bundle.snapshot, F1, claim)
        assert result["answer"] == "failed", result
        assert any(e.get("channel") == "aggregate" and e["actual"] == "absent_at_snapshot"
                   for e in result["evidence"])


def test_late_actual_value_input_expands_consequence_obligations(tmp_path, monkeypatch):
    from pythonstan_inspector import adapter
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    original, retained = adapter.capture, {}

    def retain(solver, request=None, run_metadata=None):
        retained.update(solver=solver, metadata=run_metadata)
        return original(solver, request, run_metadata)

    monkeypatch.setattr(adapter, "capture", retain)
    source = "incoming = None\nlater = 7\ndef take(**kw):\n    return next(iter(kw.values()))\nresult=take(x=incoming)\n"
    before = _run(tmp_path, source)
    solver = retained["solver"]
    owners = {o.content.name: o for o in solver.state._env
              if isinstance(o, Ctx) and isinstance(o.content, Variable) and o.content.kind.value == "global"}
    solver.handle_new_points_to(owners["incoming"], owners["incoming"].scope,
                               solver.state.get_points_to(owners["later"]))
    solver.solve_to_fixpoint()
    after = original(solver, CaptureRequest(views=("ctx_points", "call_edges", "pfg_edges", "kwargs_model")),
                     {**retained["metadata"], "audit_kind": "controlled_abstract_input_growth"})
    first, second = _check(before, source, CHANNELS), _check(after, source, CHANNELS)
    assert first["answer"] == second["answer"] == "passed", (first, second)
    assert first["obligations_expected"] == 3 and second["obligations_expected"] == 5


@pytest.mark.parametrize("summary", [False, True])
def test_unknown_or_summary_in_required_target_is_indeterminate(tmp_path, summary):
    data = _run(tmp_path).data
    view = data["snapshot"]["views"]["kwargs_model"]
    row = next(r for r in view["rows"] if r.get("collector", {}).get("objects"))
    channel = next(c for c in row["collector"]["objects"][0]["channels"] if c["kind"] == "key")
    owner = channel["owner"]["ref"]
    fact = next(f for f in data["snapshot"]["views"]["ctx_points"]["rows"] if f["owner"] == owner)
    obj = next(e for e in data["snapshot"]["entities"] if e["id"] == fact["object"])
    if summary:
        obj["attributes"]["native_summary"] = True
    else:
        obj["attributes"]["engine_class"] = "AbstractObject"
        obj["attributes"].pop("value", None)
    for claim in (GENERATION, CHANNELS):
        result = _check(SealedSnapshot.seal(data), F1, claim)
        assert result["answer"] == "indeterminate", result


def test_opaque_bound_context_prevents_named_pass(tmp_path):
    data = _run(tmp_path).data
    row = next(r for r in data["snapshot"]["views"]["kwargs_model"]["rows"] if r.get("collector", {}).get("objects"))
    collector = next(e for e in data["snapshot"]["entities"] if e["id"] == row["collector"]["objects"][0]["object"])
    context = next(e for e in data["snapshot"]["entities"] if e["id"] == collector["links"]["context"]["ref"])
    context["kind"] = "opaque"
    context["attributes"]["description_coverage"] = "opaque"
    for claim in (GENERATION, CHANNELS):
        assert _check(SealedSnapshot.seal(data), F1, claim)["answer"] == "indeterminate"


@pytest.mark.parametrize("bad", ["rows", "fields", "field_state", "reference", "channels", "signature"])
def test_storage_accepted_optional_model_shape_is_not_blindly_interpreted(tmp_path, bad):
    data = _run(tmp_path).data
    model = data["snapshot"]["views"]["kwargs_model"]
    row = next(r for r in model["rows"] if r.get("collector", {}).get("objects"))
    if bad == "rows":
        model["rows"] = ["bad"]
    elif bad == "fields":
        model["coverage"]["fields"] = []
    elif bad == "field_state":
        model["coverage"]["fields"]["constraints"] = []
    elif bad == "reference":
        row["callee_input"]["status"] = []
    elif bad == "channels":
        row["collector"]["objects"][0]["channels"] = [1]
    else:
        row["signature"]["positional"] = 1
    loaded = load_bundle(write_bundle(SealedSnapshot.seal(data), tmp_path / "malformed"))
    for claim in (GENERATION, CHANNELS):
        result = _check(loaded.snapshot, F1, claim)
        assert result["answer"] == "unsupported", result


@pytest.mark.parametrize("mutation", ["partial_registry", "partial_collector", "incoherent", "no_invocation"])
def test_controlled_dto_gaps_are_indeterminate_never_empty_pass(tmp_path, mutation):
    snapshot = _run(tmp_path)
    data = snapshot.data
    if mutation == "partial_registry":
        data["snapshot"]["views"]["kwargs_model"]["coverage"]["registry"] = "partial"
    elif mutation == "partial_collector":
        row = next(r for r in data["snapshot"]["views"]["kwargs_model"]["rows"] if r.get("collector", {}).get("objects"))
        row["coverage"]["collector"] = "partial"
        row["collector"]["objects"] = []
    elif mutation == "incoherent":
        data["snapshot"]["boundary"]["coherent"] = False
    else:
        data["snapshot"]["views"]["kwargs_model"]["rows"] = []
    result = _check(SealedSnapshot.seal(data), F1)
    assert result["answer"] == "indeterminate", result
    assert result["obligations_expected"] > 0


def test_checker_budget_cannot_return_pass_for_unexamined_obligations(tmp_path):
    result = _check(_run(tmp_path), F1, limits=QueryLimits(max_visited=150, max_bytes=1000000))
    assert result["answer"] != "passed"


@pytest.mark.parametrize("fault,frontier", [(None, "required_channels_represented"),
    ("whole_producer", "collector_generation_missing"), ("key", "required_channel_missing"),
    ("aggregate", "required_channel_missing"), ("native_keys", "required_channel_missing")])
def test_public_model_explain_distinguishes_local_boundaries(tmp_path, monkeypatch, fault, frontier):
    from pythonstan_inspector.core import query
    if fault:
        _producer_fault(monkeypatch, fault)
    snapshot = _run(tmp_path)
    result = query(Bundle(snapshot, b"{}"), QueryRequest("explain", {
        "model": "keyword_collector", "source_text": F1}))
    assert result["answer"] == "model_frontier", result
    assert result["logic"] == {"candidates": "OR", "premises_within_candidate": "AND"}
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["frontier"] == frontier
    assert {p["name"] for p in candidate["premises"]} == {
        "source_admission", "collector_binding", "actual_value_inputs", "key", "aggregate", "native_keys"}
    assert "no unique root cause" in result["causal_claim"]
    identity = candidate["invocation"]
    selected = query(Bundle(snapshot, b"{}"), QueryRequest("explain", {
        "model": "keyword_collector", "source_text": F1, "invocation": identity}))
    assert selected["candidates"][0]["invocation"] == identity
    if not fault:
        assert candidate["consumers"][0]["stage"] == "registered_model_invocation"


def test_engine_free_bundle_checker_has_no_native_imports(tmp_path):
    snapshot = _run(tmp_path)
    directory = write_bundle(snapshot, tmp_path / "bundle")
    isolated = tmp_path / "offline"
    package = isolated / "pythonstan_inspector"
    package.mkdir(parents=True)
    original = Path(__file__).resolve().parents[2] / "pythonstan_inspector"
    for filename in ("__init__.py", "contract.py", "core.py", "storage.py", "checker.py", "collector_checker.py", "identity.py"):
        shutil.copyfile(original / filename, package / filename)
    program = """import importlib.abc,json,sys
class NoNative(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'pythonstan' or fullname.startswith('pythonstan.'):
            raise AssertionError('native import: ' + fullname)
sys.meta_path.insert(0, NoNative())
from pythonstan_inspector.checker import check
from pythonstan_inspector.contract import QueryRequest
from pythonstan_inspector.storage import load_bundle
source = SOURCE
answers = []
for claim in CLAIMS:
    answers.append(check(load_bundle(BUNDLE), QueryRequest('check', {'checker':claim,'source_text':source},claim=claim))['answer'])
print(json.dumps(answers))
""".replace("SOURCE", repr(F1)).replace("CLAIMS", repr([GENERATION, CHANNELS])).replace("BUNDLE", repr(str(directory)))
    completed = subprocess.run([sys.executable, "-I", "-c", "import sys;sys.path.insert(0," + repr(str(isolated)) + ");" + program],
                               cwd=isolated, capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == ["passed", "passed"]
