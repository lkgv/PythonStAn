"""The retired entry points exchange real Source/Store states on one engine."""
from dataclasses import replace
from pathlib import Path
import json
import math
import subprocess
import sys

import pytest

from pythonstan.analysis.contracts import SourceModule
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.engines.abstract.state import Frame, HeapObject, Store
from pythonstan.compat.shared_ai import LegacyStateBridge, SharedDriver, options, BoundaryUnavailable
from pythonstan.compat.legacy_ai.api import AISummaryEngine, ArgBinding, AnalysisBudget
from pythonstan.compat.legacy_ai.state import AIState
from pythonstan.compat.legacy_ai.value_domain import AbsVal
from pythonstan.compat.legacy_ai.heap import AIAddr, AbsObj, ObjKind
from pythonstan.compat.legacy_ai.domains.mapslots import SlotMap, Slot, Presence
from pythonstan.analysis.pointer.kcfa.context import CallStringContext
from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite, BuiltinInstanceObject, BuiltinFunctionObject, ConstantObject, DictObject
from pythonstan.analysis.pointer.kcfa.heap_model import attr, key, unknown
from tests.analysis.legacy_ai_support import analyze_program, solve_source, terminal_state, EmptyPtaQuery


def bridge(initial=None):
    budget=AnalysisBudget(strict_exceptions=True,enable_narrowing=False)
    driver=SharedDriver((SourceModule('subject','subject.py',''),),options(budget))
    state=initial if initial is not None else AIState()
    return LegacyStateBridge(EmptyPtaQuery(),budget,CallStringContext(),None,state,driver)


def invoke(program,name,bindings=(),state=None):
    scope=program.scope(name)
    contexts=program.query._state.get_scopes_for_ir(scope)
    context=contexts[0] if contexts else program.query._state.get_scopes_for_ir(program.module)[0]
    budget=AnalysisBudget(strict_exceptions=True,enable_narrowing=False,max_iterations=1000)
    return AISummaryEngine(program.adapter,budget).analyze_callee(
        scope_ir=scope,call_context=context.context,caller_scope=context,
        arg_bindings=list(bindings),caller_state=state)


def typed_string(summary,expected):
    assert summary.is_complete,summary.incomplete_reasons
    assert summary.ret_value.get_const_strs()==frozenset((expected,))
    assert not summary.ret_value.addr_top


def test_none_string_none_float_and_residual_keys_survive_local_state():
    native=DictObject(CallStringContext(),AllocSite('caller-dict',AllocKind.DICT))
    initial=AIState().set_var('d',AbsVal.from_addrs(frozenset((native,))))
    initial=initial.write_field_value(native,key(None),AbsVal.from_str('none'))
    initial=initial.write_field_value(native,key('None'),AbsVal.from_str('string'))
    initial=initial.write_field_value(native,key(2.5),AbsVal.from_str('float'))
    initial=initial.write_field_value(native,unknown(),AbsVal.from_str('residual'))
    converter=bridge(initial);frame=Frame('subject','subject',(),'entry',frozenset(('d',)))
    store=converter.import_state(frame);identifier=next(iter(store.cells[frame.cell('d')].refs));obj=store.heap[identifier]
    assert obj.fields[None].exact_constants()==['none']
    assert obj.fields['None'].exact_constants()==['string']
    assert obj.fields[2.5].exact_constants()==['float']
    assert obj.default.exact_constants()==['residual']
    output=converter.old_state(store,frame)
    again=bridge(output);second=again.import_state(frame);restored=second.heap[next(iter(second.cells[frame.cell('d')].refs))]
    assert restored.fields[None].exact_constants()==['none']
    assert restored.fields['None'].exact_constants()==['string']
    assert restored.fields[2.5].exact_constants()==['float']
    assert restored.default.exact_constants()==['residual']


@pytest.mark.parametrize('number',[2.5,-0.0,0.0])
def test_scalar_native_projection_roundtrips_float_bits(number):
    converter=bridge();value=Value.constant(number)
    old=converter.old_value(value,Store())
    assert converter.value(old)==value
    constants=[obj.value for obj in old.addrs if isinstance(obj,ConstantObject)]
    assert constants and type(constants[0]) is float
    assert math.copysign(1,constants[0])==math.copysign(1,number)


