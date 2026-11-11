"""Attribute algorithm laws and protocol effect regressions.

The attribute protocols follow Python's descriptor HOWTO and builtin docs:
https://docs.python.org/3.12/howto/descriptor.html
https://docs.python.org/3.12/library/functions.html#hasattr
These tests establish selected obligations, not whole-Python soundness.
"""

import ast

import pytest

from pythonstan.analysis.pointer.ai.api import AISummary, AISummaryEngine, AnalysisBudget, ArgBinding
from pythonstan.analysis.pointer.ai.domains.attributes import AttributeSemantics
from pythonstan.analysis.pointer.ai.domains.builtins import BuiltinAttributeSemantics
from pythonstan.analysis.pointer.ai.domains.callable import CallableVal, FunctionRef
from pythonstan.analysis.pointer.ai.domains.classinfo import ClassInfoCache
from pythonstan.analysis.pointer.ai.domains.descriptor import DescriptorRef, DescriptorSemantics, DescriptorVal
from pythonstan.analysis.pointer.ai.domains.mapslots import Presence, SlotMap
from pythonstan.analysis.pointer.ai.domains.strings import StrLattice
from pythonstan.analysis.pointer.ai.heap import AIAddr, AbsObj, ObjKind
from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
from pythonstan.analysis.pointer.ai.state import AIState, AbsVal, PredicateInfo
from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
from pythonstan.analysis.pointer.ai.value_domain import get_default_registry
from pythonstan.analysis.pointer.kcfa.context import CallStringContext
from pythonstan.analysis.pointer.kcfa.heap_model import FieldKind, attr, unknown
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite, InstanceObject
from pythonstan.ir.ir_statements import IRFunc, IRReturn, IRCall


def obj(name):
    return AbstractObject(CallStringContext(), AllocSite(name, AllocKind.OBJECT))


class Query:
    def __init__(self):
        self.fields = {}
        self.mros = {}
        self.reads = []

    def field_points_to(self, receiver, field, scope, context):
        self.reads.append((receiver, field))
        return self.fields.get((receiver, field), frozenset())

    def get_class_mro(self, cls):
        return self.mros.get(cls, [cls])

    def known_attribute_names(self, receiver, scope, context):
        return frozenset(field.name for (owner, field) in self.fields
                         if owner == receiver and field.kind == FieldKind.ATTRIBUTE)


def semantics(query=None, budget=None, callback=None):
    if callback is None:
        callback = lambda *_args: AbsVal.top()
    return BuiltinAttributeSemantics(query, None, None, callback, budget or AnalysisBudget())


def test_empty_points_to_is_bottom_and_preserves_join_identity():
    empty = AbsVal.from_addrs(frozenset())
    value = AbsVal.from_str("route")
    assert empty.is_bottom()
    assert empty.join(value) == value
    assert empty.leq(value)


def test_empty_pta_field_does_not_become_phantom_present_value():
    value, havoc = AIState(pta_query=Query()).read_field_value(obj("request"), attr("missing"), None, None)
    assert value.is_bottom() and not havoc


def test_query_failure_is_havoc_instead_of_evidence_of_absence():
    class FailedQuery(Query):
        def field_points_to(self, *_args):
            raise RuntimeError("unavailable")
    _, havoc = AIState(pta_query=FailedQuery()).read_field_value(obj("request"), attr("route"), None, None)
    assert havoc


def test_unknown_attribute_overlay_write_aliases_named_read():
    receiver, target = obj("receiver"), obj("target")
    state = AIState().write_field_value(receiver, unknown(), AbsVal.from_addrs(frozenset({target})))
    read, _ = state.read_field_value(receiver, attr("route"), None, None)
    assert target in read.addrs


def test_unknown_attribute_overlay_read_includes_named_writes():
    receiver, left, right = obj("receiver"), obj("left"), obj("right")
    state = AIState().write_field_value(receiver, attr("left"), AbsVal.from_addrs(frozenset({left})))
    state = state.write_field_value(receiver, attr("right"), AbsVal.from_addrs(frozenset({right})))
    read, _ = state.read_field_value(receiver, unknown(), None, None)
    assert {left, right} <= read.addrs


