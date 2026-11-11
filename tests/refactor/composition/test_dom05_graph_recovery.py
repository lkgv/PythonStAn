"""Colliding abstract keys and aliases across the actual full-state boundary."""
import ast
import pytest
from pythonstan.analysis.domains.values import Value,ValueOperations
from pythonstan.analysis.domains.reabstract import ReferenceKey
from pythonstan.analysis.engines.abstract.state import Store,HeapObject,Flow,Frame,Unit
from pythonstan.analysis.engines.abstract.state_transform import remap_store,PayloadTransformer
from pythonstan.analysis.composition.state_adapter import FullStateAdapter,BoundaryObject
from pythonstan.analysis.composition.region_engine import NativeRegionEngine
from tests.refactor.engines.test_state_recovery import driver,host_state


def collided():
    original=Store(heap={'table':HeapObject('dict',{
        ReferenceKey('A'):Value.constant('key-A'),ReferenceKey('B'):Value.constant('key-B')},
        maybe_missing={ReferenceKey('B')},metadata={'length':2})})
    return original,remap_store(original,{'A':'S','B':'S'},ValueOperations())


def test_colliding_reference_keys_join_values_and_preserve_presence_and_owner_cardinality():
    original,mapped=collided();obj=mapped.heap['table']
    value,missing=mapped.read_slot('table',ReferenceKey('S'),ValueOperations())
    assert set(value.exact_constants())=={'key-A','key-B'} and not value.top
    assert missing and obj.singleton and not obj.havoc
    mapped.write_slot('table',ReferenceKey('S'),Value.constant('late'),ValueOperations())
    assert set(mapped.heap['table'].fields[ReferenceKey('S')].exact_constants())=={'key-A','key-B','late'}
    assert ReferenceKey('S') in mapped.heap['table'].maybe_missing
    assert original.heap['table'].fields[ReferenceKey('A')].exact_constants()==['key-A']


def test_colliding_keys_do_not_materialize_a_false_single_item_or_length():
    _,mapped=collided();d=driver('result=0\n');frame=Frame('subject','subject',(),'root',is_module=True)
    node=d.trees['subject'].body[0];obj=mapped.heap['table'];obj.maybe_missing.clear()
    lengths=d.transfer.length_builtin([Value.ref('table')],(),mapped,frame,node)
    assert any(flow.kind=='normal' and flow.value.ints.may_contain(2) for flow in lengths)
    assert not any(flow.kind=='normal' and flow.value.exact_constants()==[1] for flow in lengths)
    normal=[flow for flow in d.transfer.dictionary_views.iterator(Value.ref('table'),'items',mapped,frame,node) if flow.kind=='normal']
    assert normal and all(flow.value.top for flow in normal)
    assert 'summarized_dictionary_key_iteration' in d.obligations
    assert not mapped.heap['table'].havoc
    sequences,unknown=d.transfer.iterable_values(Value.ref('table'),mapped)
    assert not sequences and unknown


def test_root_store_aliases_and_shared_mutable_payloads_survive_adapter_roundtrip():
    original=host_state();shared=[];original.heap['suspended'].metadata['shared']=shared
    flow=Flow(original,'suspend',Value.ref('A'),continuation=(('held',original,shared),),
              cleanups=(('held',original,shared),),resume_target={'store':original,'shared':shared,
                  'cells':original.cells,'heap':original.heap,'initialized':original.initialized,'writes':original.writes,
                  'guards':original.guards,'presence':original.heap['A'].maybe_missing})
    restored=FullStateAdapter().decode(FullStateAdapter().encode(flow));root=restored.store
    assert root.heap['suspended'].metadata['entry'] is root
    assert root.heap['suspended'].metadata['pending'].store is root
    assert restored.continuation[0][1] is root and restored.cleanups[0][1] is root
    assert restored.resume_target['store'] is root
    current=root.heap['suspended'].metadata['shared']
    assert restored.continuation[0][2] is current and restored.cleanups[0][2] is current
    assert restored.resume_target['shared'] is current
    for name in ('cells','heap','initialized','writes','guards'):
        assert restored.resume_target[name] is getattr(root,name)
    assert restored.resume_target['presence'] is root.heap['A'].maybe_missing
    current.append('after');assert not shared
    root.heap['suspended'].metadata['entry'].cells['changed']=Value.constant('after')
    assert root.cells['changed'].exact_constants()==['after'] and 'changed' not in original.cells