def test_shared_roundtrip_keeps_cycles_aliases_presence_and_active_exception():
    converter=bridge();frame=Frame('subject','subject',(),'entry',frozenset(('left','right')))
    store=Store(cells={frame.cell('left'):Value.ref('cycle'),frame.cell('right'):Value.ref('cycle'),'$active_exception':Value.ref('error')})
    store.heap['cycle']=HeapObject('dict',{None:Value.ref('cycle'),'maybe':Value.constant('present')},Value.constant('fallback'),{'maybe'},metadata={'length':2})
    store.heap['error']=HeapObject('exception',metadata={'exception_name':'ValueError','exception_types':('ValueError',)},class_ids=frozenset(('builtin:ValueError',)))
    old=converter.old_state(store,frame)
    restored_converter=bridge(old);restored=restored_converter.import_state(frame)
    left=restored.cells[frame.cell('left')];right=restored.cells[frame.cell('right')]
    assert left.refs==right.refs and len(left.refs)==1
    obj=restored.heap[next(iter(left.refs))]
    assert obj.fields[None].refs==left.refs
    assert 'maybe' in obj.maybe_missing
    assert obj.default.exact_constants()==['fallback']
    assert obj.metadata['length']==2
    error=restored.cells['$active_exception'];assert restored.heap[next(iter(error.refs))].metadata['exception_name']=='ValueError'
    assert restored.cells['$active_exception_type'].exact_constants()==['ValueError']


def test_caller_only_none_key_write_is_visible_through_nested_source_call(tmp_path):
    source="""def read(d):
    return d[None] + d['None']
def outer(d):
    return read(d)
d={None:'before', 'None':'before'}
result=outer(d)
"""
    with analyze_program(tmp_path,source,ai_options={'strict_exceptions':True,'enable_narrowing':False}) as program:
        objects=frozenset(program.points('d'));binding=ArgBinding('d',objects,AbsVal.from_addrs(objects))
        state=AIState.from_arg_bindings([binding],program.adapter,AnalysisBudget())
        for obj in objects:
            state=state.write_field_value(obj,key(None),AbsVal.from_str('caller-'))
            state=state.write_field_value(obj,key('None'),AbsVal.from_str('only'))
        typed_string(invoke(program,'subject.outer',[binding],state),'caller-only')


def test_escaping_function_defaults_annotations_and_user_attributes_do_not_replay_module(tmp_path):
    source="""counter=0
def make(value):
    global counter
    counter=counter+1
    def escaped(x:value=value, *, y=value)->value:
        return x+y
    setattr(escaped, '$default:0', 'user')
    return escaped
def invoke(f):
    return f()
def annotation(f):
    return f.__annotations__['x']
def user(f):
    return getattr(f, '$default:0')
original=make('before')
a=invoke(original)
b=annotation(original)
c=user(original)
"""
    with analyze_program(tmp_path,source,ai_options={'strict_exceptions':True,'enable_narrowing':False}) as program:
        initial=AIState();initial.shared_bindings[('subject','counter')]=AbsVal.from_int(0)
        for name in ('setattr','getattr'):
            builtin=BuiltinFunctionObject(CallStringContext(),AllocSite(name,AllocKind.BUILTIN),name)
            initial.shared_bindings[('subject',name)]=AbsVal.from_addrs(frozenset((builtin,)))
        made=invoke(program,'subject.make',[ArgBinding('value',frozenset(),AbsVal.from_str('caller'))],initial)
        assert made.is_complete,made.incomplete_reasons
        function=ArgBinding('f',made.ret,made.ret_value)
        called=invoke(program,'subject.invoke',[function],made.exit_state)
        typed_string(called,'callercaller')
        assert called.exit_state.shared_bindings[('subject','counter')].get_int_lattice().get_consts()==frozenset((1,))
        typed_string(invoke(program,'subject.annotation',[function],made.exit_state),'caller')
        typed_string(invoke(program,'subject.user',[function],made.exit_state),'user')
        assert any(owner.startswith('$default:subject.make.escaped@') for owner,name in made.exit_state.shared_bindings)
        assert any(owner.startswith('$annotation:subject.make.escaped@') for owner,name in made.exit_state.shared_bindings)


