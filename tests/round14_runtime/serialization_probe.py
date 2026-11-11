"""Trusted fixed-report RLIMIT witnesses, before production transport changes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


CHILD = r'''
import hashlib, json, os, pathlib, resource, sys, time
mode, metadata = sys.argv[1:]
limit = (512 if mode == 'reference' else 128) * 1024 * 1024
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
arena = bytearray(80 * 1024 * 1024)
report = {'status': 'incomplete', 'solver_completed': False, 'call_edges': [],
          'statistics': {'iterations': 50000, 'pending_work': 9},
          'whole_program_soundness_proved': False, 'payload': ['x' * 64] * 600000}
started = time.monotonic()
count = 0
digest = hashlib.sha256()
largest_fragment = largest_write = 0
status = 'success'
try:
    if mode in ('full', 'reference'):
        encoded = json.dumps(report)
        sys.stdout.write('PROJECT_JSON:')
        sys.stdout.write(encoded)
        count = len(encoded)
        digest.update(encoded.encode('ascii'))
        largest_write = len(encoded)
    else:
        sys.stdout.write('PROJECT_JSON:')
        pending = []
        size = 0
        for fragment in json.JSONEncoder().iterencode(report):
            largest_fragment = max(largest_fragment, len(fragment))
            for offset in range(0, len(fragment), 65536):
                piece = fragment[offset:offset + 65536]
                if size + len(piece) > 65536:
                    chunk = ''.join(pending)
                    sys.stdout.write(chunk)
                    largest_write = max(largest_write, len(chunk))
                    pending.clear()
                    size = 0
                pending.append(piece)
                size += len(piece)
                count += len(piece)
                digest.update(piece.encode('ascii'))
        if pending:
            chunk = ''.join(pending)
            sys.stdout.write(chunk)
            largest_write = max(largest_write, len(chunk))
    sys.stdout.write('\n')
    sys.stdout.flush()
except MemoryError:
    status = 'memory_error'
peak = None
for line in pathlib.Path('/proc/self/status').read_text().splitlines():
    if line.startswith('VmHWM:'):
        peak = int(line.split()[1]) * 1024
result = {'mode': mode, 'status': status, 'rlimit_as_bytes': limit,
          'arena_bytes': len(arena), 'report_list_rows': 600000, 'report_atom_characters': 64,
          'encoded_bytes': count, 'encoded_sha256': digest.hexdigest() if status == 'success' else None,
          'largest_iterencode_fragment_characters': largest_fragment,
          'largest_write_characters': largest_write,
          'peak_rss_bytes': peak, 'peak_rss_method': 'linux_proc_self_status_VmHWM',
          'elapsed_seconds': time.monotonic() - started}
pathlib.Path(metadata).write_text(json.dumps(result))
os._exit(0)
'''


def run_probes(output):
    rows = []
    with tempfile.TemporaryDirectory(prefix='round14-json-probe-') as temporary:
        directory = Path(temporary)
        for mode in ('full', 'stream', 'reference'):
            metadata = directory / (mode + '.json')
            payload = directory / (mode + '.stdout')
            with payload.open('wb') as stream:
                completed = subprocess.run([sys.executable, '-I', '-S', '-c', CHILD, mode, str(metadata)],
                                           stdout=stream, stderr=subprocess.PIPE, timeout=15)
            assert completed.returncode == 0, completed.stderr.decode(errors='replace')
            row = json.loads(metadata.read_text())
            row['stdout_bytes'] = payload.stat().st_size
            digest = hashlib.sha256()
            with payload.open('rb') as stream:
                while chunk := stream.read(65536):
                    digest.update(chunk)
            row['stdout_sha256'] = digest.hexdigest()
            rows.append(row)
    full, stream, reference = rows
    assert full['status'] == 'memory_error' and stream['status'] == 'success'
    assert full['rlimit_as_bytes'] == stream['rlimit_as_bytes']
    assert stream['encoded_sha256'] == reference['encoded_sha256']
    assert stream['stdout_sha256'] == reference['stdout_sha256']
    assert stream['largest_write_characters'] <= 65536
    result = {'schema': 'round14_prepatch_json_memory_probe_v1', 'rows': rows,
              'same_primary_report_and_rlimit': True, 'reference_has_separate_larger_limit': True,
              'child_sha256': hashlib.sha256(CHILD.encode()).hexdigest(),
              'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'target_source_executed': False, 'production_source_changed': False,
              'bound_scope': 'write chunks; stock iterencode may allocate a whole escaped string leaf'}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': 'passed', 'rows': [(row['mode'], row['status']) for row in rows]}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run_probes(parser.parse_args().output)
