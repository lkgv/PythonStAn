"""Cold receipt cadence controls; no target package is imported or executed."""
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from pythonstan_inspector import incident, project_process, project_runner
from pythonstan_inspector.contract import CaptureRequest
from pythonstan_inspector.incident import IncidentRecorder, read_receipt
from pythonstan_inspector.project_runner import _ProjectDiagnostic, run_project


def clock(monkeypatch):
    current = [0.0]
    controlled = SimpleNamespace(monotonic=lambda: current[0], perf_counter=time.perf_counter)
    monkeypatch.setattr(incident, 'time', controlled)
    monkeypatch.setattr(project_runner, 'time', controlled)
    return current


def bridge(tmp_path):
    return _ProjectDiagnostic(tmp_path, 'run', CaptureRequest(), request_sha256='request',
                              observation_mode='counters')


def test_routine_phase_gate_precedes_counter_sampling_and_keeps_new_memory_cut(tmp_path, monkeypatch):
    tick = clock(monkeypatch)
    observer = bridge(tmp_path)
    writes = observer.receipt.persistence['successful_writes']
    class Solver:
        calls = 0
        def get_inspector_counters(self):
            self.calls += 1
            return {'status': 'controlled_scalar_counter'}
    solver = Solver()
    observer.solver = solver
    for seq in range(1, 101):
        observer.observe_phase({'phase': 'module_frontend_started', 'sequence': seq,
                                'module': 'app', 'frontend_phase': 'ir'})
    persisted = read_receipt(tmp_path/'incident.json', 'run', 'request')
    assert observer.receipt.data['phase']['last_progress_sequence'] == 100
    assert persisted['phase']['last_progress_sequence'] is None
    assert observer.receipt.persistence['successful_writes'] == writes
    assert solver.calls == 0
    tick[0] = 1.0
    observer.observe_phase({'phase': 'module_frontend_completed', 'sequence': 101})
    persisted = read_receipt(tmp_path/'incident.json', 'run', 'request')
    assert persisted['phase']['last_progress_sequence'] == 101
    assert observer.receipt.persistence['successful_writes'] == writes + 1
    assert solver.calls == 1
    assert observer.observation_costs['routine_checkpoint_skips'] == 100
    assert observer.observation_costs['counter_sampling_calls'] == 1


@pytest.mark.parametrize('phase', ['inventory_started', 'inventory_completed', 'import_graph_started',
    'closure_started', 'pointer_analysis_started', 'solver_fixpoint_started', 'export_entries_started',
    'report_statistics_started', 'report_ready', 'analysis_error', 'analysis_resource_exhausted',
    'module_budget_exhausted', 'module_frontend_error', 'future_unknown_stage'])
def test_critical_and_unknown_phases_force_independent_receipt(tmp_path, monkeypatch, phase):
    clock(monkeypatch)
    observer = bridge(tmp_path)
    observer.observe_phase({'phase': phase, 'sequence': 1})
    saved = read_receipt(tmp_path/'incident.json', 'run', 'request')
    assert saved['phase']['last_progress_sequence'] == 1
    assert observer.receipt.persistence['successful_writes'] == 2
    assert observer.receipt.persistence['forced_attempts'] == 2


def test_failed_routine_write_gates_attempts_and_critical_force_bypasses(tmp_path, monkeypatch):
    tick = clock(monkeypatch)
    recorder = IncidentRecorder(tmp_path, 'run', 'request', 'a'*64, {}, 'counters')
    attempted = []
    def fail(*args, **kwargs):
        attempted.append(tick[0])
        raise OSError('controlled_io_failure')
    monkeypatch.setattr(incident, 'write_receipt', fail)
    assert recorder.checkpoint() is False
    for _ in range(100):
        assert recorder.checkpoint() is False
    assert attempted == [0.0]
    tick[0] = 1.0
    assert recorder.checkpoint() is False
    assert recorder.checkpoint(force=True) is False
    assert attempted == [0.0, 1.0, 1.0]
    assert recorder.persistence['failed_attempts'] == 3
    assert recorder.persistence['attempts_completed'] == 3
    assert recorder.persistence['successful_writes'] == 0
    assert recorder.persistence['routine_attempts'] == 2
    assert recorder.persistence['forced_attempts'] == 1
    assert recorder.persistence['cadence_skips'] == 100


def test_native_already_scheduled_cold_callback_remains_forced(tmp_path, monkeypatch):
    clock(monkeypatch)
    observer = bridge(tmp_path)
    observer.cold_checkpoint()  # Native's one-second/1024-work gate already ran.
    assert observer.receipt.persistence['successful_writes'] == 2
    assert observer.receipt.persistence['forced_attempts'] == 2
    assert observer.receipt.persistence['routine_attempts'] == 0


