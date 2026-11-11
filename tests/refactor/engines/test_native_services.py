"""Actual service extraction, incremental replay and shared intrinsic cost."""
import json
from pathlib import Path
import subprocess
import sys

from pythonstan.analysis.contracts.work import SharedWorkLedger
from pythonstan.analysis.pointer.kcfa.solver import SolverQuery as CompatibilityQuery
from pythonstan.analysis.pointer.kcfa.query import SolverQuery
from pythonstan.analysis.pointer.kcfa.scheduler import NativeScheduler

ROOT=Path(__file__).resolve().parents[3]


def _probe(enabled,budget=6000,case="intrinsic_getattr"):
    command=[sys.executable,str(ROOT/"scripts/refactor/native_context_probe.py"),
        "--repo",str(ROOT),"--case",case,"--policy","2-cfa","--budget",str(budget),
        "--config-json",json.dumps({"enable_ai_intrinsics":enabled})]
    result=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    row=json.loads(result.stdout);assert row["status"]=="ok",row
    return row


def test_query_compatibility_reexports_single_implementation():
    assert CompatibilityQuery is SolverQuery
    assert SolverQuery.__module__.endswith(".query")


def test_intrinsic_actual_transfer_cost_is_counted_with_native_cost():
    plain,assisted=_probe(False),_probe(True)
    for row in (plain,assisted):
        stats=row["statistics"]
        assert stats["converged"]==1,row
        assert stats["global_work_used"]==sum(stats["work_by_kind"].values())
        assert stats["global_work_used"]<=stats["global_work_limit"]==6000
        assert {value["value"] for value in row["values"]["result"] if value["kind"]=="ConstantObject"}=={7}
    assert plain["statistics"]["ai_intrinsic_work"]==0
    assert assisted["statistics"]["ai_intrinsic_work"]>0,assisted
    assert assisted["statistics"]["intrinsic_bridge_work"]>0
    assert assisted["statistics"]["ai_intrinsic_seconds"]>0
    assert assisted["statistics"]["ai_intrinsic_seconds_scope"]=="subset_of_inclusive_native_phase"


def test_intrinsic_work_cannot_receive_a_free_budget_per_invocation():
    baseline=_probe(False)
    budget=baseline["statistics"]["global_work_used"]
    assisted=_probe(True,budget)
    stats=assisted["statistics"]
    assert stats["global_work_used"]<=budget
    assert stats["ai_intrinsic_work"]>0,assisted
    assert stats["native_selected_work"]<stats["global_work_used"]
    assert stats["converged"]==0 and stats["pending_work"]>0,assisted


def test_shared_work_ledger_denial_is_passive_and_no_counter_renewal():
    ledger=SharedWorkLedger(2)
    assert ledger.consume(kind="native_selected")
    assert ledger.consume(kind="abstract_transfer")
    assert not ledger.consume(kind="abstract_transfer")
    assert ledger.used==2 and ledger.remaining==0
    snapshot=ledger.snapshot();snapshot["by_kind"]["native_selected"]=100
    assert ledger.by_kind["native_selected"]==1


def test_source_runtime_metadata_comes_from_snapshot_without_rewriting_ir(tmp_path):
    from pythonstan.world import World
    from pythonstan.world.pipeline import Pipeline
    from pythonstan.compat.native_services import configure_native_services
    from pythonstan.analysis.pointer.kcfa.config import Config
    path=tmp_path/"subject.py"
    path.write_text("left=__file__\nright=__package__\nresult=__name__\n")
    pipeline=Pipeline(config={"filename":str(path),"project_path":str(tmp_path),
        "library_paths":[],"import_level":0,"mock_libs":False,"time_count":False,
        "analysis":[{"name":"pta","id":"PointerAnalysis","prev_analysis":["ir"],
                     "options":Config(max_iterations=2000,enable_ai_intrinsics=False,
                                      enable_synthetic_method_contexts=False,entry_points=[],log_level="ERROR").to_dict()}]})
    metadata={"subject":{"__file__":"/virtual/original.py","__name__":"logical.subject","__package__":"logical"}}
    configure_native_services(pipeline.analysis_manager,max_work=2000,source_metadata=metadata)
    metadata["subject"]["__file__"]="changed-after-injection"
    pipeline.run();query=pipeline.analysis_manager.get_results("pta").query();module=World().get_entry_module()
    for symbol,expected in (("left","/virtual/original.py"),("right","logical"),("result","logical.subject")):
        assert {obj.value for obj in query.points_to_at(symbol,module) if hasattr(obj,"value")}=={expected}
    assert module.get_filename()==str(path)
    assert module.get_qualname()=="subject"


