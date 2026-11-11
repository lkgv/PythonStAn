"""Unknown calls must change later semantics, not only diagnostic logs."""
import ast
import json
from pathlib import Path
import subprocess
import sys

import pytest
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.context import CallSite
from pythonstan.analysis.pointer.kcfa.context_selector import ContextSelector,ContextPolicy
from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint
from pythonstan.analysis.pointer.kcfa.object import AbstractObject,UnknownValue
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.pointer.kcfa.processor import ComposeProcessor
from pythonstan.analysis.pointer.kcfa.heap_model import attr
from pythonstan.analysis.pointer.kcfa.variable import Variable,VariableKind
from pythonstan.ir.ir_statements import IRCall
from tests.pointer.kcfa.builders import make_scope,make_site

ROOT=Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("case",["unknown_heap","unknown_global","unknown_cell"])
def test_unknown_effects_change_native_result_and_publish_semantic_events(case):
    command=[sys.executable,str(ROOT/"scripts/refactor/native_context_probe.py"),
             "--repo",str(ROOT),"--case",case,"--budget","6000","--policy","2-cfa"]
    result=subprocess.run(command,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    row=json.loads(result.stdout)
    assert row["status"]=="ok",row
    assert row["statistics"]["converged"]==1,row
    assert any(item["kind"]=="UnknownValue" for item in row["values"]["result"]),row
    # Independent concrete oracle is a separate process on these trusted,
    # finite fixtures only. The analyzer never executes target eval/exec.
    witness=subprocess.run([sys.executable,"-I","-c",row["source"]+"\nprint(result)\n"],
                           capture_output=True,text=True,timeout=5)
    assert witness.returncode==0,witness.stderr
    assert witness.stdout.strip()=="7"
    kinds={event["kind"] for event in row["unknown_effect_events"]}
    assert {"heap_havoc","unknown_callback","unknown_exception"} <= kinds,row
    assert row["residual_effects"],row


def test_unused_return_havoc_subscribes_future_fields_globals_and_cells():
    state=PointerAnalysisState()
    scope=make_scope("subject.outer")
    context=scope.context
    solver=PointerSolver(state,Config(max_iterations=1000,enable_ai_intrinsics=False,
                                      entry_points=[],work_scheduling_policy="fair"),
                         ComposeProcessor([]),context_selector=ContextSelector(ContextPolicy.CALL_2))
    statement=IRCall(ast.parse("mystery()").body[0].value)
    call=CallConstraint(Variable("mystery"),(),frozenset(),None,
                        CallSite(statement,"subject.outer"))
    solver._mark_unknown_call(scope,call,"independent external-boundary fixture")
    # These locations did not exist when the unknown call was processed.
    obj=AbstractObject(context,make_site(name="late_object"))
    field=state.get_field(scope,context,obj,attr("later"))
    global_slot=state.get_variable(scope,context,Variable("late_global",VariableKind.GLOBAL))
    captured=state.get_variable(scope,context,Variable("captured",VariableKind.PARAMETER))
    state.set_cell_vars(scope.obj,{"captured":captured})
    solver.solve_to_fixpoint()
    for location in (field,global_slot,captured):
        assert any(isinstance(value,UnknownValue) and value.is_top_value
                   for value in state.get_points_to(location)),location
    from pythonstan.analysis.pointer.ai.value_domain import AbsVal
    projected=AbsVal.from_addrs(frozenset(state.get_points_to(field)))
    assert projected.addr_top
    assert projected.get_const_strs() is None
    assert projected.get_int_lattice().get_consts() is None
    assert solver._stats["converged"]==1
    assert len(state.unknown_effects.snapshot())==3
    snapshot=state.unknown_effects.snapshot();snapshot[0]["kind"]="changed"
    assert state.unknown_effects.snapshot()[0]["kind"]=="heap_havoc"


def test_passive_native_value_export_does_not_create_havoc_work():
    from pythonstan.analysis.pointer.kcfa.solver import SolverQuery
    from pythonstan.analysis.pointer.kcfa.unknown_tracker import UnknownTracker
    state=PointerAnalysisState();scope=make_scope("subject", "module")
    state.set_internal_scope(scope.obj,scope)
    solver=PointerSolver(state,Config(max_iterations=1000,enable_ai_intrinsics=False,
                                     entry_points=[],work_scheduling_policy="fair"),
                         ComposeProcessor([]),context_selector=ContextSelector(ContextPolicy.CALL_2))
    statement=IRCall(ast.parse("mystery()").body[0].value)
    call=CallConstraint(Variable("mystery"),(),frozenset(),None,CallSite(statement,"subject"))
    solver._mark_unknown_call(scope,call,"external")
    solver.solve_to_fixpoint()
    query=SolverQuery(state,solver._stats,solver._unknown_tracker,solver=solver)
    pending=solver._pending_work_count()
    slots=sum(map(len,state._heap.heap.values()))
    assert any(isinstance(value,UnknownValue) for value in query.points_to_at("never_defined",scope.stmt))
    assert solver._pending_work_count()==pending==0
    assert sum(map(len,state._heap.heap.values()))==slots


def test_value_top_field_bridge_does_not_depend_on_global_havoc():
    from pythonstan.analysis.pointer.kcfa.object import AllocKind,AllocSite
    from pythonstan.analysis.pointer.kcfa.pta_query_adapter import PtaQueryAdapter
    from pythonstan.analysis.pointer.ai.value_domain import AbsVal
    scope=make_scope("subject", "module");state=PointerAnalysisState()
    site=AllocSite("opaque-value",AllocKind.UNKNOWN)
    top=UnknownValue(scope.context,site)
    assert state.unknown_effects is None
    points=PtaQueryAdapter(state).field_points_to(top,attr("anything"),scope,scope.context)
    value=AbsVal.from_addrs(frozenset(points))
    assert value.addr_top and value.get_const_strs() is None
    assert not state._field_accesses


@pytest.mark.parametrize("mutation",["ledger_only","drop_future_field_subscription"])
def test_acceptance_rejects_ledger_only_and_missing_future_havoc(monkeypatch,mutation):
    from pythonstan.analysis.pointer.kcfa.unknown_effects import UnknownEffects
    state=PointerAnalysisState();scope=make_scope("subject", "module")
    solver=PointerSolver(state,Config(max_iterations=1000,enable_ai_intrinsics=False,
                                     entry_points=[],work_scheduling_policy="fair"),
                         ComposeProcessor([]),context_selector=ContextSelector(ContextPolicy.CALL_2))
    if mutation=="ledger_only":
        monkeypatch.setattr(state,"apply_unknown_effects",lambda *args:None)
    else:
        monkeypatch.setattr(UnknownEffects,"field_created",lambda *args:None)
    statement=IRCall(ast.parse("mystery()").body[0].value)
    call=CallConstraint(Variable("mystery"),(),frozenset(),None,CallSite(statement,"subject"))
    solver._mark_unknown_call(scope,call,"mutation control")
    obj=AbstractObject(scope.context,make_site(name="late"))
    field=state.get_field(scope,scope.context,obj,attr("later"))
    solver.solve_to_fixpoint()
    def acceptance():
        assert any(isinstance(value,UnknownValue) for value in state.get_points_to(field))
    with pytest.raises(AssertionError):
        acceptance()
