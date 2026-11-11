"""Record source-bound stage acceptance without inferring whole-Python proof."""
from pathlib import Path
import argparse,hashlib,json,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[2]
STAGES={
 'contracts':['tests/refactor/contracts','tests/refactor/api','tests/api'],
 'domains':['tests/refactor/domains'],
 'semantics':['tests/refactor/semantics','tests/refactor/engines'],
 'composition':['tests/refactor/composition'],
 'llm-observation':['tests/refactor/llm','tests/refactor/verification','tests/refactor/observation','tests/certified/test_checker.py','tests/certified/test_checker_exceptions.py','tests/certified/test_checker_adversarial_ai.py'],
 'experiment-tools':['tests/refactor/benchmark'],
}
def identity():
 paths={p for directory in ['pythonstan','pythonstan_inspector','tests/refactor','tests/api','tests/certified','scripts/refactor','benchmark/refactor','docs/specs'] for p in (ROOT/directory).rglob('*') if p.is_file() and p.suffix in {'.py','.json','.yaml','.toml','.md'}}
 paths.update([ROOT/'pyproject.toml',ROOT/'docs/refactor/query-gold.json'])
 return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',choices=[*STAGES,'architecture','all'],default='all');p.add_argument('--output',type=Path,required=True);args=p.parse_args()
 out=args.output.resolve()
 if out.exists():raise SystemExit('Evidence directory must be new; earlier red and green evidence is immutable')
 out.mkdir(parents=True);before=identity();head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip();rows=[]
 for stage in [*STAGES,'architecture'] if args.stage=='all' else [args.stage]:
  cmd=[sys.executable,'scripts/refactor/check_architecture.py','--release','--self-test','--output',str(out/'architecture.json')] if stage=='architecture' else [sys.executable,'-m','pytest','--import-mode=importlib','-q',*STAGES[stage]]
  started=time.monotonic()
  with (out/(stage+'.stdout.txt')).open('w') as stdout,(out/(stage+'.stderr.txt')).open('w') as stderr:r=subprocess.run(cmd,cwd=ROOT,stdout=stdout,stderr=stderr)
  rows.append({'stage':stage,'command':cmd,'exit':r.returncode,'wall_seconds':time.monotonic()-started});(out/'progress.json').write_text(json.dumps(rows,indent=2)+'\n')
 after=identity();last=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip();stable=before==after and head==last
 result={'schema_version':1,'commit':head,'commit_after':last,'source_before':before,'source_after':after,'source_unchanged':stable,'stages':rows,'all_commands_passed':all(x['exit']==0 for x in rows),'valid_for_recorded_source':stable,'release_claim':False,'note':'Task/gate acceptance uses frozen contracts and independent semantic review, not pytest exit counts alone.'}
 (out/'record.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'output':str(out),'source_unchanged':stable,'results':[{k:x[k] for k in ['stage','exit']} for x in rows]}));return 0 if stable and result['all_commands_passed'] else 1
if __name__=='__main__':raise SystemExit(main())
