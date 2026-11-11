"""Real engine boundaries, full-store state, incremental delivery and relations."""
from dataclasses import replace
import pytest
from pythonstan import Source,ValueQuery,analyze
from pythonstan.config import resolve_config,ConfigurationError
from pythonstan.inputs import snapshot_target
from pythonstan.analysis.contracts import EntryScenario,SourceModule
from pythonstan.analysis.domains.values import Value,ValueOperations
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.engines.abstract.state import Store,HeapObject,Flow,Frame
from pythonstan.analysis.composition.plan import build_plan
from pythonstan.analysis.composition.hybrid import configure_driver
from pythonstan.analysis.composition.state_adapter import FullStateAdapter,reabstract_state
from pythonstan.analysis.composition.oracle import IncrementalInclusionStore,IncrementalCandidateOracle


def run_source(source,config):
    target=Source(source,module="example");effective=resolve_config(target,config=config);snapshot=snapshot_target(target,effective)
    plan=build_plan(snapshot,effective)
    driver=InterproceduralDriver(snapshot.module_map,options=effective.to_dict())
    configure_driver(driver,plan=plan,source_digest=snapshot.source_digest,config=effective)
    return driver,driver.run(snapshot.scenarios).to_dict()


def test_real_ai_native_ai_heap_and_scalar_boundaries():
    source="def f():\n    box={'value':'before'}\n    box['value']='after'\n    value=box['value']\n    return value\nresult=f()\n"
    config={"engine":"hybrid","regions":[{"selector":{"module":"example","qualname":"example.f","kind":"block","line_start":3},"engine":"native"},{"selector":{"module":"example","qualname":"example.f","kind":"block","line_start":4},"engine":"native"}]}
    driver,result=run_source(source,config)
    assert result["values"]["example"]["result"]["constants"]==["after"]
    assert result["solver_status"]=="converged" and result["coverage_status"]=="closed_under_profile"
    phases=result["phase_trace"]
    assert sum(p["phase"]=="bounded_region_inclusion" for p in phases)>=2
    assert any(p.get("whole_program_prepass") is False for p in phases)
    assert result["stats"]["native_region_work"]>0
    assert result["stats"]["oracle"]["publications"]>0
    assert any(p.get("input_heap_objects",0)>0 for p in phases)
    assert driver.region_scheduler.native.runs>=2


def test_native_boundary_carries_cells_and_exception_outcome():
    source="def outer():\n    value='before'\n    def inner():\n        nonlocal value\n        value='after'\n    inner()\n    return value\nresult=outer()\n"
    _,result=run_source(source,{"engine":"ai_primary","regions":[{"selector":{"module":"example","qualname":"example.outer.inner","kind":"block","line_start":5},"engine":"native"}]})
    assert result["values"]["example"]["result"]["constants"]==["after"]


def test_unsupported_native_call_rejected_before_work():
    source="def f():\n    return len([])\nresult=f()\n"
    with pytest.raises(ConfigurationError,match="expression Call"):
        run_source(source,{"engine":"ai_primary","regions":[{"selector":{"module":"example","qualname":"example.f","kind":"block","line_start":2},"engine":"native"}]})


def test_same_engine_domain_boundary_reabstracts_value_and_heap():
    source="value=1 if flag else 3\nresult=value\n"
    config={"engine":"ai_primary","inputs":{"bindings":{"example.flag":{"type":"bool","top":True}}},"abstract":{"solver_options":{"max_traces_per_block":1}},"regions":[{"selector":{"module":"example","kind":"block","line_start":2},"engine":"ai_primary","abstract":{"int_domain_max_consts":1}}]}
    _,result=run_source(source,config)
    value=result["values"]["example"]["result"]
    assert value["integer_interval"]=={"lower":1,"upper":3,"parity":"odd"}
    assert result["stats"]["domain_region_adaptations"]>0
    assert not any(p["phase"]=="bounded_region_inclusion" for p in result["phase_trace"])