def test_real_native_region_write_is_visible_through_embedded_root_alias():
    d=driver("box['value']='after'\n");state=Store(cells={'global:subject:box':Value.ref('box')},
        heap={'box':HeapObject('dict',{'value':Value.constant('before')})})
    state.heap['suspended']=HeapObject('generator',metadata={'entry':state})
    frame=Frame('subject','subject',(),'root',is_module=True)
    original=Flow(state,continuation=(('root',state),))
    rows=NativeRegionEngine().execute(d.trees['subject'].body[0],original,frame,d)
    normal=next(flow for flow in rows if flow.kind=='normal')
    assert normal.store.heap['suspended'].metadata['entry'] is normal.store
    assert normal.continuation[0][1] is normal.store
    assert normal.store.heap['suspended'].metadata['entry'].heap['box'].fields['value'].exact_constants()==['after']
    assert state.heap['box'].fields['value'].exact_constants()==['before']


def test_real_host_future_old_key_aliases_keep_weak_finite_reads_and_writes():
    d=driver('result=0\n');state=collided()[0]
    state.heap.update({'A':HeapObject('instance'),'B':HeapObject('instance')})
    d.units['U']=Unit('U',Frame('subject','subject',(),'U',is_module=True),(),state)
    d.unit_scenarios['U']='scenario';d.reabstract_addresses({'A':'S','B':'S'})
    current=d.units['U'].entry
    before,_=current.read_slot('table',ReferenceKey('A'),d.ops)
    assert set(before.exact_constants())=={'key-A','key-B'} and not before.top
    current.write_slot('table',ReferenceKey('B'),Value.constant('late'),d.ops)
    after,missing=current.read_slot('table',ReferenceKey('A'),d.ops)
    assert set(after.exact_constants())=={'key-A','key-B','late'} and missing and not after.top
    assert current.heap['table'].singleton and not current.heap['table'].havoc


def test_replaced_flat_maps_are_authoritative_and_new_boundary_objects_convert_once():
    original=host_state();adapter=FullStateAdapter();boundary=adapter.encode(Flow(original,continuation=(('root',original),)))
    boundary.cells={**boundary.cells,'kernel':Value.ref('new')}
    boundary.heap={**boundary.heap,'new':BoundaryObject('dict',{'value':Value.constant('after')},Value.bottom(),set(),True,frozenset(),{},False)}
    restored=adapter.decode(boundary);root=restored.store
    assert root.heap['suspended'].metadata['entry'] is root and restored.continuation[0][1] is root
    assert isinstance(root.heap['new'],HeapObject)
    assert root.heap['suspended'].metadata['entry'].cells['kernel'].refs==frozenset({'new'})
    assert root.heap['suspended'].metadata['entry'].heap['new'].fields['value'].exact_constants()==['after']
    assert 'new' not in original.heap


def test_actual_native_new_allocation_is_in_the_rebound_root_heap():
    d=driver("new={'value':'after'}\n");state=Store()
    state.heap['suspended']=HeapObject('generator',metadata={'entry':state})
    frame=Frame('subject','subject',(),'root',is_module=True)
    rows=NativeRegionEngine().execute(d.trees['subject'].body[0],Flow(state,continuation=(('root',state),)),frame,d)
    root=next(row.store for row in rows if row.kind=='normal')
    assert root.heap['suspended'].metadata['entry'] is root
    new=next(iter(root.cells['global:subject:new'].refs))
    assert isinstance(root.heap[new],HeapObject)
    assert root.heap['suspended'].metadata['entry'].heap[new].fields['value'].exact_constants()==['after']


