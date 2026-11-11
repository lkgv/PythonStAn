import hashlib
import json
import subprocess
import sys

from scripts import analyze_project as cli
from tests.round13_runtime.test_worker_terminal_contract import BOOTSTRAP


def test_actual_worker_versions_overlap_clock_and_binds_exact_flushed_payload(tmp_path):
    progress = tmp_path / 'progress.jsonl'
    flush_observation = tmp_path / 'flush.json'
    configuration = {'project_root': str(tmp_path), 'entry_file': 'large'}
    payload = {'configuration': configuration, 'memory_mb': 128, 'progress_path': str(progress),
               'run_id': 'stream-clock', 'request_sha256': cli._request_digest(configuration)}
    observe_flush = r'''
class ObservedFlush:
    def __init__(self, original):
        self.original = original
    def write(self, text):
        return self.original.write(text)
    def flush(self):
        self.original.flush()
        rows = [json.loads(line) for line in progress_path.read_text().splitlines()]
        pathlib.Path(FLUSH_PATH).write_text(json.dumps({'phase_at_stdout_flush': rows[-1]['phase']}))
sys.stdout = ObservedFlush(sys.stdout)
'''.replace('FLUSH_PATH', repr(str(flush_observation)))
    bootstrap = BOOTSTRAP.replace("sys.argv = [cli.__file__, '--worker']", observe_flush + "\nsys.argv = [cli.__file__, '--worker']")
    completed = subprocess.run([sys.executable, '-I', '-S', '-c', bootstrap, str(cli.ROOT)],
                               input=json.dumps(payload), text=True, capture_output=True, timeout=3)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith('PROJECT_JSON:') and completed.stdout.endswith('\n')
    body = completed.stdout[len('PROJECT_JSON:'):-1].encode('ascii')
    report = json.loads(body)
    assert report['statistics'] == {'iterations': 17, 'converged': 0, 'pending_work': 9}
    events = [json.loads(line) for line in progress.read_text().splitlines()]
    terminal = events[-3:]
    assert [event['phase'] for event in terminal] == ['worker_serialization_started', 'worker_json_ready', 'worker_output_completed']
    assert all(event['worker_transport_protocol'] == 'project_json_line_stream_v2' for event in terminal)
    assert all(event['worker_terminal_clock_version'] == 2 for event in terminal)
    ready = terminal[1]
    assert ready['worker_json_bytes'] == len(body)
    assert ready['worker_json_sha256'] == hashlib.sha256(body).hexdigest()
    assert ready['worker_json_max_write_characters'] <= 65536
    assert ready['worker_json_max_encoder_fragment_characters'] > 65536
    assert ready['worker_json_ready_meaning'] == 'encoding_and_stdout_flush_completed'
    assert json.loads(flush_observation.read_text())['phase_at_stdout_flush'] == 'worker_serialization_started'
    assert [event['worker_elapsed_seconds'] for event in terminal] == sorted(
        event['worker_elapsed_seconds'] for event in terminal)
