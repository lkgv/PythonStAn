#!/usr/bin/env python3
"""Finite projected-call inclusion checks; not a configuration soundness proof."""
import argparse
import json
from pathlib import Path


def audit(report):
    if not report.get('implementation_stable'):
        raise ValueError('matrix implementation changed during measurement')
    rows={}
    for row in report['rows']:
        key=(row['case'],row['context_policy'],row['index_sensitive'],row['enable_ai_intrinsics'])
        if key in rows: raise ValueError('duplicate configuration row')
        rows[key]=row
    results=[]
    def inclusion(kind, smaller, larger):
        a,b=rows[smaller],rows[larger]
        comparable=all(row['repeat_call_edges_stable'] and row['completed_trials']==row['repetitions'] for row in (a,b))
        item={'property':kind,'subset_config':list(smaller),'superset_config':list(larger),'comparable':comparable}
        if comparable:
            left=set(map(tuple,a['trial_results'][0]['call_edges']))
            right=set(map(tuple,b['trial_results'][0]['call_edges']))
            item.update(subset_holds=left<=right,not_in_superset=sorted(left-right))
        results.append(item)
    for case in sorted({key[0] for key in rows}):
        for index in (False,True):
            for ai in (False,True):
                for low,high in (('0-cfa','1-cfa'),('1-cfa','2-cfa')):
                    inclusion('context_projection',(case,high,index,ai),(case,low,index,ai))
        for context in ('0-cfa','1-cfa','2-cfa'):
            for ai in (False,True):
                inclusion('index_projection',(case,context,True,ai),(case,context,False,ai))
            for index in (False,True):
                inclusion('ai_additive_projection',(case,context,index,False),(case,context,index,True))
    return {'schema_version':1,'implementation_sha256':report['implementation_sha256_after'],
            'checks':results,'total_checks':len(results),
            'unmeasured_checks':sum(not row['comparable'] for row in results),
            'failed_checks':sum(row.get('subset_holds') is False for row in results),
            'interpretation':'Finite caller/callee name projections on these measured inputs only; not abstract-state inclusion or a general soundness/precision theorem.',
            'full_python_soundness_proved':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matrix',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try: result=audit(json.loads(args.matrix.read_text()))
    except (ValueError,KeyError,OSError) as exc: parser.error(str(exc))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({key:result[key] for key in ('total_checks','unmeasured_checks','failed_checks')}))
    return 0 if not(result['unmeasured_checks'] or result['failed_checks']) else 2


if __name__=='__main__': raise SystemExit(main())
