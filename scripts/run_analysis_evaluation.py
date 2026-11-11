#!/usr/bin/env python3
"""Reproduce one offline evaluation cycle; passing gates do not prove full Python.

Run after edits are frozen. Reports preserve commands, statuses, source digest,
and raw logs. No target source is transmitted to an LLM by this command.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def fingerprint():
    digest = hashlib.sha256()
    for path in sorted((ROOT / 'pythonstan').rglob('*.py')):
        digest.update(str(path.relative_to(ROOT)).encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'docs/certified_analysis/evidence/latest_cycle')
    parser.add_argument('--full-suite', action='store_true', help='run the full repository suite (now the default)')
    parser.add_argument('--scoped-only', action='store_true', help='explicitly omit the full repository gate for a diagnostic run')
    parser.add_argument('--timeout-seconds', type=int, default=300)
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error('timeout must be positive')
    if args.full_suite and args.scoped_only:
        parser.error('--full-suite and --scoped-only are mutually exclusive')
    dest = args.output_dir.resolve()
    if not dest.is_relative_to(ROOT):
        parser.error('output directory must be inside the repository for reproducible oracle paths')
    dest.mkdir(parents=True, exist_ok=True)
    phases = [
        ('scoped_gate', ['scripts/check_certified_release.py']),
        ('empirical', ['scripts/run_empirical_benchmarks.py', '--output', str(dest / 'empirical.json')]),
        ('exception_oracle', ['scripts/check_exception_oracle.py', '--output', str((dest / 'exception_oracle.json').relative_to(ROOT))]),
        ('conditional_effects', ['scripts/check_closed_heap_effects.py', '--output', str(dest / 'closed_heap_effects.json')]),
    ]
    if not args.scoped_only:
        phases.append(('historical_full_suite', ['-m','pytest','-q','--continue-on-collection-errors','--tb=short']))
    before = fingerprint()
    results = []
    for name, command in phases:
        log = dest / (name + '.log')
        started = time.monotonic()
        status = 'finished'
        with log.open('w') as output:
            try:
                code = subprocess.run([sys.executable, *command], cwd=ROOT, stdout=output,
                                      stderr=subprocess.STDOUT, timeout=args.timeout_seconds).returncode
            except subprocess.TimeoutExpired:
                code, status = None, 'timeout'
        results.append(dict(name=name, command=[sys.executable,*command], exit_code=code,
                            status=status, wall_seconds=time.monotonic()-started,
                            log=log.name, log_sha256=hashlib.sha256(log.read_bytes()).hexdigest()))
        print(name, status, code, flush=True)
    after = fingerprint()
    report = dict(schema_version=1, implementation_sha256_before=before,
                  implementation_sha256_after=after, implementation_stable=before==after,
                  phases=results, full_python_soundness_proved=False,
                  full_repository_gate_executed=not args.scoped_only,
                  interpretation='A reproducible evaluation gate, not automatic algorithm convergence or a semantic proof.')
    report['gates_passed'] = before == after and all(r['exit_code']==0 for r in results)
    (dest / 'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['gates_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