def test_full_state_roundtrip_and_env_only_mutant_detection():
    ops=ValueOperations();state=Store(cells={"cell":Value.ref("box")},heap={"box":HeapObject("dict",{"value":Value.constant("after")},metadata={"capture":Value.ref("box")})},initialized={"example":"initializing"},writes={("heap","box","value")},events=(("cleanup","seen"),),obligations=frozenset(("residual",)))
    flow=Flow(state,"raise",Value.constant("ValueError"),"ValueError",continuation=("resume",),cleanups=(("finally",),))
    adapter=FullStateAdapter();boundary=adapter.encode(flow);copy=adapter.decode(boundary)
    assert adapter.check_roundtrip(flow,ops)
    assert copy.continuation==flow.continuation and copy.cleanups==flow.cleanups
    assert copy.store.heap["box"].metadata==flow.store.heap["box"].metadata
    boundary.heap={};mutant=adapter.decode(boundary)
    assert not flow.store.leq(mutant.store,ops)
    assert copy.store.heap["box"].fields["value"].exact_constants()==["after"]


def test_incremental_late_edges_and_fields_are_actually_consumed():
    kernel=IncrementalInclusionStore();kernel.add_fact("source",Value.ref("box"));kernel.add_fact(("field","box","value"),Value.constant("before"));kernel.solve()
    kernel.add_load("source","value","target");kernel.solve()
    assert kernel.value("target").exact_constants()==["before"]
    kernel.add_fact(("field","box","value"),Value.constant("after"));kernel.solve()
    assert set(kernel.value("target").exact_constants())=={"before","after"}
    kernel.add_fact("other",Value.ref("box"));kernel.add_edge("other","source");kernel.solve()
    assert not kernel.queue and kernel.statistics()["late_edge_replays"]>0


def test_wrong_identity_rejected_and_partial_empty_never_excludes():
    source=SourceModule("example","example.py","def f(x): return x\nresult=f('after')\n")
    driver=InterproceduralDriver([source],{"resources":{"max_work":10000}})
    oracle=IncrementalCandidateOracle(source_digest=driver.source_digest,profile="python-core-v1")
    with pytest.raises(ValueError,match="source identity"):oracle.bind(driver,source_digest="source-b")
    answer=oracle.candidates("not_yet_known")
    assert answer.coverage=="partial" and answer.values.is_bottom() and not answer.permits_exclusion()
    oracle.bind(driver);driver.oracle=oracle
    result=driver.run((EntryScenario("example"),)).to_dict()
    assert result["values"]["example"]["result"]["constants"]==["after"]


def test_partial_oracle_new_callee_consumed_without_removing_unknown_effects():
    source=SourceModule("example","example.py","box={'value':'before'}\ndef known():\n    box['value']='after'\nunknown()\nresult=box['value']\n")
    options={"resources":{"max_work":10000},"inputs":{"bindings":{"example.unknown":{"top":True}}}}
    driver=InterproceduralDriver([source],options)
    oracle=IncrementalCandidateOracle(source_digest=driver.source_digest,profile="python-core-v1");oracle.bind(driver);driver.oracle=oracle
    identifier=f"function:{driver.point(driver.trees['example'].body[1])}@"
    oracle.publish_cell("global:example:unknown",Value.ref(identifier),"validated_may_candidate")
    result=driver.run((EntryScenario("example"),)).to_dict()
    assert any(row["callee"]=="example.known" for row in result["calls"])
    assert any(row["callee"]=="<callback>" for row in result["calls"])
    assert result["coverage_status"]=="open" and result["values"]["example"]["result"]["top"]
    assert result["stats"]["oracle_values_consumed"]>0
    assert result["stats"]["oracle"]["nonempty_candidate_reads"]>0


RELATION_SOURCE="def identity(payload):\n    return payload\ndef box(payload):\n    return {'value':payload}\ndef dispatch(key,payload):\n    table={'read':identity,'write':box}\n    return table[key](payload)\nresult=dispatch(key,'after')\n"

def relation_answer(enabled,source=RELATION_SOURCE):
    q=ValueQuery(module="example",symbol="result",point="module_exit")
    result=analyze(Source(source,module="example"),config={"engine":"ai_primary","inputs":{"bindings":{"example.key":{"type":"str","top":True}}},"abstract":{"enable_value_partition":enabled}},queries=(q,"stats"))
    return result,result.query(q).value


