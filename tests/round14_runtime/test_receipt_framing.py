"""Actual supervisor receipt framing, independently of target analysis."""
import json
import subprocess
import sys

import pytest

from scripts import analyze_project as cli


@pytest.mark.parametrize(('ending', 'returncode', 'expected'), [
    ('\n', 0, 'incomplete'), ('', 0, 'worker_error'), ('\n', 7, 'worker_error'),
])
def test_complete_json_requires_final_newline_and_zero_exit(monkeypatch, ending, returncode, expected):
    record = 'PROJECT_JSON:' + json.dumps({'status': 'incomplete', 'solver_completed': False,
        'call_edges': [], 'whole_program_soundness_proved': False}) + ending
    code = 'import os,sys;sys.stdout.write(' + repr(record) + ');sys.stdout.flush();os._exit(' + str(returncode) + ')'
    original = subprocess.Popen
    def controlled(command, **kwargs):
        return original([sys.executable, '-I', '-S', '-c', code], **kwargs)
    monkeypatch.setattr(cli.subprocess, 'Popen', controlled)
    result = cli.run_isolated({'project_root': '/trusted', 'entry_file': 'unused.py'}, timeout=2)
    assert result['status'] == expected
    assert result['solver_completed'] is False and result['call_edges'] == []
    assert result['whole_program_soundness_proved'] is False


def test_unterminated_last_marker_never_falls_back_to_earlier_complete_json(monkeypatch):
    report = json.dumps({'status': 'incomplete', 'solver_completed': False, 'call_edges': []})
    output = 'PROJECT_JSON:' + report + '\nPROJECT_JSON:' + report
    code = 'import os,sys;sys.stdout.write(' + repr(output) + ');sys.stdout.flush();os._exit(0)'
    original = subprocess.Popen
    monkeypatch.setattr(cli.subprocess, 'Popen', lambda command, **kwargs:
                        original([sys.executable, '-I', '-S', '-c', code], **kwargs))
    result = cli.run_isolated({'project_root': '/trusted', 'entry_file': 'unused.py'}, timeout=2)
    assert result['status'] == 'worker_error'
    assert result['solver_completed'] is False and result['call_edges'] == []
    assert result['resource_limits']['worker_transport_protocol'] == 'project_json_line_stream_v2'
    assert result['resource_limits']['worker_terminal_clock_version'] == 2
