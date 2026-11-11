"""Private worker lifecycle tests with trusted stand-ins, never target execution."""
from pathlib import Path
import os
import signal
import subprocess
import sys
import time

import pytest

from scripts import analyze_project as cli


BOOTSTRAP = r'''
import atexit, json, os, pathlib, subprocess, sys, time, types
sys.path.insert(0, sys.argv[1])
from scripts import analyze_project as cli
api = types.ModuleType('pythonstan.analysis.project')
class Config:
    @classmethod
    def from_dict(cls, configuration):
        return configuration
class FlushFailure:
    def __init__(self, original):
        self.original = original
    def write(self, data):
        return self.original.write(data)
    def flush(self):
        raise OSError('controlled flush failure')
def analyze(configuration, progress):
    mode = configuration['entry_file']
    progress({'sequence': 1, 'phase': 'report_ready',
              'implementation_sha256_before': 'a' * 64,
              'configuration_sha256': 'b' * 64})
    if mode == 'analysis_error':
        raise ValueError('controlled analysis error')
    if mode == 'no_json':
        os._exit(0)
    if mode == 'torn_json':
        os.write(1, b'PROJECT_JSON:{"status":')
        os._exit(0)
    if mode in ('descendant_pipe', 'escaped_descendant_pipe'):
        marker = pathlib.Path(configuration['project_root']) / 'descendant.pid'
        code = ('import os,pathlib,signal,time; '
                'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                'pathlib.Path(' + repr(str(marker)) + ').write_text(str(os.getpid())); '
                'time.sleep(30)')
        subprocess.Popen([sys.executable, '-I', '-S', '-c', code],
                         start_new_session=mode == 'escaped_descendant_pipe')
        deadline = time.monotonic() + 0.5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert marker.exists(), 'trusted descendant did not start'
    if mode == 'delayed_shutdown':
        atexit.register(time.sleep, 2)
    if mode == 'stdout_failure':
        sys.stdout = FlushFailure(sys.stdout)
    if mode == 'stderr_failure':
        sys.stderr = FlushFailure(sys.stderr)
    if mode == 'progress_failure':
        progress_path.unlink()
        progress_path.mkdir()
    report = {'status': 'incomplete', 'solver_completed': False,
            'first_party_translation_complete': True,
            'whole_program_soundness_proved': False, 'semantic_complete': False,
            'call_edges': [{'caller': 'trusted', 'callee': 'controlled'}],
            'statistics': {'iterations': 17, 'converged': 0, 'pending_work': 9},
            'obligations_retained': True}
    if mode == 'large':
        report['payload'] = 'x' * (1024 * 1024)
    return report
api.ProjectConfig = Config
api.analyze_project = analyze
sys.modules[api.__name__] = api
payload = json.load(sys.stdin)
progress_path = pathlib.Path(payload['progress_path'])
mode = payload['configuration']['entry_file']
if mode == 'delayed_terminal':
    original_exit = os._exit
    def delayed_exit(code):
        time.sleep(2)
        original_exit(code)
    os._exit = delayed_exit
if mode == 'nonzero_terminal':
    original_exit = os._exit
    os._exit = lambda code: original_exit(7)
import io
sys.stdin = io.StringIO(json.dumps(payload))
sys.argv = [cli.__file__, '--worker']
code = cli.main()
if mode == 'nonzero_terminal':
    os._exit(7)
if mode == 'delayed_terminal':
    time.sleep(2)
raise SystemExit(code)
'''


@pytest.fixture
def controlled_supervisor(monkeypatch, tmp_path):
    original_popen = subprocess.Popen
    def controlled_popen(command, **kwargs):
        return original_popen([sys.executable, '-I', '-S', '-c', BOOTSTRAP, str(cli.ROOT)], **kwargs)
    monkeypatch.setattr(cli.subprocess, 'Popen', controlled_popen)
    def run(mode, timeout=0.7):
        return cli.run_isolated({'project_root': str(tmp_path), 'entry_file': mode}, timeout=timeout)
    return run


def test_private_worker_exits_after_complete_output_without_atexit_wait(controlled_supervisor):
    result = controlled_supervisor('delayed_shutdown')
    assert result['status'] == 'incomplete'
    assert result['solver_completed'] is False
    assert result['call_edges'] == [{'caller': 'trusted', 'callee': 'controlled'}]
    assert result['statistics'] == {'iterations': 17, 'converged': 0, 'pending_work': 9}
    assert result['whole_program_soundness_proved'] is False
    assert result['execution_progress']['last_phase'] == 'worker_output_completed'


def test_complete_analysis_error_report_preserves_its_status(controlled_supervisor):
    result = controlled_supervisor('analysis_error')
    assert result['status'] == 'analysis_error'
    assert result['diagnostic'] == 'ValueError: controlled analysis error'
    assert result['solver_completed'] is False
    assert result['call_edges'] == []


