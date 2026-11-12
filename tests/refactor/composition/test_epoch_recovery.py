"""Real host coarsening must carry optional oracle and L2 authority with it."""
import ast
from types import SimpleNamespace

import pytest

from pythonstan.analysis.contracts import EntryScenario,SourceModule
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver,BudgetStop
from pythonstan.analysis.engines.abstract.state import HeapObject
from pythonstan.analysis.composition.hybrid import configure_driver
from pythonstan.llm.providers import make_summary_provider

def driver_for(source='result=0\n',engine='hybrid'):
    module=SourceModule('example','example.py',source)
    config={'engine':engine,'resources':{'max_work':100000,'max_seconds':0,'max_memory_mb':0}}
    driver=InterproceduralDriver((module,),options=config)
    configure_driver(driver,config=config)
    driver.run((EntryScenario('example'),))
    return driver,module,config

def test_actual_host_moves_oracle_fields_rules_and_late_old_alias_subscribers():
    driver,_,_=driver_for()
    unit=next(iter(driver.units.values()));oracle=driver.oracle
    for name,constant in [('left','A'),('right','B')]:
        unit.entry.heap[name]=HeapObject('instance',{'value':Value.constant(constant)})
        oracle.publish_field(name,'value',Value.constant(constant))
    oracle.publish_cell('object',Value.ref('left'))
    oracle.register_load('object','value','read')
    assert oracle.candidates('read',unit.key).values.exact_constants()==['A']
    before=oracle.kernel;callbacks=[]
    before.subscribe(('field','left','value'),lambda node,version:callbacks.append((node,version)))
    prepared=oracle.prepare_reabstract({'left':'summary','right':'summary'},driver.ops)
    assert oracle.kernel is before and callbacks==[]
    assert set(prepared['kernel'].value(('field','summary','value')).exact_constants())=={'A','B'}
    driver.reabstract_addresses({'left':'summary','right':'summary'})
    assert oracle.kernel is not before and driver.ops.arena.epoch==1
    assert set(oracle.candidates('read',unit.key).values.exact_constants())=={'A','B'}
    assert callbacks and all(node==('field','summary','value') for node,_ in callbacks)
    driver.queue.clear();driver.queued.clear()
    oracle.publish_field('right','value',Value.constant('C'))
    assert unit.key in driver.queued,'Old alias publication must wake the pre-existing real consumer'
    assert set(oracle.candidates('read',unit.key).values.exact_constants())=={'A','B','C'}
    assert oracle.kernel.value(('field','left','value'))==oracle.kernel.value(('field','summary','value'))

def test_failed_host_preparation_keeps_oracle_aliases_and_callbacks_unpublished():
    driver,_,_=driver_for();oracle=driver.oracle;unit=next(iter(driver.units.values()))
    unit.entry.heap['left']=HeapObject('instance',{'value':Value.constant('A')})
    oracle.publish_field('left','value',Value.constant('A'))
    original=oracle.kernel;epoch=driver.ops.arena.epoch;root=unit.entry
    driver.max_work=driver.work
    with pytest.raises(BudgetStop):driver.reabstract_addresses({'left':'summary'})
    assert oracle.kernel is original and driver.ops.arena.epoch==epoch and unit.entry is root
    assert oracle.kernel.value(('field','left','value')).exact_constants()==['A']

SOURCE='''def ident(payload):
    return payload
def box(payload):
    return {'value':payload}
def dispatch(table,key,payload):
    return table[key](payload)
table={'read':ident,'write':box}
a=dispatch(table,'read','after')
b=dispatch(table,'write','before')
'''

