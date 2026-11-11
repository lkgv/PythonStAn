"""Reproduce the current Native author gate without changing its assertions."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[5]
HERE = Path(__file__).resolve().parent
REGISTRY = HERE / 'native_control_witnesses.json'


def digest(paths):
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(set(paths))}


def main():
    registry = json.loads(REGISTRY.read_text())
    selectors = sorted({node for row in registry['rows'] for node in row['test_nodes']})
    selectors += ['tests/refactor/engines/test_native_restored_boundaries.py',
                  'tests/refactor/engines/test_native_services.py',
                  'tests/refactor/engines/test_control_metadata.py',
                  'tests/refactor/contracts/test_architecture.py']
    paths = list((ROOT/'pythonstan').rglob('*.py'))
    paths += list((ROOT/'pythonstan_inspector').rglob('*.py'))
    paths += list((ROOT/'scripts/refactor').glob('*.py'))
    paths += [ROOT/node.split('::', 1)[0] for node in selectors]
    paths += [ROOT/'pythonstan/config/control_metadata.json', REGISTRY]
    before = digest(paths)
    command = [sys.executable, '-m', 'pytest', '--import-mode=importlib', '-q', *selectors]
    started = time.monotonic()
    with (HERE/'current_native_gate.stdout.txt').open('w') as stdout, (HERE/'current_native_gate.stderr.txt').open('w') as stderr:
        result = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr)
    after = digest(paths)
    receipt = {'schema_version': 1, 'owner': 'recover_native_arch',
               'command': command, 'exit_code': result.returncode,
               'wall_seconds': time.monotonic()-started,
               'native_option_rows': len(registry['rows']),
               'registered_contexts': len(registry['registered_context_cases']),
               'source_before': before, 'source_after': after,
               'source_stable': before == after,
               'changed_during_run': sorted(key for key in before if before[key] != after[key]),
               'scope': 'Author operational Native controls, Source inputs and architecture; independent review and release gates remain separate.'}
    (HERE/'current_native_gate.receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps({key: receipt[key] for key in ('exit_code','native_option_rows','registered_contexts','source_stable','changed_during_run')}))
    return result.returncode or (0 if before == after else 2)


if __name__ == '__main__':
    raise SystemExit(main())
