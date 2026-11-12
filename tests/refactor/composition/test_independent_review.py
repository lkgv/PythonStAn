"""Independent heldout region/adapter/identity review regressions.

Expected values follow builtin Python semantics, not analyzer output. Adapter
mutation checks distinguish actual source bugs from checker/ownership gaps.
"""

from pythonstan.analysis.contracts.identity import ObjectId, CellId
from pythonstan.analysis.state.effects import CellWrite, HeapWrite, HeapDefaultWrite, MetadataUpdate, MetadataAspect
from dataclasses import replace
import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts import SourceModule
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.state.flow import FlowTail
from pythonstan.analysis.engines.abstract.state import Store, HeapObject, Flow
from pythonstan.analysis.composition.state_adapter import FullStateAdapter
from pythonstan.analysis.composition.oracle import IncrementalCandidateOracle, IncrementalInclusionStore


def selected(source, line):
    query = ValueQuery(module='example', symbol='result', point='module_exit')
    result = analyze(Source(source, module='example'), config={
        'engine': 'ai_primary',
        'resources': {'max_work': 10000, 'max_seconds': 0},
        'regions': [{'selector': {'module': 'example', 'kind': 'block', 'line_start': line},
                     'engine': 'native'}]}, queries=(query, 'effects'))
    return result, result.query(query).value


@pytest.mark.parametrize(('source', 'line', 'expected'), [
    ('result = ~1\n', 1, -2),
    ("obj = {'name': 'keyvalue'}\ntry:\n    result = obj.name\nexcept AttributeError:\n    result = 'caught'\n", 3, 'caught'),
    ("items = []\ntry:\n    items.flag = 1\nexcept AttributeError:\n    result = 'caught'\n", 3, 'caught'),
    ("items = (1,)\ntry:\n    items[0] = 2\nexcept TypeError:\n    result = 'caught'\n", 3, 'caught'),
    ("try:\n    result = (1).missing\nexcept AttributeError:\n    result = 'caught'\n", 2, 'caught'),
])
def test_native_region_preserves_builtin_protocols(source, line, expected):
    result, value = selected(source, line)
    # An explicitly rejected boundary is an allowed unsupported outcome only
    # when declared by the capability contract; a closed wrong result is not.
    assert value is not None and value['constants'] == [expected], result.raw_snapshot()


def test_boundary_nested_generator_state_is_detached():
    captured = Store(cells={'capture': Value.constant('before')})
    original = Flow(Store(heap={'generator': HeapObject('generator', metadata={'entry': captured})}))
    adapter = FullStateAdapter()
    boundary = adapter.encode(original)
    boundary.heap['generator'].metadata['entry'].cells['capture'] = Value.constant('changed')
    assert original.store.heap['generator'].metadata['entry'].cells['capture'].exact_constants() == ['before']
    decoded = adapter.decode(adapter.encode(original))
    decoded.store.heap['generator'].metadata['entry'].cells['capture'] = Value.constant('later')
    assert original.store.heap['generator'].metadata['entry'].cells['capture'].exact_constants() == ['before']


def test_roundtrip_validator_rejects_cleanup_and_resume_loss():
    class MissingCleanup(FullStateAdapter):
        def decode(self, boundary):
            return replace(super().decode(boundary), tail=FlowTail())
    flow = Flow(Store(), 'suspend', Value.constant('yielded'), continuation=('resume',),
                cleanups=(('finally',),), resume_target='target')
    assert not MissingCleanup().check_roundtrip(flow, ValueOperations())


def test_oracle_explicit_identity_cannot_override_actual_driver_identity():
    first = InterproceduralDriver((SourceModule('example', 'a.py', 'result=1\n'),))
    second = InterproceduralDriver((SourceModule('example', 'a.py', 'result=2\n'),))
    oracle = IncrementalCandidateOracle(source_digest=first.source_digest, profile='python-core-v1')
    with pytest.raises(ValueError, match='source identity'):
        oracle.bind(second, source_digest=first.source_digest)


def test_native_intermediate_solve_uses_a_budget_or_shared_accountant(monkeypatch):
    original = IncrementalInclusionStore.solve
    calls = []
    def checked(self, max_work=None, *args, **kwargs):
        calls.append(max_work)
        assert max_work is not None or kwargs.get('on_work') is not None, 'unbounded intermediate Native solve'
        return original(self, max_work, *args, **kwargs)
    monkeypatch.setattr(IncrementalInclusionStore, 'solve', checked)
    _, value = selected("box = {'value': 'after'}\nresult = box['value']\n", 2)
    assert value['constants'] == ['after'] and calls