def test_named_attribute_does_not_read_unrelated_precise_overlay_fields():
    receiver, left, right = obj("receiver"), obj("left"), obj("right")
    state = AIState().write_field_value(receiver, attr("left"), AbsVal.from_addrs(frozenset({left})))
    state = state.write_field_value(receiver, attr("right"), AbsVal.from_addrs(frozenset({right})))
    result = semantics().handle_getattr(frozenset({receiver}), AbsVal.from_str("left"), None,
                                       state, SummaryAccumulator())
    assert left in result.addrs and right not in result.addrs


def test_named_pta_attribute_includes_unknown_name_writes():
    query = Query()
    receiver, residual = obj("receiver"), obj("residual")
    query.fields[(receiver, unknown())] = frozenset({residual})
    result, _ = AIState(pta_query=query).read_field_value(receiver, attr("route"), None, None)
    assert residual in result.addrs


@pytest.mark.parametrize("method", ["handle_getattr", "handle_hasattr"])
def test_attribute_query_invokes_getattr_hook_even_when_a_may_value_exists(method):
    query = Query()
    receiver, existing, hook, fallback = [obj(name) for name in ("receiver", "existing", "hook", "fallback")]
    query.fields[(receiver, attr("route"))] = frozenset({existing})
    query.fields[(receiver, attr("__getattr__"))] = frozenset({hook})
    seen = []
    def invoke(target, arguments, summary, bound_receiver):
        seen.append(target)
        summary.add_write(frozenset({receiver}), attr("observed"), frozenset({fallback}))
        return AbsVal.from_addrs(frozenset({fallback}))
    service = semantics(query, callback=invoke)
    effects = SummaryAccumulator()
    state = AIState(pta_query=query)
    if method == "handle_getattr":
        value = service.handle_getattr(frozenset({receiver}), AbsVal.from_str("route"), None, state, effects)
        assert {existing, fallback} <= value.addrs
    else:
        service.handle_hasattr(frozenset({receiver}), AbsVal.from_str("route"), effects, state)
    assert hook in seen
    assert effects.writes
    assert (frozenset({receiver}), attr("__getattr__")) in effects.reads


@pytest.mark.parametrize("default", [False, True])
def test_getattr_default_catches_only_the_current_lookup_attribute_error(default):
    effects = SummaryAccumulator()
    effects.add_may_raise("RuntimeError")
    service = semantics()
    service.handle_getattr(frozenset({obj("receiver")}), AbsVal.from_str("missing"),
                           AbsVal.none_val() if default else None, AIState(), effects)
    assert "RuntimeError" in effects.may_raise
    assert ("AttributeError" in effects.may_raise) is (not default)


def test_hasattr_catches_attribute_error_but_preserves_other_hook_effects():
    query = Query()
    receiver, hook, value = obj("receiver"), obj("hook"), obj("value")
    query.fields[(receiver, attr("__getattribute__"))] = frozenset({hook})
    def invoke(target, arguments, effects, bound_receiver):
        effects.add_may_raise("AttributeError")
        effects.add_may_raise("ValueError")
        effects.add_write(frozenset({receiver}), attr("side_effect"), frozenset({value}))
        return AbsVal.top()
    effects = SummaryAccumulator()
    effects.add_may_raise("AttributeError")  # earlier operation must be retained
    service = semantics(query, callback=invoke)
    service.handle_hasattr(frozenset({receiver}), AbsVal.from_str("route"), effects, AIState(pta_query=query))
    assert effects.may_raise == {"AttributeError", "ValueError"}
    assert effects.writes
    fresh = SummaryAccumulator()
    service.handle_hasattr(frozenset({receiver}), AbsVal.from_str("route"), fresh, AIState(pta_query=query))
    assert fresh.may_raise == {"ValueError"}