def test_alias_first_field_map_and_standalone_reference_key_map_both_join_collisions():
    fields={ReferenceKey('A'):Value.constant('key-A'),ReferenceKey('B'):Value.constant('key-B')}
    state=Store(heap={'table':HeapObject('dict',fields=fields)})
    mapped=PayloadTransformer({'A':'S','B':'S'},ValueOperations()).visit({'alias_first':fields,'state_second':state})
    assert mapped['alias_first'] is mapped['state_second'].heap['table'].fields
    assert set(mapped['alias_first'][ReferenceKey('S')].exact_constants())=={'key-A','key-B'}
    assert ReferenceKey('S') in mapped['state_second'].heap['table'].metadata['summarized_keys']
    plain=PayloadTransformer({'A':'S','B':'S'},ValueOperations()).visit(fields)
    assert set(plain[ReferenceKey('S')].exact_constants())=={'key-A','key-B'}


def test_alias_first_heap_map_rebuilds_address_keys_in_place_without_stale_entries():
    heap={'A':HeapObject('instance',{'value':Value.constant('A')}),'B':HeapObject('instance',{'value':Value.constant('B')})}
    original={'alias_first':heap,'state_second':Store(heap=heap)}
    mapped=PayloadTransformer({'A':'S','B':'S'},ValueOperations()).visit(original)
    assert mapped['alias_first'] is mapped['state_second'].heap and set(mapped['alias_first'])=={'S'}
    assert set(mapped['alias_first']['S'].fields['value'].exact_constants())=={'A','B'}
    assert not mapped['alias_first']['S'].singleton


def test_alias_first_cells_rekey_and_join_in_the_same_memo_dictionary():
    cells={'A':Value.constant('A'),'B':Value.constant('B')}
    mapped=PayloadTransformer({'A':'S','B':'S'},ValueOperations()).visit({'alias_first':cells,'state_second':Store(cells=cells)})
    assert mapped['alias_first'] is mapped['state_second'].cells and set(mapped['alias_first'])=={'S'}
    assert set(mapped['alias_first']['S'].exact_constants())=={'A','B'}


def test_alias_first_metadata_uses_its_trusted_role_and_preserves_literal_strings():
    metadata={'owner':'A','mro':('A','B'),'closure':(('x','A'),),'label':'A'}
    mapped=PayloadTransformer({'A':'S','B':'S'},ValueOperations()).visit({'alias_first':metadata,'object_second':HeapObject('namespace',metadata=metadata)})
    assert mapped['alias_first'] is mapped['object_second'].metadata
    assert mapped['alias_first']=={'owner':'S','mro':('S','S'),'closure':(('x','S'),),'label':'A'}


@pytest.mark.parametrize('reverse',[False,True])
def test_owner_collision_unions_key_summary_without_turning_finite_reads_into_havoc(reverse):
    entries=[('A',HeapObject('dict',{ReferenceKey('A'):Value.constant('key-A'),ReferenceKey('B'):Value.constant('key-B')},metadata={'length':2})),
             ('B',HeapObject('dict',metadata={'length':0}))]
    original=Store(heap=dict(reversed(entries) if reverse else entries))
    mapped=remap_store(original,{'A':'S','B':'S'},ValueOperations())
    value,missing=mapped.read_slot('S',ReferenceKey('S'),ValueOperations())
    assert set(value.exact_constants())=={'key-A','key-B'} and missing and not value.top
    assert not mapped.heap['S'].singleton and not mapped.heap['S'].havoc
    assert ReferenceKey('S') in mapped.heap['S'].metadata['summarized_keys']
    left=remap_store(Store(heap={'A':entries[0][1]}),{'A':'S','B':'S'},ValueOperations())
    right=remap_store(Store(heap={'B':entries[1][1]}),{'A':'S','B':'S'},ValueOperations())
    joined=left.join(right,ValueOperations())
    assert left.leq(joined,ValueOperations()) and right.leq(joined,ValueOperations())
