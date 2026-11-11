from pathlib import Path
import pytest
from pythonstan import Source,File,Project,ValueQuery,analyze

@pytest.mark.parametrize('kind',['source','file','project'])
def test_qualified_default_is_real_ai_driven_hybrid_for_three_inputs(tmp_path,monkeypatch,kind):
    from pythonstan.compat import legacy_runner
    from pythonstan.world.pipeline import Pipeline
    from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
    def forbidden(*args,**kwargs):raise AssertionError('Default invoked a full Native predecessor')
    monkeypatch.setattr(legacy_runner,'run_legacy',forbidden)
    monkeypatch.setattr(Pipeline,'__init__',forbidden)
    monkeypatch.setattr(PointerSolver,'__init__',forbidden)
    source="def f(x):return x\nresult=f('after')\n"
    (tmp_path/'subject.py').write_text(source)
    target={'source':Source(source,module='subject'),'file':File(tmp_path/'subject.py'),'project':Project(tmp_path)}[kind]
    q=ValueQuery('subject','result');r=analyze(target,queries=[q]);value=r.query(q).value
    assert r.actual_engine==r.effective_config['engine']=='hybrid'
    assert r.solver_status=='converged' and r.coverage_status=='closed_under_profile'
    assert value['constants']==['after'] and value['tagged_constants']==[{'type':'str','value':'after'}]
    assert value['types']==['str'] and not value['objects'] and not value['top'] and not value['top_sorts'] and not value['may_unbound'] and not value['bottom']
    assert not {'integer_interval','string_patterns'} & value.keys()
    assert not r.effective_config['llm']['enabled']
    assert all(row.get('phase')!='native' for row in r.phase_trace)
    before=r.serialize();analyze(Source('result=99',module='subject'))
    assert r.serialize()==before