def test_generic_descriptor_get_and_set_are_executed_with_class_mro_lookup():
    query = Query()
    cls, descriptor_cls = obj("class"), obj("descriptor_class")
    instance = InstanceObject(CallStringContext(), AllocSite("instance", AllocKind.INSTANCE), cls)
    descriptor = InstanceObject(CallStringContext(), AllocSite("descriptor", AllocKind.INSTANCE), descriptor_cls)
    getter, setter, result = obj("get_method"), obj("set_method"), obj("result")
    query.fields[(cls, attr("route"))] = frozenset({descriptor})
    query.fields[(descriptor_cls, attr("__get__"))] = frozenset({getter})
    query.fields[(descriptor_cls, attr("__set__"))] = frozenset({setter})
    seen = []
    def invoke(target, arguments, effects, receiver):
        seen.append((target, arguments, receiver))
        effects.add_call("descriptor", frozenset({target}))
        return AbsVal.from_addrs(frozenset({result}))
    service = semantics(query, callback=invoke)
    effects = SummaryAccumulator()
    state = AIState(pta_query=query)
    value = service.handle_getattr(frozenset({instance}), AbsVal.from_str("route"), None, state, effects)
    service.handle_setattr(frozenset({instance}), AbsVal.from_str("route"), value, state, effects)
    assert result in value.addrs
    assert {getter, setter} <= {item[0] for item in seen}
    get_call = next(item for item in seen if item[0] == getter)
    assert get_call[1][1][1].addrs == frozenset({cls})
    assert get_call[2].addrs == frozenset({descriptor})
    assert (frozenset({descriptor_cls}), attr("__get__")) in effects.reads


def test_unresolved_generic_descriptor_is_incomplete():
    desc = DescriptorVal.from_tokens(frozenset({DescriptorRef(obj("descriptor"), has_get=True)}))
    effects = SummaryAccumulator()
    DescriptorSemantics(lambda *_args: AbsVal.top()).interpret_get(desc, AbsVal.top(), None, effects)
    assert effects.incomplete_reasons == {"unresolved_descriptor_get"}


def test_protocol_positional_parameters_use_callee_names_and_posonly_receiver(monkeypatch):
    scope = IRFunc("getter", ast.parse("def getter(this, /, instance, owner):\n    pass\n").body[0])
    callee = AbstractObject(CallStringContext(), AllocSite(scope, AllocKind.FUNCTION))
    receiver, instance, owner = [obj(name) for name in ("descriptor", "instance", "class")]
    captured = []
    def analyze(_engine, **kwargs):
        captured.extend(kwargs["arg_bindings"])
        return AISummary()
    monkeypatch.setattr(AISummaryEngine, "analyze_callee", analyze)
    interpreter = IRInterpreter(Query(), AnalysisBudget())
    interpreter.invoke_callee(callee, [("obj", AbsVal.from_addrs(frozenset({instance}))),
                                     ("type", AbsVal.from_addrs(frozenset({owner})))],
                              SummaryAccumulator(), AbsVal.from_addrs(frozenset({receiver})),
                              positional_args=True)
    assert {binding.param_name: binding.points_to for binding in captured} == {
        "this": frozenset({receiver}), "instance": frozenset({instance}), "owner": frozenset({owner})}


def test_known_name_filter_keeps_unknown_residual_and_hook_uncertainty():
    receiver, known, residual = obj("request"), obj("known"), obj("residual")
    state = AIState().write_field_value(receiver, attr("route_name"), AbsVal.from_addrs(frozenset({known})))
    state = state.write_field_value(receiver, unknown(), AbsVal.from_addrs(frozenset({residual})))
    name = AbsVal(components={"string": StrLattice.from_prefix("route_")})
    effects = SummaryAccumulator()
    result = semantics().handle_getattr(frozenset({receiver}), name, None, state, effects)
    assert {known, residual} <= result.addrs
    assert "dynamic_attribute_name" in effects.incomplete_reasons
    assert (frozenset({receiver}), unknown()) in effects.reads


def test_name_candidates_include_ai_heap_namespace():
    receiver, value = obj("receiver"), obj("value")
    slots = SlotMap.for_namespace().weak_update_str("route_name", AbsVal.from_addrs(frozenset({value})), Presence.PRESENT)
    address = AIAddr.from_kcfa_obj(receiver)
    state = AIState().set_obj(address, AbsObj(addr=address, kind=ObjKind.INSTANCE, slots=slots))
    candidates = semantics()._filter_known_attr_names(
        frozenset({receiver}), AbsVal(components={"string": StrLattice.from_prefix("route_")}), state, 10,
    )
    assert candidates == frozenset({"route_name"})


