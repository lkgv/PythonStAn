"""Build and exercise the installed wheel outside the source checkout."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import venv

ROOT = Path(__file__).resolve().parents[2]
PROBE = r'''
import io,json,pathlib,sys
import pythonstan
from pythonstan import Source,File,Project,ValueQuery,analyze
expected=sys.argv[1]
def exact(value):
    assert value['constants']==['after'] and value['tagged_constants']==[{'type':'str','value':'after'}]
    assert value['types']==['str'] and not value['objects'] and not value['top'] and not value['top_sorts'] and not value['may_unbound'] and not value['bottom']
    assert 'integer_interval' not in value and 'string_patterns' not in value
assert pathlib.Path(pythonstan.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve())
from pythonstan.compat import legacy_runner
from pythonstan.world.pipeline import Pipeline
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
def forbidden(*a,**k):raise AssertionError('unexpected full Native predecessor')
legacy_runner.run_legacy=forbidden;Pipeline.__init__=forbidden;PointerSolver.__init__=forbidden
text="def f(x):return x\nresult=f('after')\n"
root=pathlib.Path.cwd();(root/'subject.py').write_text(text)
q=ValueQuery('subject','result');results=[]
for target in [Source(text,module='subject'),File(root/'subject.py'),Project(root)]:
    r=analyze(target,queries=(q,));v=r.query(q).value
    assert r.actual_engine==expected;exact(v)
    assert r.solver_status=='converged' and r.coverage_status=='closed_under_profile'
    assert all(row.get('phase')!='native' for row in r.phase_trace)
    results.append(r)
before=results[0].serialize();analyze(Source('result=99',module='subject'))
assert results[0].serialize()==before
print(json.dumps({'installed_module':pythonstan.__file__,'expected_engine':expected,'three_inputs':'exact_after','session_isolation':True,'full_native_predecessor_sentinel':True}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--expected-engine', default='hybrid', choices=('ai_primary','hybrid'))
    args = parser.parse_args();out=args.output.resolve()
    if out.exists():raise SystemExit('Evidence directory must be new')
    out.mkdir(parents=True);rows=[]
    environment={k:v for k,v in os.environ.items() if k not in {'PYTHONPATH','PYTHONHOME'}}

    def run(name, command, *, cwd, stdin=None):
        result=subprocess.run(command,cwd=cwd,env=environment,input=stdin,text=True,capture_output=True)
        (out/(name+'.stdout.txt')).write_text(result.stdout);(out/(name+'.stderr.txt')).write_text(result.stderr)
        rows.append({'name':name,'command':command,'exit':result.returncode})
        if result.returncode:raise RuntimeError(f'{name}: exit {result.returncode}; inspect raw evidence')
        return result.stdout

    def source_identity():
        paths=[ROOT/'pyproject.toml',ROOT/'README.md',Path(__file__)]
        paths.extend(p for directory in ['pythonstan','pythonstan_inspector'] for p in (ROOT/directory).rglob('*') if p.is_file() and p.suffix in {'.py','.json','.yaml','.yml'})
        return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    before=source_identity()
    (out/'source_before.json').write_text(json.dumps(before,indent=2)+'\n')
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    try:
        wheelhouse=out/'wheels';wheelhouse.mkdir()
        run('wheel',[sys.executable,'-m','pip','wheel','--no-deps','--no-build-isolation','--wheel-dir',str(wheelhouse),str(ROOT)],cwd=out)
        wheels=list(wheelhouse.glob('pythonstan-*.whl'));assert len(wheels)==1
        with tempfile.TemporaryDirectory(prefix='pythonstan_installed_') as temporary:
            temporary=Path(temporary);envpath=temporary/'env';venv.EnvBuilder(with_pip=True,system_site_packages=True).create(envpath)
            python=str(envpath/'bin/python');cli=str(envpath/'bin/pythonstan');work=temporary/'work';work.mkdir()
            run('install',[python,'-m','pip','install','--no-deps','--force-reinstall',str(wheels[0])],cwd=work)
            probe=temporary/'probe.py';probe.write_text(PROBE)
            installed=json.loads(run('api',[python,str(probe),args.expected_engine],cwd=work))
            for name,options,stdin in [('code',['--code',"result='after'"],None),('stdin',['--stdin','--filename','snippet.py'],"result='after'"),('file',['--file','subject.py'],None),('project',['--project','.'],None)]:
                payload=json.loads(run(name,[cli,'analyze',*options,'--query','values','--format','json'],cwd=work,stdin=stdin))
                assert payload['actual_engine']==args.expected_engine and payload['solver_status']=='converged'
                module='snippet' if name in {'code','stdin'} else 'subject'
                value=payload['answers']['values']['value'][module]['result'];assert value['constants']==['after'] and value['tagged_constants']==[{'type':'str','value':'after'}] and value['types']==['str'] and not value['objects'] and not value['top'] and not value['top_sorts'] and not value['may_unbound'] and not value['bottom'] and 'integer_interval' not in value and 'string_patterns' not in value
            run('inspector-help',[str(envpath/'bin/pythonstan-inspector'),'--help'],cwd=work)
            for name,options in [('options',['config','list-options']),('effective',['config','show-effective','--file','subject.py']),('plan',['plan','--project','.'])]:
                json.loads(run(name,[cli,*options],cwd=work))
            result={'accepted':True,'installed':installed,'wheel':wheels[0].name,'wheel_sha256':hashlib.sha256(wheels[0].read_bytes()).hexdigest()}
    except Exception as error:
        result={'accepted':False,'error':f'{type(error).__name__}: {error}'}
    after=source_identity();last=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    (out/'source_after.json').write_text(json.dumps(after,indent=2)+'\n')
    stable=before==after and head==last
    result['accepted']=result['accepted'] and stable
    result.update(commit=head,commit_after=last,source_unchanged=stable,commands=rows,script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));return 0 if result['accepted'] else 1


if __name__=='__main__':raise SystemExit(main())