def test_per_value_partition_preserves_key_handler_relation():
    before,before_value=relation_answer(False);after,after_value=relation_answer(True)
    pairs=lambda value:{(row["key"],row["handler"]) for row in value["relations"]}
    assert len(pairs(before_value))==4
    assert pairs(after_value)=={("'read'","example.identity"),("'write'","example.box")}
    assert set(after_value["constants"])=={"after"} and after_value["objects"]
    assert before.solver_status==after.solver_status=="converged"
    assert before.coverage_status==after.coverage_status=="closed_under_profile"
    assert before.proof_status==after.proof_status=="not_requested"


def test_partition_tokens_survive_copy_alias_and_return():
    source=RELATION_SOURCE.replace("return table[key](payload)","alias=table\n    handler=alias[key]\n    copied=handler\n    return copied(payload)")
    _,value=relation_answer(True,source)
    assert {(row["key"],row["handler"]) for row in value["relations"]}=={("'read'","example.identity"),("'write'","example.box")}


def test_shadowed_builtin_join_retains_user_and_builtin_alternatives():
    source="if flag:\n    def int(x):\n        return 'user'\nresult=int(7)\n"
    _,result=run_source(source,{"engine":"ai_primary","inputs":{"bindings":{"example.flag":{"type":"bool","top":True}}},"abstract":{"solver_options":{"max_traces_per_block":1}}})
    assert set(result["values"]["example"]["result"]["constants"])=={"user",7}


def test_definition_time_defaults_are_state_not_mutable_code_registry():
    source="if flag:\n    x=1\nelse:\n    x=2\ndef f(y=x):\n    return y\nresult=f()\n"
    _,result=run_source(source,{"engine":"ai_primary","inputs":{"bindings":{"example.flag":{"type":"bool","top":True}}}})
    assert set(result["values"]["example"]["result"]["constants"])=={1,2}
    assert result["coverage_status"]=="closed_under_profile"


@pytest.mark.parametrize("source",["result=1e309\n","result=float('nan')\n","result=float('-inf')\n"])
def test_nonfinite_float_has_json_safe_covering_typed_abstraction(source):
    q=ValueQuery(module="example",symbol="result",point="module_exit")
    result=analyze(Source(source,module="example"),config={"engine":"ai_primary"},queries=(q,))
    value=result.query(q).value
    assert not value["top"] and not value["bottom"] and "float" in value["types"]
    assert not value["constants"] and "float" in value["top_sorts"]
    import json
    json.dumps(result.to_dict(),allow_nan=False)


def test_global_work_budget_charges_ast_oracle_and_native_region():
    source="box={'value':'before'}\nbox['value']='after'\nresult=box['value']\n"
    config={"engine":"hybrid","resources":{"max_work":10},"regions":[{"selector":{"module":"example","kind":"block","line_start":2},"engine":"native"}]}
    _,result=run_source(source,config)
    assert result["solver_status"]=="budget_exhausted"
    assert result["stats"]["work"]<=10
    assert result["stats"].get("oracle_work",0)>0
    assert result["stats"].get("native_region_work",0)>0
    assert result["stats"]["work"]==sum(result["stats"].get(key,0) for key in ("ast_work","oracle_work","native_region_work"))


def test_unknown_callback_havocs_exposed_closure_cells():
    source="def outer():\n    value='before'\n    def callback():\n        nonlocal value\n        value='after'\n    unknown(callback)\n    return value\nresult=outer()\n"
    _,result=run_source(source,{"engine":"ai_primary","inputs":{"bindings":{"example.unknown":{"top":True}}}})
    assert result["coverage_status"]=="open"
    assert result["values"]["example"]["result"]["top"]
    assert result["effects"]["havoc"]


def test_unknown_external_import_applies_heap_globals_callbacks_and_exceptions():
    source="box={'value':'before'}\nimport opaque_external\nresult=box['value']\n"
    _,result=run_source(source,{"engine":"ai_primary"})
    assert result["coverage_status"]=="open" and result["values"]["example"]["result"]["top"]
    assert result["effects"]["havoc"] and "BaseException" in result["effects"]["exceptions"]
    assert any(call["callee"]=="<callback>" for call in result["calls"])