def test_source_snapshot_survives_tac_mutation_and_deleted_original(tmp_path):
    source="def identity(value):\n    return value\nresult=identity('before')\n"
    with analyze_program(tmp_path,source,ai_options={'strict_exceptions':True,'enable_narrowing':False}) as program:
        scope=program.scope('subject.identity');snapshot=scope.get_source_snapshot()
        Path(snapshot.filename).unlink()
        scope.get_ast().body[:]=[]
        typed_string(invoke(program,'subject.identity',[ArgBinding('value',frozenset(),AbsVal.from_str('after'))]),'after')


def test_unrepresentable_foreign_invocation_performs_real_havoc_and_exception():
    foreign=BuiltinInstanceObject(CallStringContext(),AllocSite('foreign',AllocKind.INSTANCE),'foreign')
    state=AIState().set_var('foreign',AbsVal.from_addrs(frozenset((foreign,))))
    solver=solve_source('result=foreign()\n',state,budget=AnalysisBudget(strict_exceptions=True,enable_narrowing=False))
    result=solver.summary
    assert not result.is_complete and result.normal_exit and result.ret_value is not None
    assert result.exit_state.get_var('result').addr_top
    assert 'BaseException' in result.may_raise
    assert result.exit_state.overlay_havoc
    assert {effect.kind for effect in result.unknown_effects}=={'may_read','may_write','may_call'}
    assert any(callee=='<callback>' for _,callee,_ in solver._shared_driver.calls)
    assert solver._shared_driver.writes


def test_local_budget_denial_does_not_charge_executed_shared_transfer():
    class Ledger:
        remaining=10
        consumed=0
        def consume(self,*,kind):
            assert kind=='abstract_transfer';self.consumed+=1;self.remaining-=1;return True
    ledger=Ledger();driver=SharedDriver((SourceModule('subject','subject.py',''),),options(AnalysisBudget(max_iterations=0)),work_ledger=ledger)
    from pythonstan.analysis.engines.abstract.driver import BudgetStop
    with pytest.raises(BudgetStop):driver.charge(1,'expression')
    assert ledger.consumed==0 and driver.work==0


def test_nondefault_retired_enumeration_control_is_rejected_without_aliasing_integer_width():
    with pytest.raises(BoundaryUnavailable,match='max_const_ints'):options(AnalysisBudget(max_const_ints=3))
    assert options(AnalysisBudget())['abstract']['int_domain_max_consts']==10


@pytest.mark.parametrize('strict',[False,True])
def test_unsupported_configuration_rejects_before_driver_even_with_host_fallback(strict,monkeypatch):
    def no_driver(*args,**kwargs):raise AssertionError('A driver was constructed for invalid options')
    monkeypatch.setattr(SharedDriver,'__init__',no_driver)
    with pytest.raises(BoundaryUnavailable,match='max_const_ints'):
        AISummaryEngine(EmptyPtaQuery(),AnalysisBudget(strict_exceptions=strict,max_const_ints=3))


def test_direct_solver_conflicting_aliases_are_rejected_and_local_budget_is_live():
    from pythonstan.analysis.pointer.ai.solver import SolverConfig
    with pytest.raises(BoundaryUnavailable,match='Conflicting legacy widening'):
        options(AnalysisBudget(),SolverConfig(widen_delay=0,loop_unroll=1))
    assert options(AnalysisBudget(),SolverConfig(max_iterations=0))['resources']['max_work']==0
    no_split=options(AnalysisBudget(max_value_partitions=0))['abstract']
    assert not no_split['enable_value_partition'] and not no_split['refine_booleans']


@pytest.mark.parametrize('first',['pythonstan.analysis.domains.bool','pythonstan.analysis.domains.none','pythonstan.analysis.pointer.ai.api','pythonstan.analysis.pointer.ai.value_domain'])
def test_cold_import_order_preserves_same_module_aliases(first):
    code="""import importlib
importlib.import_module(FIRST)
for suffix in ('api','config','state','value_domain','heap'):
    old=importlib.import_module('pythonstan.analysis.pointer.ai.'+suffix)
    new=importlib.import_module('pythonstan.compat.legacy_ai.'+suffix)
    assert old is new,suffix
for suffix in ('bool','none'):
    old=importlib.import_module('pythonstan.analysis.pointer.ai.domains.'+suffix)
    new=importlib.import_module('pythonstan.analysis.domains.'+suffix)
    assert old is new,suffix
""".replace('FIRST',repr(first))
    completed=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=15)
    assert completed.returncode==0,completed.stderr


