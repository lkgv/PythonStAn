"""Copied-install independence, native-frame parity, and private-worker exit."""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from pythonstan_inspector import project_process
from pythonstan_inspector.project_progress import _read_progress
from scripts.analyze_project import _read_progress as native_read_progress


ROOT = Path(__file__).resolve().parents[2]


def test_native_progress_decoder_has_exact_function_ast_parity():
    import inspect
    assert ast.dump(ast.parse(inspect.getsource(_read_progress))) == ast.dump(
        ast.parse(inspect.getsource(native_read_progress)))


def test_package_process_cleanup_matches_frozen_stdlib_contract():
    import inspect
    from scripts import analyze_project as native_cli
    for name in ('_bounded_worker_supported', '_close_worker_pipes'):
        assert ast.dump(ast.parse(inspect.getsource(getattr(project_process, name)))) == ast.dump(
            ast.parse(inspect.getsource(getattr(native_cli, name))))
    # The installed supervisor now drains bounded binary chunks without
    # communicate's unbounded retained output. Its cleanup is intentionally a
    # separately versioned capability, exercised by real-pipe negative tests.
    assert project_process.worker_limits()['process_transport_protocol'] == 'bounded_binary_selectors_v2'
    cleanup_ast = ast.parse(inspect.getsource(project_process._terminate_worker))
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == 'communicate' for node in ast.walk(cleanup_ast))


@pytest.mark.parametrize('variant', ['complete', 'torn', 'stale', 'out_of_order', 'identity_change'])
def test_native_progress_decoder_golden_binding_and_torn_frame_parity(tmp_path, variant):
    base = {'run_id': 'run', 'request_sha256': 'request',
            'implementation_sha256_before': 'a' * 64, 'configuration_sha256': 'b' * 64}
    module = {'path': 'app.py', 'module': 'app', 'ownership': 'first_party',
              'sha256': 'c' * 64, 'bytes': 4, 'parsed': False}
    frames = [{**base, 'sequence': 1, 'phase': 'inventory_completed', 'inventory': [module],
               'worker_elapsed_seconds': 0.1},
              {**base, 'sequence': 2, 'phase': 'module_frontend_completed',
               'module_record': {**module, 'parsed': True, 'lowered': True},
               'worker_elapsed_seconds': 0.2},
              {**base, 'sequence': 3, 'phase': 'worker_output_completed',
               'worker_elapsed_seconds': 0.3}]
    if variant == 'stale':
        frames[-1]['run_id'] = 'another'
    if variant == 'out_of_order':
        frames[-1]['sequence'] = 9
    if variant == 'identity_change':
        frames[1]['module_record']['sha256'] = 'd' * 64
    path = tmp_path / 'progress.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in frames)
                    + ('{"unfinished":' if variant == 'torn' else ''))
    actual = _read_progress(path, 'run', 'request')
    assert actual == native_read_progress(path, 'run', 'request')
    assert actual['coverage_observed']['discovered_python_files'] == 1
    assert actual['semantic_complete'] is False and actual['call_projection_completed'] is False
    if variant in ('complete', 'torn'):
        assert actual['events'] == 3
        assert actual['last_phase'] == 'worker_output_completed'
        assert actual['coverage_observed']['lowered_modules'] == 1
    else:
        assert actual['rejected_or_torn_frames'] > 0


@pytest.fixture
def copied_install(tmp_path):
    site = tmp_path / 'site'
    site.mkdir()
    for name in ('pythonstan', 'pythonstan_inspector'):
        shutil.copytree(ROOT / name, site / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    assert not (site / 'scripts').exists()
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'app.py').write_text('def callback():\n return 7\ndef entry():\n return callback()\nentry()\n')
    return site, project


PARENT = r'''
import importlib.abc, json, pathlib, sys
site, project, stage, command = sys.argv[1:]
sys.path.insert(0, site)
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'scripts' or fullname.startswith('scripts.'):
            raise AssertionError('installed supervisor imported repository scripts: ' + fullname)
        if fullname == 'pythonstan' or fullname.startswith('pythonstan.'):
            raise AssertionError('supervisor imported native analysis: ' + fullname)
sys.meta_path.insert(0, Block())
config = {'project_root': project, 'entry_file': 'app.py', 'analysis_stage': stage,
          'max_iterations': 2000, 'auto_framework_entries': False}
if command == 'cli':
    from pythonstan_inspector.cli import main
    path = pathlib.Path(project) / 'config.json'
    path.write_text(json.dumps(config))
    code = main(['capture-project', str(path), str(pathlib.Path(project) / 'capture'), '--timeout', '15'])
    assert code == 0
else:
    from pythonstan_inspector.project_runner import run_project
    result = run_project(config, pathlib.Path(project) / 'capture', timeout_seconds=15)
    assert result['status'] == 'worker_finished', result
    if stage == 'inventory':
        assert result['native_report']['status'] == 'inventory_completed', result
        assert result['bundle'] is None
        assert result['capture_state']['status'] == 'unsupported'
    else:
        assert result['native_report']['solver_completed'] is True, result
        assert ['app.entry', 'app.callback'] in result['native_report']['call_edges']
        assert result['capture_state']['status'] == 'sealed', result
        from pythonstan_inspector.storage import load_bundle
        from pythonstan_inspector.core import query
        from pythonstan_inspector.contract import QueryRequest
        bundle = load_bundle(result['bundle'])
        assert query(bundle, QueryRequest('summary'))['boundary']['coherent'] is True
    assert result['resource_limits']['worker_exit_policy'] == 'disposable_after_owned_output_flush'
    print(json.dumps(result))
assert not any(name == 'scripts' or name.startswith('scripts.') or
               name == 'pythonstan' or name.startswith('pythonstan.') for name in sys.modules)
import pythonstan_inspector.project_runner as runner
assert pathlib.Path(runner.__file__).is_relative_to(pathlib.Path(site))
'''