def test_eager_publications_share_global_budget_and_cannot_renew_it():
    from types import SimpleNamespace
    from pythonstan.analysis.pointer.kcfa.analysis import PointerAnalysis
    from pythonstan.analysis.pointer.kcfa.config import Config
    from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
    from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
    from pythonstan.analysis.pointer.kcfa.processor import Processor
    from tests.pointer.kcfa.builders import make_scope
    from pythonstan.analysis.pointer.kcfa.points_to_set import reset_object_table
    # This unit creates a fresh run outside Pipeline; close the previous arena.
    reset_object_table()
    state=PointerAnalysisState();scope=make_scope("subject","module")
    config=Config(max_iterations=1000,enable_ai_intrinsics=False,builtin_bootstrap_policy="eager_inert",log_level="ERROR")
    solver=PointerSolver(state,config,Processor());solver.set_global_work_limit(2)
    driver=SimpleNamespace(state=state,solver=solver,kcfa_config=config)
    PointerAnalysis._initialize_builtins(driver,scope,scope.context)
    assert solver._bootstrap_publications==2
    assert solver.work_ledger.used==2
    solver.solve_to_fixpoint();solver.solve_to_fixpoint()
    assert solver.work_ledger.used==2
    assert solver._stats["converged"]==0 and solver._pending_work_count()>0


def test_eager_contract_rejects_custom_ledger_callbacks_before_calling_them():
    from types import SimpleNamespace
    from pythonstan.analysis.pointer.kcfa.analysis import PointerAnalysis
    from pythonstan.analysis.pointer.kcfa.config import Config
    from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
    from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
    from pythonstan.analysis.pointer.kcfa.processor import Processor
    from tests.pointer.kcfa.builders import make_scope
    state=PointerAnalysisState();scope=make_scope("subject","module");called=[]
    config=Config(max_iterations=1000,enable_ai_intrinsics=False,builtin_bootstrap_policy="eager_inert",log_level="ERROR")
    solver=PointerSolver(state,config,Processor())
    solver.work_ledger.consume=lambda **kwargs:called.append(kwargs) or True
    driver=SimpleNamespace(state=state,solver=solver,kcfa_config=config)
    PointerAnalysis._initialize_builtins(driver,scope,scope.context)
    assert solver._bootstrap_publications==0 and not called
    assert len(state._worklist)==65


def test_field_and_unresolved_mro_observation_do_not_create_solver_facts():
    from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
    from pythonstan.analysis.pointer.kcfa.query import SolverQuery
    from pythonstan.analysis.pointer.kcfa.unknown_tracker import UnknownTracker
    from pythonstan.analysis.pointer.kcfa.variable import Variable,VariableKind
    from pythonstan.analysis.pointer.kcfa.heap_model import attr
    from tests.pointer.kcfa.builders import make_scope
    state=PointerAnalysisState();scope=make_scope("subject.Child","class")
    state.set_internal_scope(scope.obj,scope)
    scope.stmt._kcfa_base_variables=(Variable("missing_base",VariableKind.GLOBAL),)
    query=SolverQuery(state,{},UnknownTracker())
    def snapshot():
        return (len(state._env),len(state._field_accesses),len(state._worklist),
                len(state.pointer_flow_graph.edges),len(state.constraints._constraints),
                dict(getattr(state,"_residual_effects",{})))
    before=snapshot()
    observation=query.field_observation(scope.obj,attr("never_requested"))
    assert observation["status"]=="not_requested" and not observation["points_to"]
    from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
    assert not query.field_of_objects(PointsToSet.singleton(scope.obj),"never_requested")
    projection=query.get_class_mro(scope.obj)
    assert not projection.order_resolved
    assert snapshot()==before