def test_distinct_fresh_closure_activations_keep_real_prototypes_parents_and_cells(tmp_path):
    source="""def make(value):
    def escaped():
        return value
    return escaped
def pair(left,right):
    return [make(left),make(right)]
def first(functions):
    return functions[0]()
def second(functions):
    return functions[1]()
original=pair('before-left','before-right')
a=first(original)
b=second(original)
"""
    from pythonstan.analysis.pointer.kcfa.object import FunctionObject
    from pythonstan.analysis.pointer.kcfa.context import ObjectContext, FiniteContextAtom
    with analyze_program(tmp_path,source,ai_options={'strict_exceptions':True,'enable_narrowing':False}) as program:
        made=invoke(program,'subject.pair',[ArgBinding('left',frozenset(),AbsVal.from_str('caller-left')),ArgBinding('right',frozenset(),AbsVal.from_str('caller-right'))])
        assert made.is_complete,made.incomplete_reasons
        native_list=next(iter(made.ret));state=made.exit_state
        left,_=state.read_field_value(native_list,key(0),None,None);right,_=state.read_field_value(native_list,key(1),None,None)
        assert len(left.addrs)==len(right.addrs)==1 and left.addrs!=right.addrs
        for function in (*left.addrs,*right.addrs):
            assert isinstance(function,FunctionObject)
            assert function.ir is program.scope('subject.make.escaped')
            assert function.container_scope in program.query._state.get_scopes_for_ir(program.scope('subject.make'))
            assert isinstance(function.context,ObjectContext) and len(function.context.alloc_sites)==1
            atom=function.context.alloc_sites[0]
            assert isinstance(atom,FiniteContextAtom) and atom.source is function.ir
            assert len(atom.origins)==1 and type(atom.origins[0]) is str
        binding=ArgBinding('functions',made.ret,made.ret_value)
        typed_string(invoke(program,'subject.first',[binding],state),'caller-left')
        typed_string(invoke(program,'subject.second',[binding],state),'caller-right')


@pytest.mark.parametrize('mode',['object','globals','class_locals','locals_snapshot'])
def test_namespace_views_preserve_live_alias_or_snapshot_contract_across_boundary(mode):
    converter=bridge();frame=Frame('subject','subject',(),'entry',frozenset(('left','right')))
    metadata={'namespace_kind':mode}
    store=Store(cells={frame.cell('left'):Value.ref('view'),frame.cell('right'):Value.ref('view')})
    if mode=='object':
        metadata['owner']='owner';store.heap['owner']=HeapObject('instance',{'value':Value.constant('before')})
    elif mode=='globals':
        metadata['module']='subject';store.cells['global:subject:value']=Value.constant('before')
    elif mode=='class_locals':
        metadata['prefix']='local:class-owner:';store.cells['local:class-owner:value']=Value.constant('before')
    store.heap['view']=HeapObject('namespace',{'value':Value.constant('snapshot')} if mode=='locals_snapshot' else {},metadata=metadata)
    old=converter.old_state(store,frame)
    restored_converter=bridge(old);restored=restored_converter.import_state(frame)
    left=restored.cells[frame.cell('left')];right=restored.cells[frame.cell('right')]
    assert left==right and len(left.refs)==1
    node=__import__('ast').parse("view['value']").body[0].value
    transfer=restored_converter.driver.transfer
    outputs=transfer.setitem(left,Value.constant('value'),Value.constant('after'),restored,frame,node)
    normal=[out for out in outputs if out.kind=='normal'];assert normal
    for written in normal:
        reads=transfer.getitem(right,Value.constant('value'),written.store,frame,node)
        assert [out.value.exact_constants() for out in reads if out.kind=='normal']==[['after']]
        view=written.store.heap[next(iter(right.refs))]
        assert not view.havoc
        if mode=='object':assert written.store.heap[view.metadata['owner']].fields['value'].exact_constants()==['after']
        elif mode=='globals':assert written.store.cells['global:subject:value'].exact_constants()==['after']
        elif mode=='class_locals':assert written.store.cells['local:class-owner:value'].exact_constants()==['after']
    assert not restored_converter.reasons