def test_roundtrip_validator_rejects_effect_and_initialization_loss() -> None:
    class MissingEffects(FullStateAdapter):
        def decode(self, boundary):
            flow = super().decode(boundary)
            flow.store.writes.clear()
            flow.store.events = ()
            flow.store.initialized.clear()
            return flow
    flow = Flow(Store(cells={'value': Value.constant('after')},
                      initialized={'example': 'initializing'},
                      writes={CellWrite(CellId('value'))},
                      events=(('cleanup', 'performed'),)))
    assert not MissingEffects().check_roundtrip(flow, ValueOperations())


def test_oracle_explicit_profile_cannot_override_actual_driver_profile():
    driver = InterproceduralDriver((SourceModule('example', 'a.py', 'result=1\n'),),
                                   {'semantic_profile': 'python-protocol-v1'})
    oracle = IncrementalCandidateOracle(source_digest=driver.source_digest, profile='python-core-v1')
    with pytest.raises(ValueError, match='profile identity'):
        oracle.bind(driver, profile='python-core-v1')


@pytest.mark.parametrize(('source', 'line', 'expected'), [
    ("obj={'x':'after'}\nhandler=obj.get\nresult=handler('x')\n", 2, 'after'),
    ("result=(1).real\n", 1, 1),
    ("items=['before']\nitems[-1]='after'\nresult=items[0]\n", 2, 'after'),
    ("items=['before']\ntry:\n    items[2]='after'\nexcept IndexError:\n    result='caught'\n", 3, 'caught'),
    ("items=['before']\ntry:\n    items[0.0]='after'\nexcept TypeError:\n    result='caught'\n", 3, 'caught'),
    ("a='before'\nobj=(1,)\ntry:\n    a=obj[0]=2\nexcept TypeError:\n    result=a\n", 4, 2),
])
def test_native_generalization_is_exact_or_explicitly_rejected(source, line, expected):
    from pythonstan.config import ConfigurationError
    try:
        result, value = selected(source, line)
    except ConfigurationError:
        # These are heldout protocol combinations, outside the mandatory gold.
        # A finite Native profile may reject them explicitly before claiming
        # a result; it may not fabricate a closed wrong normal/exception path.
        return
    assert value is not None and value['constants'] == [expected], result.raw_snapshot()


@pytest.mark.parametrize(('source', 'line', 'expected'), [
    ("items=['before']\ntry:\n    result=items[0.0]\nexcept TypeError:\n    result='caught'\n", 3, 'caught'),
    ("items={1}\ntry:\n    result=items[0]\nexcept TypeError:\n    result='caught'\n", 3, 'caught'),
    ("class Base:\n    def __getitem__(self, key): return 'after'\nclass Derived(Base):\n    pass\nobj=Derived()\nresult=obj[0]\n", 6, 'after'),
])
def test_native_load_protocols_and_inherited_hooks_are_modeled_or_rejected(source, line, expected):
    from pythonstan.config import ConfigurationError
    try:
        result, value = selected(source, line)
    except ConfigurationError:
        return
    assert value is not None and value['constants'] == [expected], result.raw_snapshot()


def test_native_mixed_scalar_reference_receiver_does_not_omit_invalid_branch():
    from pythonstan.config import ConfigurationError
    source = "obj=[] if flag else 1\ntry:\n    result=obj[0]\nexcept TypeError:\n    result='type'\nexcept IndexError:\n    result='index'\n"
    query = ValueQuery('example', 'result')
    try:
        result = analyze(Source(source, module='example'), config={
            'engine':'ai_primary', 'inputs':{'bindings':{'example.flag':{'type':'bool','top':True}}},
            'abstract':{'solver_options':{'max_traces_per_block':1}},
            'regions':[{'selector':{'module':'example','kind':'block','line_start':3},'engine':'native'}]},
            queries=(query,))
    except ConfigurationError:
        return
    value = result.query(query).value
    assert value is not None and set(value['constants']) == {'type','index'}, result.raw_snapshot()


def test_native_class_inherited_lookup_is_modeled_or_rejected():
    from pythonstan.config import ConfigurationError
    source="class Base:\n    value='after'\nclass Derived(Base):\n    pass\nresult=Derived.value\n"
    try:
        result,value=selected(source,5)
    except ConfigurationError:
        return
    assert value is not None and value['constants']==['after'],result.raw_snapshot()


def test_native_builtin_name_copy_is_resolved_or_rejected():
    from pythonstan.config import ConfigurationError
    try:
        result,value=selected('result=int\n',1)
    except ConfigurationError:
        return
    assert value is not None and 'builtin:int' in value['objects'],result.raw_snapshot()