def test_demand_prioritization_keeps_all_root_scenarios_pending():
    sources=[SourceModule("slow","slow.py","x=0\nwhile x<1000:\n    x=x+1\n"),SourceModule("wanted","wanted.py","result='after'\n")]
    driver=InterproceduralDriver(sources,{"resources":{"max_work":10},"queries":[{"kind":"value","module":"wanted","symbol":"result","point":"module_exit"}]})
    result=driver.run((EntryScenario("slow"),EntryScenario("wanted"))).to_dict()
    assert result["values"]["wanted"]["result"]["constants"]==["after"]
    assert result["solver_status"]=="budget_exhausted" and result["stats"]["root_scenarios"]==2
    assert {module for _,module,_,_,_ in driver.root_keys}=={"slow","wanted"}


def test_iterator_position_is_part_of_input_equation_identity():
    source="def step(it):\n    return next(it)\nit=iter([1,2])\nresult=[]\nfor _ in range(2):\n    result.append(step(it))\n"
    _,result=run_source(source,{"engine":"ai_primary"})
    assert result["solver_status"]=="converged" and result["coverage_status"]=="closed_under_profile"
    assert result["values"]["example"]["result"]["items"]==[1,2]


def test_semantic_metadata_join_is_not_stale_exact_state():
    ops=ValueOperations();before=Store(heap={"it":HeapObject("iterator",metadata={"index":0,"container":Value.ref("items")})})
    after=before.copy();after.heap["it"].metadata["index"]=1
    assert not after.leq(before,ops)
    joined=before.join(after,ops)
    assert joined.heap["it"].havoc and before.leq(joined,ops) and after.leq(joined,ops)


def test_large_string_repeat_is_bounded_before_host_allocation(monkeypatch):
    from pythonstan.analysis.domains import values as module
    original=module.operator.mul
    calls=[]
    def bounded(a,b):
        calls.append((a,b))
        assert not (type(a) is str and type(b) is int and b>10000)
        return original(a,b)
    monkeypatch.setattr(module.operator,"mul",bounded)
    ops=ValueOperations();value,errors=ops.scalar_binary(__import__('ast').Mult(),Value.constant('a'),Value.constant(10**12))
    assert not calls and value.strings.has_unknown and not value.top
    negative,_=ops.scalar_binary(__import__('ast').Mult(),Value.constant('a'),Value.constant(-(10**12)))
    assert negative.exact_constants()==['']


@pytest.mark.parametrize(('future','expected'),[('',1),('from __future__ import annotations\n',0)])
def test_function_annotations_evaluate_at_definition_unless_future(future,expected):
    source=future+"flag=[]\ndef mark():\n    flag.append(1)\n    return int\ndef f(x: mark()):\n    pass\nresult=len(flag)\n"
    _,result=run_source(source,{"engine":"ai_primary"})
    assert result["values"]["example"]["result"]["constants"]==[expected]
    assert result["coverage_status"]=="closed_under_profile"


def test_dynamic_namespace_covers_new_global_and_deleted_global():
    q=ValueQuery(module="example",symbol="new_name",point="module_exit")
    result=analyze(Source("exec(code)\n",module="example"),config={"engine":"ai_primary","inputs":{"bindings":{"example.code":{"type":"str","top":True}}}},queries=(q,))
    assert result.query(q).value["top"] and result.query(q).value["may_unbound"]
    source="x='before'\nexec(code)\ntry:\n    x\nexcept NameError:\n    result='after'\n"
    _,raw=run_source(source,{"engine":"ai_primary","inputs":{"bindings":{"example.code":{"type":"str","top":True}}}})
    assert 'after' in raw["values"]["example"]["result"]["constants"] or raw["values"]["example"]["result"]["top"]
    assert raw["coverage_status"]=="open"


def test_post_havoc_strong_assignment_restores_known_binding():
    source="exec(code)\nresult='after'\n"
    _,raw=run_source(source,{"engine":"ai_primary","inputs":{"bindings":{"example.code":{"type":"str","top":True}}}})
    assert raw["values"]["example"]["result"]["constants"]==['after'] and not raw["values"]["example"]["result"]["top"]


def test_optional_source_program_rejects_wrong_session_identity():
    from pythonstan.frontend.source import parse_sources
    first=SourceModule('example','example.py','result=1\n');second=SourceModule('example','example.py','result=2\n')
    with pytest.raises(ValueError,match='input identity'):
        InterproceduralDriver([second],program=parse_sources([first]))
