"""Separate real engine mutation gates from supplemental protocol fixtures."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from pythonstan_inspector.checker import COPY_CLOSURE, MODULE_COPY_SPEC, check
from pythonstan_inspector.contract import InspectorError, QueryLimits, QueryRequest, SealedSnapshot
from pythonstan_inspector.storage import Bundle, load_bundle, write_bundle


def _request(checker=COPY_CLOSURE, **args):
    return QueryRequest("check", {"checker": checker, **args}, claim=checker)


def _bundle(snapshot):
    return Bundle(snapshot, b"{}")


def _run(tmp_path, text="source = 1\ntarget = source\n"):
    from pythonstan_inspector.runner import run_source
    source = tmp_path / "subject.py"
    source.write_text(text)
    snapshot, _, _ = run_source(source)
    return snapshot


def _recapture_missing_late_replay(tmp_path, monkeypatch):
    """Fault injection operates on production state, never artifact JSON."""
    from pythonstan_inspector import adapter
    from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode, PointerFlowEdge, PointerFlowKind
    from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
    captured = {}
    original_capture = adapter.capture

    def retain(solver, request=None, run_metadata=None):
        captured["solver"] = solver
        captured["metadata"] = run_metadata
        return original_capture(solver, request, run_metadata)

    monkeypatch.setattr(adapter, "capture", retain)
    _run(tmp_path, "source = 1\ntarget = 2\n")
    solver = captured["solver"]
    nodes = {node.var.content.name: node for node in solver.state._env
             if isinstance(node, NormalNode) and hasattr(node.var.content, "name")}
    edge = PointerFlowEdge(nodes["source"], nodes["target"], PointerFlowKind.NORMAL)
    before_pending = solver._pending_work_count()
    before_iteration = solver._iteration
    # Production entry point still registers the late edge, but the controlled
    # defect removes its mandatory replay of already-published source facts.
    monkeypatch.setattr(PointerAnalysisState, "_add_points_flow_edge",
                        lambda state, item: state._pointer_flow_graph.add_edge(item))
    solver.state._add_points_flow_edge(edge)
    assert solver._pending_work_count() == before_pending == 0
    snapshot = original_capture(solver, run_metadata=captured["metadata"])
    assert solver._iteration == before_iteration
    return snapshot, solver


def test_real_source_ordinary_registered_closure_passes(tmp_path):
    result = check(_bundle(_run(tmp_path)), _request())
    assert result["answer"] == result["check_status"] == "passed"
    assert result["requested_claim"] == result["claim"] == COPY_CLOSURE
    assert result["obligations_examined"] > 0
    assert result["independence"]["production_transfer_reused"] is False
    assert result["independence"]["production_scheduler_reused"] is False
    assert result["concrete_reachability_proved"] is False


def test_actual_engine_missing_late_replay_is_saved_then_killed(tmp_path, monkeypatch):
    snapshot, solver = _recapture_missing_late_replay(tmp_path, monkeypatch)
    path = write_bundle(snapshot, tmp_path / "bad_late_replay")
    result = check(load_bundle(path), _request())
    assert result["answer"] == "failed"
    assert result["completion"]["engine"] == "completed"
    assert result["completion"]["check"] == "failed"
    assert result["evidence"]
    witness = result["evidence"][0]
    assert witness["violation"] == "copy_closure"
    assert witness["input_fact"]["object"] == witness["missing_consequence"]["object"]
    assert witness["rule"]["source"] == witness["input_fact"]["owner"]
    assert witness["rule"]["target"] == witness["missing_consequence"]["owner"]
    assert witness["rule"]["source"]["snapshot"]["snapshot_id"] == snapshot.ref.snapshot_id
    assert witness["target_field_coverage"] == "complete"
    assert "delivery_history" in " ".join(result["diagnostics"])
    frozen = snapshot.encoded
    solver.state._env.clear()
    assert snapshot.encoded == frozen
    assert check(load_bundle(path), _request())["answer"] == "failed"


def test_engine_free_reader_executes_real_mutation_checker(tmp_path, monkeypatch):
    snapshot, _ = _recapture_missing_late_replay(tmp_path, monkeypatch)
    path = write_bundle(snapshot, tmp_path / "bundle")
    isolated = tmp_path / "isolated"
    package = isolated / "pythonstan_inspector"
    package.mkdir(parents=True)
    original = Path(__file__).resolve().parents[2] / "pythonstan_inspector"
    # No adapter, runner, engine or third-party dependencies are copied.
    for filename in ("__init__.py", "contract.py", "core.py", "identity.py", "storage.py", "checker.py",
                     "collector_checker.py"):
        shutil.copyfile(original / filename, package / filename)
    script = isolated / "read.py"
    script.write_text("import sys,json\nsys.path.insert(0," + repr(str(isolated)) + ")\n"
        "from pythonstan_inspector.storage import load_bundle\n"
        "from pythonstan_inspector.checker import check\n"
        "from pythonstan_inspector.contract import QueryRequest\n"
        "r=check(load_bundle(" + repr(str(path)) + "),QueryRequest('check'))\n"
        "assert r['answer']=='failed'\n"
        "assert not any(k=='pythonstan' or k.startswith('pythonstan.') for k in sys.modules)\n"
        "print(json.dumps(r))\n")
    result = subprocess.run([sys.executable, "-I", "-S", str(script)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["check_status"] == "failed"


def test_independent_source_spec_catches_rule_never_generated(tmp_path, monkeypatch):
    from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
    original = PointerSolver._apply_copy
    skipped = []

    def drop_required_copy(solver, scope, context, constraint):
        if constraint.source.name == "source" and constraint.target.name == "target":
            skipped.append((constraint.source.name, constraint.target.name))
            return None
        return original(solver, scope, context, constraint)

    monkeypatch.setattr(PointerSolver, "_apply_copy", drop_required_copy)
    source = "source = 1\ntarget = 2\ntarget = source\n"
    snapshot = _run(tmp_path, source)
    assert skipped
    bundle = load_bundle(write_bundle(snapshot, tmp_path / "missing_rule"))
    # Checking the surviving registry cannot discover the deleted requirement.
    assert check(bundle, _request())["answer"] == "passed"
    independent = check(bundle, _request(MODULE_COPY_SPEC, source_text=source))
    assert independent["answer"] == "failed"
    witness = independent["evidence"][0]
    assert witness["violation"] == "missing_required_copy_rule"
    assert witness["source_obligation"]["source_name"] == "source"
    assert witness["source_obligation"]["target_name"] == "target"
    assert witness["source_obligation"]["line"] == 3


def test_source_spec_passes_only_its_bound_narrow_language(tmp_path):
    source = "source = 1\ntarget = source\n"
    bundle = _bundle(_run(tmp_path, source))
    result = check(bundle, _request(MODULE_COPY_SPEC, source_text=source))
    assert result["answer"] == "passed"
    assert check(bundle, _request(MODULE_COPY_SPEC, source_text=source + "other=3\n"))["answer"] == "indeterminate"


def test_source_spec_rejects_general_frontend_claim(tmp_path):
    source = "def callback():\n    return 1\n"
    result = check(_bundle(_run(tmp_path, source)), _request(MODULE_COPY_SPEC, source_text=source))
    assert result["answer"] == "unsupported"
    assert result["requested_claim"] == MODULE_COPY_SPEC


def test_source_spec_never_uses_truncated_name_as_missing_rule(tmp_path):
    name = "a" * 513
    source = name + " = 1\ntarget = " + name + "\n"
    result = check(_bundle(_run(tmp_path, source)), _request(MODULE_COPY_SPEC, source_text=source))
    assert result["answer"] == "unsupported"
    assert "description_limit" in result["diagnostics"][0]


@pytest.fixture
def protocol_snapshot(tmp_path):
    """Supplemental JSON fixture, calibrated from an actual production capture."""
    return _run(tmp_path).data


def _changed(data, edit):
    data = copy.deepcopy(data)
    edit(data["snapshot"])
    return _bundle(SealedSnapshot.seal(data))


@pytest.mark.parametrize("view", ["node_points", "pfg_edges"])
def test_partial_registry_cannot_pass_original_full_claim(protocol_snapshot, view):
    bundle = _changed(protocol_snapshot,
        lambda snap: snap["views"][view]["coverage"].update(registry="partial"))
    assert check(bundle, _request())["answer"] == "indeterminate"


def test_partial_can_refute_with_exact_input_and_complete_target(tmp_path, monkeypatch):
    snapshot, _ = _recapture_missing_late_replay(tmp_path, monkeypatch)
    partial = _changed(snapshot.data,
        lambda snap: snap["views"]["pfg_edges"]["coverage"].update(registry="partial"))
    result = check(partial, _request())
    assert result["answer"] == "failed"
    assert result["evidence"][0]["target_field_coverage"] == "complete"


def test_missing_target_field_coverage_prevents_false_failure(tmp_path, monkeypatch):
    snapshot, _ = _recapture_missing_late_replay(tmp_path, monkeypatch)
    initial = check(_bundle(snapshot), _request())
    targets = {item["missing_consequence"]["owner"]["local_id"] for item in initial["evidence"]}

    def truncate(snap):
        for owner in targets:
            snap["views"]["node_points"]["coverage"]["owners"][str(owner)]["fields"]["facts"] = "partial"

    assert check(_changed(snapshot.data, truncate), _request())["answer"] == "indeterminate"


@pytest.mark.parametrize("fidelity", ["display_summary", "native_summary", "opaque"])
def test_nonexact_view_never_passes_exact_closure(protocol_snapshot, fidelity):
    bundle = _changed(protocol_snapshot,
        lambda snap: snap["views"]["node_points"].update(fidelity=fidelity))
    assert check(bundle, _request())["answer"] == "unsupported"


def test_native_summary_object_cannot_prove_missing_concrete_fact(tmp_path, monkeypatch):
    snapshot, _ = _recapture_missing_late_replay(tmp_path, monkeypatch)

    def summary(snap):
        for entity in snap["entities"]:
            if entity["kind"] == "object":
                entity["attributes"]["native_summary"] = True

    assert check(_changed(snapshot.data, summary), _request())["answer"] == "indeterminate"


def test_coherence_is_required_but_engine_completion_is_not(protocol_snapshot):
    unsafe = _changed(protocol_snapshot, lambda snap: snap["boundary"].update(coherent=False))
    assert check(unsafe, _request())["answer"] == "indeterminate"
    timeout = copy.deepcopy(protocol_snapshot)
    timeout["run"]["stop_reason"] = "iteration_limit"
    result = check(_bundle(SealedSnapshot.seal(timeout)), _request())
    assert result["answer"] == "passed"
    assert result["completion"]["engine"] == "iteration_limit"
    assert result["concrete_reachability_proved"] is False


def test_budget_and_requested_claim_cannot_weaken_pass(protocol_snapshot):
    bundle = _bundle(SealedSnapshot.seal(protocol_snapshot))
    request = QueryRequest("check", limits=QueryLimits(max_visited=1))
    with pytest.raises(InspectorError, match="admission"):
        check(bundle, request)
    request = QueryRequest("check", claim="all_python_soundness")
    result = check(bundle, request)
    assert result["answer"] == "unsupported"
    assert result["requested_claim"] == "all_python_soundness"
    assert check(bundle, _request("unrecognized_checker"))["answer"] == "unsupported"
    assert check(bundle, QueryRequest("check", cursor="old"))["answer"] == "unsupported"


def test_snapshot_scoped_witnesses_do_not_alias_reused_local_ids(tmp_path, monkeypatch):
    snapshot, _ = _recapture_missing_late_replay(tmp_path, monkeypatch)
    other = _changed(snapshot.data, lambda snap: snap.update(snapshot_id="other-snapshot"))
    left = check(_bundle(snapshot), _request())["evidence"][0]["input_fact"]["owner"]
    right = check(other, _request())["evidence"][0]["input_fact"]["owner"]
    assert left["local_id"] == right["local_id"]
    assert left != right


def test_actual_same_display_names_remain_separate_scoped_entities(tmp_path):
    source = ("def left():\n    source = 1\n    target = source\n    return target\n"
              "def right():\n    source = 2\n    target = source\n    return target\n"
              "left()\nright()\n")
    snapshot = _run(tmp_path, source)
    data = snapshot.data
    targets = [entity for entity in data["snapshot"]["entities"]
               if entity["kind"] == "node" and entity["label"] == "node:target"]
    assert len(targets) == 2
    assert targets[0]["id"] != targets[1]["id"]
    assert targets[0]["origin"]["scope"] != targets[1]["origin"]["scope"]
    assert check(_bundle(snapshot), _request())["answer"] == "passed"


def test_adapter_copy_flag_is_not_a_shared_semantic_oracle(tmp_path, monkeypatch):
    snapshot, _ = _recapture_missing_late_replay(tmp_path, monkeypatch)

    def faulty_flag(snap):
        for rule in snap["views"]["pfg_edges"]["rows"]:
            rule["identity_copy"] = False

    assert check(_changed(snapshot.data, faulty_flag), _request())["answer"] == "failed"


def test_unavailable_endpoint_type_cannot_hide_a_rule(protocol_snapshot):
    def erase_type(snap):
        for entity in snap["entities"]:
            if entity["kind"] == "node":
                entity["attributes"].pop("engine_class", None)

    assert check(_changed(protocol_snapshot, erase_type), _request())["answer"] == "indeterminate"


def test_unknown_semantic_version_is_never_interpreted_as_v1(protocol_snapshot):
    for view_name in ("node_points", "pfg_edges"):
        bundle = _changed(protocol_snapshot,
            lambda snap: snap["views"][view_name].update(semantic_version="future_semantics_v2"))
        assert check(bundle, _request())["answer"] == "unsupported"


def test_direct_public_checker_enforces_whole_response_byte_budget(protocol_snapshot):
    bundle = _bundle(SealedSnapshot.seal(protocol_snapshot))
    request = QueryRequest("check", limits=QueryLimits(max_bytes=1))
    with pytest.raises(InspectorError, match="byte limit"):
        check(bundle, request)


def test_decode_admission_precedes_access_to_decoded_data(protocol_snapshot):
    class NeverDecode:
        snapshot = SealedSnapshot.seal(protocol_snapshot)

        @property
        def data(self):
            raise AssertionError("decode must not occur before admission")

    with pytest.raises(InspectorError, match="admission"):
        check(NeverDecode(), QueryRequest("check", limits=QueryLimits(max_visited=1)))


def test_two_input_coalescing_requires_every_pair_not_just_advanced_cursors():
    """Independent finite protocol oracle; NOT an actual Python-engine gate.

    Contract: finite monotone left/right sets, output must contain Cartesian
    product.  The deliberate faulty consumer advances both cursors but joins
    only simultaneous delta pairs.  No production helper or scheduler is used.
    """
    left, right = {"l0", "l1"}, {"r0", "r1"}
    expected = {(lvalue, rvalue) for lvalue in left for rvalue in right}
    faulty_output = {("l0", "r0"), ("l1", "r1")}
    consumed = {"left": len(left), "right": len(right)}
    assert consumed == {"left": 2, "right": 2}
    missing = expected.difference(faulty_output)
    assert missing == {("l0", "r1"), ("l1", "r0")}
    assert expected.difference(expected) == set()