def test_filtered_dynamic_setattr_keeps_residual_writes():
    receiver, value = obj("request"), obj("value")
    state = AIState().write_field_value(receiver, attr("route_name"), AbsVal.from_str("old"))
    name = AbsVal(components={"string": StrLattice.from_prefix("route_")})
    effects = SummaryAccumulator()
    updated = semantics().handle_setattr(
        frozenset({receiver}), name, AbsVal.from_addrs(frozenset({value})), state, effects,
    )
    assert (receiver, unknown()) in updated.overlay
    assert effects.incomplete_reasons == {"dynamic_attribute_name"}


@pytest.mark.parametrize("cap", [0, 1, 2])
def test_finite_name_budget_never_drops_known_field_values(cap):
    query = Query()
    receiver, left, right = obj("receiver"), obj("left"), obj("right")
    query.fields[(receiver, attr("left"))] = frozenset({left})
    query.fields[(receiver, attr("right"))] = frozenset({right})
    effects = SummaryAccumulator()
    result = semantics(query, AnalysisBudget(max_const_strings=cap)).handle_getattr(
        frozenset({receiver}), AbsVal.from_strs(frozenset({"left", "right"})), None,
        AIState(pta_query=query), effects,
    )
    assert {left, right} <= result.addrs
    assert (not effects.incomplete_reasons) is (cap >= 2)


def test_class_namespace_cache_observes_later_alias_writes():
    cls, old, new = obj("class"), obj("old"), obj("new")
    cache = ClassInfoCache(Query(), AnalysisBudget())
    state = AIState().write_field_value(cls, attr("route"), AbsVal.from_addrs(frozenset({old})))
    assert old in cache.get_info(cls, state).get_namespace_value("route").addrs
    updated = state.write_field_value(cls, attr("route"), AbsVal.from_addrs(frozenset({new})))
    assert {old, new} <= cache.get_info(cls, updated).get_namespace_value("route").addrs
    assert cache.get_info(cls, state).get_namespace_value("unrelated") is None


def test_class_may_attribute_does_not_hide_possible_base_attribute():
    query = Query()
    cls, base, current, inherited = [obj(name) for name in ("class", "base", "current", "inherited")]
    query.mros[cls] = [cls, base]
    query.fields[(cls, attr("route"))] = frozenset({current})
    query.fields[(base, attr("route"))] = frozenset({inherited})
    service = AttributeSemantics(query, None, None, lambda *_args: AbsVal.top())
    value, _, _ = service.read_class_attr_with_classinfo(cls, "route", AIState(pta_query=query), SummaryAccumulator())
    assert {current, inherited} <= value.addrs


def test_class_lookup_preserves_ancestors_past_the_old_fixed_mro_prefix():
    query = Query()
    classes = [obj("class_" + str(i)) for i in range(12)]
    query.mros[classes[0]] = classes
    inherited = obj("inherited")
    query.fields[(classes[-1], attr("route"))] = frozenset({inherited})
    service = AttributeSemantics(query, None, None, lambda *_args: AbsVal.top())
    value, _, _ = service.read_class_attr_with_classinfo(classes[0], "route", AIState(pta_query=query), SummaryAccumulator())
    assert inherited in value.addrs


def test_engine_token_budgets_are_isolated_and_widening_respects_state_budget():
    registry = get_default_registry()
    default_limit = registry.get("callable").max_tokens
    AISummaryEngine(Query(), AnalysisBudget(max_ho_tokens=1))
    AISummaryEngine(Query(), AnalysisBudget(max_ho_tokens=50))
    assert registry.get("callable").max_tokens == default_limit
    values = [AbsVal(components={"callable": CallableVal.from_tokens(frozenset({FunctionRef(obj(name))}))})
              for name in ("left", "right")]
    low = AIState(budget=AnalysisBudget(max_ho_tokens=1)).set_var("f", values[0])
    high = AIState(budget=AnalysisBudget(max_ho_tokens=5)).set_var("f", values[0])
    incoming = AIState().set_var("f", values[1])
    assert low.widen(incoming).get_var("f").get_component("callable").is_top()
    assert len(high.widen(incoming).get_var("f").get_component("callable").tokens) == 2


def test_callee_cache_does_not_reuse_unversioned_mutable_pta(monkeypatch):
    from pythonstan.analysis.pointer.ai.solver import AISolver

    query = Query()
    targets = [obj("first"), obj("later")]
    observed = []
    def solve(_solver):
        observed.append(True)
        return AISummary(ret=frozenset({targets[len(observed) - 1]}))
    monkeypatch.setattr(AISolver, "solve", solve)
    engine = AISummaryEngine(query)
    scope = object()
    assert engine.analyze_callee(scope, None, []).ret == frozenset({targets[0]})
    assert engine.analyze_callee(scope, None, []).ret == frozenset({targets[1]})