@pytest.mark.parametrize('mode', ['stdout_failure', 'stderr_failure', 'progress_failure',
                                  'nonzero_terminal', 'no_json', 'torn_json'])
def test_terminal_errors_never_reach_success_exit(controlled_supervisor, mode):
    result = controlled_supervisor(mode)
    assert result['status'] == 'worker_error'
    if mode in ('no_json', 'torn_json'):
        assert result['returncode'] == 0  # Process success alone is insufficient.
    else:
        assert result['returncode'] != 0
    assert result['solver_completed'] is False
    assert result['call_edges'] == []
    assert result['whole_program_soundness_proved'] is False


def test_large_report_retains_complete_payload_and_counters(controlled_supervisor):
    result = controlled_supervisor('large', timeout=2)
    assert result['status'] == 'incomplete'
    assert result['payload'] == 'x' * (1024 * 1024)
    assert result['statistics'] == {'iterations': 17, 'converged': 0, 'pending_work': 9}
    assert result['execution_progress']['last_phase'] == 'worker_output_completed'


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX process-group ownership witness')
def test_exited_worker_and_term_ignoring_descendant_pipe_are_bounded(controlled_supervisor, tmp_path):
    result = controlled_supervisor('descendant_pipe')
    assert result['status'] == 'timeout'
    assert result['solver_completed'] is False and result['call_edges'] == []
    assert result['partial_progress']['last_phase'] == 'worker_output_completed'
    termination = result['resource_limits']['worker_termination']
    assert termination['scope'] == 'owned_posix_process_group'
    assert termination['child_reaped'] is True
    assert termination['child_returncode'] == 0
    assert termination['pipes_drained'] is True and termination['pipes_closed'] is True
    assert termination['cleanup_wall_seconds'] < termination['cleanup_grace_seconds']
    descendant = int((tmp_path / 'descendant.pid').read_text())
    stat = Path('/proc') / str(descendant) / 'stat'
    if stat.exists():
        # A reparented zombie is dead; this process cannot reap a grandchild.
        assert stat.read_text().split(') ', 1)[1].split()[0] == 'Z'


@pytest.mark.skipif(os.name != 'posix', reason='POSIX escaped-session boundary witness')
def test_escaping_descendant_is_explicit_and_pipe_cleanup_still_has_a_deadline(controlled_supervisor, tmp_path):
    started = time.monotonic()
    try:
        result = controlled_supervisor('escaped_descendant_pipe', timeout=0.3)
        assert time.monotonic() - started < 2.5
        assert result['status'] == 'timeout' and result['call_edges'] == []
        termination = result['resource_limits']['worker_termination']
        assert termination['escaping_descendants_covered'] is False
        assert termination['child_reaped'] is True and termination['child_returncode'] == 0
        assert termination['pipes_drained'] is False and termination['pipes_closed'] is True
        assert any('TimeoutExpired' in error for error in termination['errors'])
    finally:
        marker = tmp_path / 'descendant.pid'
        if marker.exists():
            try:
                os.kill(int(marker.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_complete_output_followed_by_deadline_remains_timeout(controlled_supervisor):
    result = controlled_supervisor('delayed_terminal')
    assert result['status'] == 'timeout'
    assert result['partial_progress']['last_phase'] == 'worker_output_completed'
    assert result['solver_completed'] is False
    assert result['call_edges'] == []
    assert result['whole_program_soundness_proved'] is False


def test_real_inventory_worker_delivers_terminal_record_without_executing_source(tmp_path):
    (tmp_path / 'main.py').write_text("raise AssertionError('target source must not execute')\n")
    result = cli.run_isolated({'project_root': str(tmp_path), 'entry_file': 'main.py',
                               'analysis_stage': 'inventory'}, timeout=10)
    assert result['status'] == 'inventory_completed'
    assert result['execution_progress']['last_phase'] == 'worker_output_completed'
    assert result['whole_program_soundness_proved'] is False


def test_unsupported_bounded_worker_capability_refuses_before_launch(monkeypatch):
    monkeypatch.setattr(cli, '_bounded_worker_supported', lambda: False)
    def forbidden_launch(*args, **kwargs):
        raise AssertionError('unsupported platform must not launch a child')
    monkeypatch.setattr(cli.subprocess, 'Popen', forbidden_launch)
    result = cli.run_isolated({'project_root': '/unused', 'entry_file': 'unused.py'})
    assert result['status'] == 'worker_error'
    assert result['analysis_executed'] is False
    assert result['solver_completed'] is False and result['call_edges'] == []
    assert result['resource_limits']['platform_support'] == 'unsupported_platform'
    assert result['resource_limits']['bounded_pipe_cleanup_supported'] is False
    assert result['resource_limits']['worker_exit_policy'] == 'disposable_after_owned_output_flush'
    assert result['resource_limits']['cleanup_grace_seconds'] == 1.0
    assert result['returncode'] is None