@pytest.mark.parametrize(('stage', 'command'), [('inventory', 'api'), ('solver', 'api'), ('solver', 'cli')])
def test_copied_install_runs_native_child_without_checkout_scripts_or_native_parent(copied_install, stage, command):
    site, project = copied_install
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    completed = subprocess.run([sys.executable, '-I', '-S', '-c', PARENT,
                                str(site), str(project), stage, command], cwd=site,
                               env=env, text=True, capture_output=True, timeout=20)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result['status'] == 'worker_finished'
    assert result['native_report']['whole_program_soundness_proved'] is False
    if stage == 'solver':
        assert result['capture_state']['status'] == 'sealed'


def test_inspector_platform_refusal_has_no_child_launch(monkeypatch):
    monkeypatch.setattr(project_process, '_bounded_worker_supported', lambda: False)
    def forbidden(*args, **kwargs):
        raise AssertionError('unsupported capability must not launch')
    monkeypatch.setattr(project_process.subprocess, 'Popen', forbidden)
    with pytest.raises(project_process.UnsupportedWorkerPlatform):
        project_process.run_worker_process(['unused'], input='', cwd='.', timeout=1)
    assert project_process.worker_limits()['platform_support'] == 'unsupported_platform'


TRUSTED_WORKER = r'''
import atexit, os, sys, time, types
from pythonstan_inspector import project_runner
mode = MODE
api = types.ModuleType('pythonstan.analysis.project')
class Config:
    @classmethod
    def from_dict(cls, value):
        return value
def analyze(configuration, *, progress, diagnostic):
    progress({'sequence': 1, 'phase': 'report_ready',
              'implementation_sha256_before': 'a' * 64, 'configuration_sha256': 'b' * 64})
    if mode == 'atexit':
        atexit.register(time.sleep, 2)
    return {'status': 'incomplete', 'solver_completed': False,
            'whole_program_soundness_proved': False,
            'statistics': {'iterations': 31, 'pending_work': 5}}
api.ProjectConfig = Config
api.analyze_project = analyze
sys.modules[api.__name__] = api
if mode == 'delayed_exit':
    exit_process = os._exit
    def delayed_exit(code):
        time.sleep(2)
        exit_process(code)
    os._exit = delayed_exit
project_runner._worker()
'''


@pytest.mark.parametrize('mode', ['atexit', 'delayed_exit'])
def test_actual_inspector_worker_disposable_exit_and_timeout_are_distinct(tmp_path, monkeypatch, mode):
    from pythonstan_inspector.project_runner import run_project
    original_popen = subprocess.Popen
    code = TRUSTED_WORKER.replace('mode = MODE', 'mode = ' + repr(mode))
    def injected(command, **kwargs):
        return original_popen([sys.executable, '-c', code], **kwargs)
    monkeypatch.setattr(project_process.subprocess, 'Popen', injected)
    result = run_project({'project_root': '/trusted-control', 'entry_file': 'unused.py'},
                         tmp_path / 'capture', timeout_seconds=0.7)
    assert result['native_report']['status'] == 'incomplete'
    assert result['native_report']['statistics'] == {'iterations': 31, 'pending_work': 5}
    assert result['native_report']['solver_completed'] is False
    assert result['bundle'] is None
    if mode == 'atexit':
        assert result['status'] == 'worker_finished'
        assert result['partial_progress']['last_phase'] == 'inspector_worker_finished'
    else:
        assert result['status'] == 'timeout'
        assert result['capture_state']['status'] == 'unsupported'
        assert result['worker_incident_receipt']['capture']['status'] == 'unsupported'
        assert result['resource_limits']['worker_termination']['child_reaped'] is True


def test_package_supervisor_kills_owned_descendant_pipe_without_promoting_timeout(tmp_path, monkeypatch):
    from pythonstan_inspector.project_runner import run_project
    code = r'''
import json, os, pathlib, subprocess, sys, time
payload = json.load(sys.stdin)
marker = pathlib.Path(payload['incident']) / 'descendant.pid'
child = ('import os,pathlib,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
         'pathlib.Path(' + repr(str(marker)) + ').write_text(str(os.getpid())); time.sleep(30)')
subprocess.Popen([sys.executable, '-I', '-S', '-c', child])
deadline = time.monotonic() + 0.5
while not marker.exists() and time.monotonic() < deadline:
    time.sleep(0.005)
assert marker.exists()
response = {'run_id': payload['run_id'], 'request_sha256': payload['request_sha256']}
print('PROJECT_INSPECTOR_JSON:' + json.dumps(response), flush=True)
os._exit(0)
'''
    original_popen = subprocess.Popen
    def injected(command, **kwargs):
        return original_popen([sys.executable, '-I', '-S', '-c', code], **kwargs)
    monkeypatch.setattr(project_process.subprocess, 'Popen', injected)
    result = run_project({'project_root': '/trusted-control', 'entry_file': 'unused.py'},
                         tmp_path / 'capture', timeout_seconds=0.35)
    assert result['status'] == 'timeout'
    assert result['bundle'] is None and result['native_report'] is None
    termination = result['resource_limits']['worker_termination']
    assert termination['child_reaped'] is True and termination['child_returncode'] == 0
    assert termination['pipes_drained'] is True and termination['pipes_closed'] is True
    pid = int((Path(result['incident']) / 'descendant.pid').read_text())
    stat = Path('/proc') / str(pid) / 'stat'
    if stat.exists():
        assert stat.read_text().split(') ', 1)[1].split()[0] == 'Z'