def test_versioned_cache_binds_snapshot_caller_and_recursion_budget(monkeypatch):
    from pythonstan.analysis.pointer.ai.solver import AISolver

    class VersionedQuery(Query):
        revision = 0
        def analysis_cache_token(self):
            return self.revision
    query = VersionedQuery()
    calls = []
    def solve(_solver):
        calls.append(True)
        return AISummary()
    monkeypatch.setattr(AISolver, "solve", solve)
    engine = AISummaryEngine(query)
    scope, caller = object(), object()
    engine.analyze_callee(scope, None, [], caller_scope=caller)
    engine.analyze_callee(scope, None, [], caller_scope=caller)
    assert len(calls) == 1
    engine.analyze_callee(scope, None, [], caller_scope=object())
    engine.analyze_callee(scope, None, [], caller_scope=caller, recursion_depth=1)
    query.revision += 1
    engine.analyze_callee(scope, None, [], caller_scope=caller)
    assert len(calls) == 4


def test_alias_heap_write_invalidates_a_saved_membership_predicate():
    receiver, value = obj("dictionary"), obj("value")
    state = AIState().set_var("dictionary", AbsVal.from_addrs(frozenset({receiver})))
    state = state.set_var("alias", AbsVal.from_addrs(frozenset({receiver})))
    state = state.add_predicate("condition", PredicateInfo("dict_contains", "dictionary", key_literal="key"))
    updated = state.write_field_value(next(iter(state.get_var("alias").addrs)), attr("key"),
                                      AbsVal.from_addrs(frozenset({value})))
    assert updated.get_predicate("condition") is None
    assert state.get_predicate("condition") is not None


def test_slot_narrowing_recovers_a_spilled_precise_key():
    from pythonstan.analysis.pointer.ai.domains.mapslots import Slot

    precise = SlotMap.for_namespace().weak_update_str("route", AbsVal.from_str("target"), Presence.PRESENT)
    widened = SlotMap(default=Slot.maybe(AbsVal.from_str("target")))
    assert precise.leq(widened)
    narrowed = widened.narrow(precise)
    assert precise.leq(narrowed)
    assert narrowed.get_str_value("route").str_const == frozenset({"target"})


def test_slot_order_checks_constraints_on_the_right_hand_side():
    broad = SlotMap.top()
    refined = SlotMap.top().strong_update_str("route", AbsVal.from_str("target"), Presence.PRESENT)
    assert refined.leq(broad)
    assert not broad.leq(refined)


def test_narrowing_preserves_initial_values_at_entry_with_backedge():
    from pythonstan.analysis.pointer.ai._cfg_adapter import CFGAdapter
    from pythonstan.analysis.pointer.ai._narrowing import NarrowingPass
    from pythonstan.graph.cfg.base_block import BaseBlock
    from pythonstan.graph.cfg.cfg import ControlFlowGraph
    from pythonstan.graph.cfg.edges import NormalEdge

    block = BaseBlock(0)
    cfg = ControlFlowGraph(block)
    cfg.add_blk(block)
    cfg.add_edge(NormalEdge(block, block))
    initial = AIState().set_var("x", AbsVal.from_strs(frozenset({"initial", "backedge"})))
    backedge = AIState().set_var("x", AbsVal.from_str("backedge"))
    states = {0: initial}
    NarrowingPass(CFGAdapter(cfg), IRInterpreter(Query(), AnalysisBudget()), states,
                  {(0, 0): backedge}, {0: backedge}).run(2)
    assert states[0].get_var("x").str_const == frozenset({"initial", "backedge"})


def test_address_top_is_a_true_lattice_upper_bound_and_meet_identity():
    top = AbsVal.top()
    values = [AbsVal.bottom(), AbsVal.from_str("value"), AbsVal.from_int(1),
              AbsVal.from_addrs(frozenset({obj("address")}))]
    for value in values:
        assert value.leq(top)
        assert value.meet(top) == value
        assert top.meet(value) == value
        assert value.join(top).addr_top
        assert value.join(top).leq(top) and top.leq(value.join(top))
        assert value.widen(top).addr_top
    assert not top.leq(values[-1])