def test_returned_bound_receiver_static_and_class_methods_use_same_core(tmp_path):
    source="""def make(value):
    class Base:
        label='class'
    class Holder(Base):
        def __init__(self):
            self.value=value
        def bound(self):
            return self.value
        @staticmethod
        def static():
            return 'static'
        @classmethod
        def classed(cls):
            return cls.label
    owner=Holder()
    return [owner.bound,owner.static,owner.classed]
def first(methods):
    return methods[0]()
def second(methods):
    return methods[1]()
def third(methods):
    return methods[2]()
original=make('before')
a=first(original)
b=second(original)
c=third(original)
"""
    with analyze_program(tmp_path,source,ai_options={'strict_exceptions':True,'enable_narrowing':False}) as program:
        made=invoke(program,'subject.make',[ArgBinding('value',frozenset(),AbsVal.from_str('caller'))])
        assert made.is_complete,made.incomplete_reasons
        binding=ArgBinding('methods',made.ret,made.ret_value)
        for scope,expected in [('first','caller'),('second','static'),('third','class')]:
            typed_string(invoke(program,'subject.'+scope,[binding],made.exit_state),expected)


def test_legacy_method_addresses_separate_receivers_and_weak_join_real_aliases(tmp_path):
    source="""class Holder:
    def method(self):
        return 1
left=Holder()
right=Holder()
a=left.method()
b=right.method()
"""
    from pythonstan.analysis.pointer.kcfa.object import ClassObject, InstanceObject, MethodObject
    from pythonstan.analysis.pointer.kcfa.context import FiniteContextAtom
    with analyze_program(tmp_path,source,ai_options={'plan_scopes':['subject'],'strict_exceptions':True}) as program:
        cls=next(obj for obj in program.points('Holder') if isinstance(obj,ClassObject))
        ir=program.scope('subject.Holder.method');container=program.query._state.get_scopes_for_ir(program.scope('subject.Holder'))[0]
        first=InstanceObject(CallStringContext(),AllocSite('first',AllocKind.INSTANCE),cls)
        second=InstanceObject(CallStringContext(),AllocSite('second',AllocKind.INSTANCE),cls)
        site=AllocSite(ir,AllocKind.METHOD)
        unbound=MethodObject(CallStringContext(),site,container,ir,cls,None)
        left=unbound.deliver_into(first);right=unbound.deliver_into(second)
        addresses=[AIAddr.from_kcfa_obj(obj) for obj in (unbound,left,right)]
        assert len(set(addresses))==3
        assert AIAddr(site,CallStringContext()).projection==()
        assert all(isinstance(address.projection[1],FiniteContextAtom) for address in addresses)
        base=AIState().set_obj(addresses[1],AbsObj(addresses[1],ObjKind.BOUND_METHOD))
        alias=base.copy();slots=SlotMap();slots.partitions[('str','value')]=Slot(AbsVal.from_str('after'),Presence.PRESENT)
        alias.heap[addresses[1]]=AbsObj(addresses[1],ObjKind.BOUND_METHOD,slots=slots)
        other=base.join(alias)
        assert other.heap[addresses[1]].slots.effective_slot(('str','value')).val.get_const_strs()==frozenset(('after',))
        assert AIAddr.from_kcfa_obj(left)==addresses[1]


def test_active_exception_from_caller_supports_real_bare_reraise(tmp_path):
    source="def reraiser():\n    raise\ntry:\n    reraiser()\nexcept Exception:\n    pass\n"
    with analyze_program(tmp_path,source,ai_options={'plan_scopes':['subject'],'strict_exceptions':True}) as program:
        exception=BuiltinInstanceObject(CallStringContext(),AllocSite('caller-error',AllocKind.INSTANCE),'ValueError')
        state=AIState(exn=AbsVal.from_addrs(frozenset((exception,))))
        summary=invoke(program,'subject.reraiser',state=state)
        assert summary.is_complete,summary.incomplete_reasons
        assert not summary.normal_exit and summary.ret_value.is_bottom()
        assert summary.may_raise==frozenset(('ValueError',))
        assert 'ValueError' in summary.prefix_exceptions
        assert summary.exceptional_exit_states[0][1].exn.addrs==frozenset((exception,))
