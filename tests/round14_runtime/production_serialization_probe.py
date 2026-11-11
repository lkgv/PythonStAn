"""Repeat the fixed RLIMIT witness using the actual production writer."""
import argparse
import hashlib
import json
from pathlib import Path

import serialization_probe


ROOT = Path(__file__).resolve().parents[2]
CHILD = r'''
import hashlib, json, os, pathlib, resource, sys, time
sys.path.insert(0, REPOSITORY)
from scripts.analyze_project import _write_project_json
mode, metadata = sys.argv[1:]
limit = (512 if mode == 'reference' else 128) * 1024 * 1024
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
arena = bytearray(80 * 1024 * 1024)
report = {'status': 'incomplete', 'solver_completed': False, 'call_edges': [],
          'statistics': {'iterations': 50000, 'pending_work': 9},
          'whole_program_soundness_proved': False, 'payload': ['x' * 64] * 600000}
started = time.monotonic()
count = 0
encoded_sha = None
largest_fragment = largest_write = 0
status = 'success'
try:
    if mode in ('full', 'reference'):
        encoded = json.dumps(report)
        sys.stdout.write('PROJECT_JSON:')
        sys.stdout.write(encoded)
        sys.stdout.write('\n')
        sys.stdout.flush()
        count = len(encoded)
        encoded_sha = hashlib.sha256(encoded.encode('ascii')).hexdigest()
        largest_write = len(encoded)
    else:
        emitted = _write_project_json(report, sys.stdout)
        count = emitted['payload_bytes']
        encoded_sha = emitted['payload_sha256']
        largest_write = emitted['max_write_characters']
        largest_fragment = emitted['max_encoder_fragment_characters']
except MemoryError:
    status = 'memory_error'
peak = None
for line in pathlib.Path('/proc/self/status').read_text().splitlines():
    if line.startswith('VmHWM:'):
        peak = int(line.split()[1]) * 1024
result = {'mode': mode, 'status': status, 'rlimit_as_bytes': limit,
          'arena_bytes': len(arena), 'report_list_rows': 600000, 'report_atom_characters': 64,
          'encoded_bytes': count, 'encoded_sha256': encoded_sha,
          'largest_iterencode_fragment_characters': largest_fragment,
          'largest_write_characters': largest_write,
          'peak_rss_bytes': peak, 'peak_rss_method': 'linux_proc_self_status_VmHWM',
          'elapsed_seconds': time.monotonic() - started}
pathlib.Path(metadata).write_text(json.dumps(result))
os._exit(0)
'''.replace('REPOSITORY', repr(str(ROOT)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    output = parser.parse_args().output
    serialization_probe.CHILD = CHILD
    serialization_probe.run_probes(output)
    result = json.loads(output.read_text())
    result['schema'] = 'round14_production_json_memory_probe_v1'
    result['production_writer_used'] = True
    result['production_script_sha256'] = hashlib.sha256((ROOT / 'scripts/analyze_project.py').read_bytes()).hexdigest()
    result['runner_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    output.write_text(json.dumps(result, indent=2) + '\n')
