"""Finite store order must preserve dispatch, unbound and missing-key growth."""
import pytest

from pythonstan.analysis.contracts import SourceModule
from pythonstan.analysis.domains.values import Value,ValueOperations
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.engines.abstract.state import Store,HeapObject,Frame


@pytest.mark.parametrize('left,right',[
    (HeapObject('dict'),HeapObject('instance')),
    (HeapObject('instance',class_ids=frozenset({'A'})),HeapObject('instance',class_ids=frozenset({'B'}))),
    (HeapObject('instance',class_ids=frozenset({'A','B'})),HeapObject('instance',class_ids=frozenset({'A'})))])
def test_heap_kind_and_new_class_alternatives_are_semantic_growth(left,right):
    assert not Store(heap={'same':left}).leq(Store(heap={'same':right}),ValueOperations())


@pytest.mark.parametrize('left',[
    Store(),Store(havoc_namespaces=frozenset({'subject'}))])
def test_implicit_unbound_or_havoc_cell_is_not_below_a_bound_cell(left):
    right=Store(cells={'global:subject:marker':Value.constant('present')})
    assert not left.leq(right,ValueOperations())


@pytest.mark.parametrize('default',[Value.bottom(),Value.constant('present')])
def test_implicit_absent_key_is_not_below_a_must_present_key(default):
    absent=Store(heap={'box':HeapObject('dict',default=default)})
    present=Store(heap={'box':HeapObject('dict',{'x':Value.constant('present')},default=default)})
    assert not absent.leq(present,ValueOperations())


def test_untracked_key_and_explicit_missing_marker_have_same_presence():
    ops=ValueOperations()
    implicit=Store(heap={'box':HeapObject('dict')})
    explicit=Store(heap={'box':HeapObject('dict',maybe_missing={'x'})})
    assert implicit.leq(explicit,ops) and explicit.leq(implicit,ops)


@pytest.mark.parametrize('left,right',[
    (Store(),Store(cells={'global:subject:x':Value.constant(1)})),
    (Store(havoc_namespaces=frozenset({'subject'})),Store(cells={'global:subject:x':Value.constant(1)})),
    (Store(heap={'box':HeapObject('dict')}),Store(heap={'box':HeapObject('dict',{'x':Value.constant('present')})})),
    (Store(heap={'box':HeapObject('instance',class_ids=frozenset({'A'}))}),Store(heap={'box':HeapObject('instance',class_ids=frozenset({'B'}))})),
    (Store(heap={'box':HeapObject('dict',{'x':Value.constant('dict')})}),Store(heap={'box':HeapObject('instance',{'y':Value.constant('instance')})}))])
def test_join_is_an_upper_bound_for_implicit_cells_presence_classes_and_shapes(left,right):
    ops=ValueOperations();joined=left.join(right,ops)
    assert left.leq(joined,ops) and right.leq(joined,ops)
    if left.heap and right.heap and left.heap['box'].kind!=right.heap['box'].kind:
        assert joined.heap['box'].kind=='summary' and joined.heap['box'].havoc


def test_new_class_input_reexecutes_actual_source_and_keeps_both_finite_returns():
    source=SourceModule('subject','subject.py','def f(obj):\n    return obj.label\n')
    driver=InterproceduralDriver((source,),{'resources':{'max_work':50000,'max_seconds':0,'max_memory_mb':0}})
    driver.scenario='order';node=driver.trees['subject'].body[0];key='same-unit'
    frame=Frame('subject','subject.f',(),key,frozenset({'obj'}))
    def entry(class_id):
        return Store(cells={frame.cell('obj'):Value.ref('same-object')},heap={
            'same-object':HeapObject('instance',class_ids=frozenset({class_id})),
            'A':HeapObject('class',{'label':Value.constant('A')},metadata={'mro':('builtin:object',)}),
            'B':HeapObject('class',{'label':Value.constant('B')},metadata={'mro':('builtin:object',)})})
    driver._request(key,frame,tuple(node.body),entry('A'),False,None);driver._solve()
    first=driver.ops.join_all(flow.value for flow in driver.units[key].outcomes if flow.kind=='return')
    assert first.exact_constants()==['A'] and not first.top
    driver._request(key,frame,tuple(node.body),entry('B'),False,None);driver._solve()
    grown=driver.ops.join_all(flow.value for flow in driver.units[key].outcomes if flow.kind=='return')
    assert set(grown.exact_constants())=={'A','B'} and not grown.top and not grown.top_sorts
    assert driver.units[key].entry.heap['same-object'].class_ids==frozenset({'A','B'})
    assert not driver.obligations