def test_actual_l2_authority_is_revoked_and_retired_fresh_id_is_not_reapplied():
    module=SourceModule('example','example.py',SOURCE)
    config={'engine':'ai_primary','resources':{'max_work':100000,'max_seconds':0,'max_memory_mb':0},
            'llm':{'enabled':True,'level2':True,'provider':'offline','max_proposals':2,'checker_budget':10000}}
    provider=make_summary_provider(SimpleNamespace(modules=(module,)),config)
    driver=InterproceduralDriver((module,),options=config);driver.summary_provider=provider
    snapshot=driver.run((EntryScenario('example'),)).to_dict()
    assert snapshot['values']['example']['a']['constants']==['after']
    assert provider.statistics['applications']==2
    assert all(record['status']=='verified' for record in provider.proof_records)
    fresh=next(iter(provider._applied_allocation_ids))
    unit=next(unit for unit in driver.units.values() if unit.is_module)
    store=next(flow.store for flow in unit.outcomes if flow.kind=='normal')
    site=next(node.value for node in driver.trees['example'].body if isinstance(node,ast.Assign) and node.targets[0].id=='b')
    dispatch=next(identifier for identifier,info in driver.functions.items() if info.qualname=='example.dispatch')
    table=store.cells['global:example:table']
    applied=provider.statistics['applications'];before=driver.work
    driver.reabstract_addresses({fresh:'summary:fresh'})
    assert driver.work>before and provider.statistics['state_invalidations']==1
    assert fresh in provider._retired_allocation_ids and not provider.programs
    assert all(record['status']=='unknown' for record in provider.proof_records)
    unit=driver.units[unit.key];driver.current=unit.key;driver.scenario=driver.unit_scenarios[unit.key]
    assert provider.try_apply(driver._summary_context(),dispatch,[table,Value.constant('write'),Value.constant('before')],(),store,unit.frame,site) is None
    assert provider.statistics['applications']==applied
    current=next(flow.store for flow in unit.outcomes if flow.kind=='normal')
    assert fresh not in current.heap and not current.heap['summary:fresh'].singleton


def test_oracle_preparation_checks_budget_before_cloning_each_fact(monkeypatch):
    from pythonstan.analysis.composition.oracle import IncrementalInclusionStore
    driver,_,_=driver_for();kernel=IncrementalInclusionStore(driver.ops)
    for index in range(64):kernel.add_fact(('field',f'owner{index}','value'),Value.ref('left'))
    remapped=[];original=driver.ops.remap
    def observed(value,mapping):remapped.append(value);return original(value,mapping)
    monkeypatch.setattr(driver.ops,'remap',observed)
    before=driver.work;driver.max_work=before+2
    with pytest.raises(BudgetStop):
        kernel.prepare_reabstract({'left':'summary'},driver.ops,
            charge=lambda amount:driver.charge(amount,'test_oracle_prepare'))
    assert driver.work==before+1 and remapped==[]
    assert len(kernel.facts)==64 and driver.ops.arena.epoch==0
    assert all(value.refs==frozenset({'left'}) for value in kernel.facts.values())


@pytest.mark.parametrize('billed',[False,True])
def test_standalone_invalidation_does_not_fabricate_driver_charges(billed):
    module=SourceModule('example','example.py',SOURCE)
    config={'engine':'ai_primary','resources':{'max_work':100000,'max_seconds':0,'max_memory_mb':0},
            'llm':{'enabled':True,'level2':True,'provider':'offline'}}
    provider=make_summary_provider(SimpleNamespace(modules=(module,)),config)
    driver=InterproceduralDriver((module,),options=config);driver.summary_provider=provider
    driver.run((EntryScenario('example'),));before=driver.work;paid=provider.statistics['charged_to_driver_work']
    assert provider.statistics['uncharged_attempted_work']==0
    provider.invalidate_state_epoch(1,driver=driver if billed else None)
    added=provider.statistics['state_invalidation_work']
    assert added>0 and all(record['status']=='unknown' for record in provider.proof_records)
    assert driver.work-before==(added if billed else 0)
    assert provider.statistics['charged_to_driver_work']-paid==(added if billed else 0)
    assert provider.statistics['uncharged_attempted_work']==(0 if billed else added)