def test_address_top_survives_refinement_and_distinguishes_cache_fingerprint():
    unknown = AbsVal.top().join(AbsVal.from_addrs(frozenset({obj("known_witness")})))
    assert unknown.with_component("string", StrLattice.from_prefix("route_")).addr_top
    assert unknown.with_components({}).addr_top
    finite = AbsVal(addrs=unknown.addrs, components=unknown.components)
    assert unknown.fingerprint() != finite.fingerprint()
    assert not unknown.with_addrs(frozenset()).addr_top
    with pytest.raises(TypeError, match="addr_top"):
        AbsVal(addr_top=1)


def test_unknown_address_witnesses_cannot_refine_callable_top_to_a_finite_set():
    scope = IRFunc("function", ast.parse("def function():\n    pass\n").body[0])
    function = AbstractObject(CallStringContext(), AllocSite(scope, AllocKind.FUNCTION))
    unknown = AbsVal.top().join(AbsVal.from_addrs(frozenset({function})))
    reduced = AIState().set_var("callable", unknown).get_var("callable")
    assert reduced.addr_top and reduced.get_component("callable").is_top()


def test_unknown_return_addresses_cannot_be_serialized_as_a_complete_summary():
    from pythonstan.analysis.pointer.ai.value_domain import to_points_to

    unknown = AbsVal.top()
    state = AIState().set_var("value", unknown)
    effects = SummaryAccumulator()
    statement = IRReturn(ast.Return(value=ast.Name(id="value", ctx=ast.Load())))
    IRInterpreter(Query(), AnalysisBudget()).transfer(statement, state, effects)
    assert {"unknown_address_input", "unknown_return_addresses"} <= effects.incomplete_reasons
    with pytest.raises(ValueError, match="Address Top"):
        to_points_to(unknown)


def test_known_call_witness_does_not_hide_additional_unknown_call_targets(monkeypatch):
    scope = IRFunc("function", ast.parse("def function():\n    pass\n").body[0])
    function = AbstractObject(CallStringContext(), AllocSite(scope, AllocKind.FUNCTION))
    state = AIState().set_var("function", AbsVal.top().join(AbsVal.from_addrs(frozenset({function}))))
    interpreter = IRInterpreter(Query(), AnalysisBudget())
    # A normal target can resolve successfully; the input still has address Top.
    monkeypatch.setattr(AISummaryEngine, "analyze_callee", lambda *_args, **_kwargs: AISummary())
    effects = SummaryAccumulator()
    interpreter.transfer(IRCall(ast.parse("function()").body[0].value), state, effects)
    assert "unknown_address_input" in effects.incomplete_reasons


def test_intrinsic_unknown_address_input_is_explicitly_incomplete():
    result = AISummaryEngine(Query()).analyze_intrinsic("getattr", None, [
        ArgBinding("receiver", frozenset(), AbsVal.top()),
        ArgBinding("name", frozenset(), AbsVal.from_str("route")),
    ], target_name="result")
    assert not result.is_complete
    assert "unknown_address_input" in result.incomplete_reasons


def test_nested_calls_inherit_generic_solver_policy_without_caller_block_ids(monkeypatch):
    from pythonstan.analysis.pointer.ai.solver import SolverConfig

    scope = IRFunc("function", ast.parse("def function():\n    pass\n").body[0])
    function = AbstractObject(CallStringContext(), AllocSite(scope, AllocKind.FUNCTION))
    policy = SolverConfig(enable_trace_partitioning=True, max_total_traces=7,
                          loop_unroll=3, merge_points={2}, loop_iters={2: 4})
    captured = []
    def analyze(engine, **_kwargs):
        captured.append(engine.solver_config)
        return AISummary()
    monkeypatch.setattr(AISummaryEngine, "analyze_callee", analyze)
    interpreter = IRInterpreter(Query(), AnalysisBudget(), solver_config=policy)
    interpreter.invoke_callee(function, [], SummaryAccumulator())
    assert captured[0].enable_trace_partitioning
    assert captured[0].max_total_traces == 7 and captured[0].loop_unroll == 3
    assert not captured[0].merge_points and not captured[0].loop_iters
    assert policy.merge_points == {2} and policy.loop_iters == {2: 4}