def test_cold_failure_before_receipt_writer_is_attempt_gated(tmp_path, monkeypatch):
    tick = clock(monkeypatch)
    observer = bridge(tmp_path)
    entered = []
    def fail_before_receipt():
        entered.append(tick[0])
        raise MemoryError('controlled_prewrite_trace_failure')
    monkeypatch.setattr(observer, '_persist_trace_batch', fail_before_receipt)
    tick[0] = 1.0
    for sequence in range(1, 101):
        observer.observe_phase({'phase': 'module_frontend_started', 'sequence': sequence})
    assert entered == [1.0]
    assert observer.receipt.persistence['successful_writes'] == 1  # bootstrap only
    assert observer.observation_costs['cold_checkpoint_failures'] == 1
    tick[0] = 2.0
    observer.observe_phase({'phase': 'module_frontend_started', 'sequence': 101})
    observer.observe_phase({'phase': 'solver_fixpoint_started', 'sequence': 102})
    assert entered == [1.0, 2.0, 2.0]
    assert observer.observation_costs['cold_checkpoint_failures'] == 3
    assert observer.receipt.data['health']['cold_checkpoint_failed'] is True


def test_receipt_cost_cut_does_not_claim_current_write_measured_itself(tmp_path):
    observer = bridge(tmp_path)
    observer.observe_phase({'phase': 'report_ready', 'sequence': 1})
    saved = read_receipt(tmp_path/'incident.json', 'run', 'request')
    recorded = saved['receipt_persistence']
    current = observer.receipt.persistence
    assert recorded['attempts_started'] == current['attempts_started'] == 2
    assert recorded['attempts_completed'] == 1 < current['attempts_completed'] == 2
    assert recorded['successful_writes'] == 1 < current['successful_writes'] == 2
    assert recorded['last_successful_write_worker_elapsed_seconds'] is not None
    assert recorded['validation_encode_seconds'] >= 0
    assert recorded['write_replace_seconds'] >= 0
    assert saved['observation_costs']['phase_observer_calls'] == 0
    assert observer.observation_costs['phase_observer_calls'] == 1
    assert len((tmp_path/'incident.json').read_bytes()) < incident.MAX_RECEIPT_BYTES


def replace_worker(monkeypatch, code):
    original = project_process.subprocess.Popen
    def launch(command, **kwargs):
        return original([sys.executable, '-c', code], **kwargs)
    monkeypatch.setattr(project_process.subprocess, 'Popen', launch)


def test_timeout_before_long_frontend_retains_new_progress_and_older_receipt(tmp_path, monkeypatch):
    (tmp_path/'app.py').write_text('result=1\n')
    replace_worker(monkeypatch, '''import time
from pythonstan.world.analysis_manager import AnalysisManager
original=AnalysisManager.analysis
def slow(self, name, scope):
    if name == 'ir':
        time.sleep(10)
    return original(self, name, scope)
AnalysisManager.analysis=slow
from pythonstan_inspector.project_runner import _worker
_worker()
''')
    result = run_project({'project_root': str(tmp_path), 'entry_file': 'app.py',
                          'auto_framework_entries': False}, tmp_path/'incident',
                         observation_mode='counters', timeout_seconds=2)
    assert result['status'] == 'timeout'
    progress, saved = result['partial_progress'], result['worker_incident_receipt']
    assert progress['last_phase'] == 'module_frontend_started'
    assert progress['last_frontend_phase'] == 'ir'
    assert saved['phase']['last_progress_sequence'] < progress['events']
    assert saved['phase']['last_completed'] == 'inventory_completed'
    assert saved['identity']['source_verification'] == 'unverified'
    assert saved['native']['status'] == 'not_observed'
    assert result['bundle'] is None


def test_progress_failure_keeps_prefix_gap_and_critical_final_receipt(tmp_path, monkeypatch):
    (tmp_path/'app.py').write_text('result=1\n')
    replace_worker(monkeypatch, '''from pythonstan_inspector import project_runner as runner
original=runner._json_bytes
def fail(value):
    if type(value) is dict and value.get('phase') == 'module_frontend_started':
        raise OSError('controlled_progress_delivery_failure')
    return original(value)
runner._json_bytes=fail
runner._worker()
''')
    result = run_project({'project_root': str(tmp_path), 'entry_file': 'app.py',
                          'auto_framework_entries': False}, tmp_path/'incident',
                         observation_mode='counters', timeout_seconds=10)
    assert result['status'] == 'worker_finished', result
    assert result['partial_progress']['events'] == 3  # inventory + parse only
    saved = result['worker_incident_receipt']
    assert saved['health']['progress_write_error'] == 'progress_prefix_stopped'
    assert saved['phase']['last_completed'] == 'inspector_worker_finished'
    assert saved['observation_costs']['progress_write_failures'] == 1
    assert saved['native']['solver_completed'] is True
    assert saved['capture']['status'] == 'not_requested'
