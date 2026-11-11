#!/usr/bin/env python3
"""Combine immutable first measurements without pretending to rerun analysis."""
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CORPUS=ROOT/'benchmark/realworld_hardcases'


def main():
    reports=[]
    evidence=[]
    for cohort in ('web_orm','validation','plugins'):
        path=CORPUS/cohort/'results/first.json'
        report=json.loads(path.read_text())
        assert report['implementation_stable']
        evidence.append(dict(path=str(path.relative_to(ROOT)),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        reports.append(report)
    fingerprints={r['implementation_sha256_after'] for r in reports}
    assert len(fingerprints)==1
    rows=[row for report in reports for row in report['rows']]
    assert len({row['case']['id'] for row in rows})==len(rows)
    totals={}
    for mode in ('baseline','current'):
        completed=[row for row in rows if 'call_obligations' in row[mode]]
        failed=[row for row in rows if 'call_obligations' not in row[mode]]
        totals[mode]=dict(cases=len(rows),statuses=dict(Counter(row[mode]['status'] for row in rows)),
            required_pairs=sum(len(row['case']['required_edges']) for row in rows),
            recovered_pairs=sum(row[mode]['call_obligations']['recovered_count'] for row in completed),
            known_missing_pairs=sum(len(row[mode]['call_obligations']['missing_required']) for row in completed),
            required_pairs_in_failed_rows=sum(len(row['case']['required_edges']) for row in failed),
            negative_pairs=sum(len(row['case']['forbidden_edges']) for row in rows),
            checked_negative_pairs=sum(len(row['case']['forbidden_edges']) for row in completed),
            negative_pairs_in_failed_rows=sum(len(row['case']['forbidden_edges']) for row in failed),
            violated_negatives=sum(len(row[mode]['call_obligations']['violated_negative']) for row in completed),
            useful_reviewed_cases=sum(row[mode].get('useful_reviewed_call_result',False) for row in rows),
            unknown_records=sum(len(row[mode].get('unknown_records',[])) for row in rows),
            declared_effects=sum(len(row['case']['effects']) for row in rows),effects_discharged_by_kcfa=0)
    output=dict(schema_version=1,phase='immutable first measurements before coroutine identity repair',
        analyzer_checkpoint='228662c0078148e395f8294c5f9b3bece8bf44cb',
        implementation_sha256=next(iter(fingerprints)),sources=evidence,totals=totals,rows=rows,
        runtime_validated=sum(row.get('runtime_validation_passed',False) for row in rows),
        full_python_soundness_proved=False,whole_applications_analyzed=0)
    path=CORPUS/'results/initial.json';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps(output['totals'],indent=2))


if __name__=='__main__':
    main()
