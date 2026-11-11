import hashlib
from io import StringIO
import json

import pytest

from scripts import analyze_project as cli


class RecordingStream(StringIO):
    def __init__(self, short=None):
        super().__init__()
        self.attempts = []
        self.flushes = 0
        self.short = short
    def write(self, text):
        self.attempts.append(len(text))
        if self.short is not None:
            text = text[:self.short]
        return super().write(text)
    def flush(self):
        self.flushes += 1
        super().flush()


@pytest.mark.parametrize('chunk_chars', [1, 7, 65536])
def test_stream_is_exact_default_json_with_payload_only_metrics(chunk_chars):
    report = {'status': 'incomplete', 'solver_completed': False, 'call_edges': [['a', 'b']],
              'statistics': {'iterations': 50000, 'pending_work': 9},
              'unicode': '应用\ud800\n', 'values': (True, None, -0.0, float('nan'), float('inf'))}
    stream = RecordingStream()
    metrics = cli._write_project_json(report, stream, chunk_chars=chunk_chars)
    expected = json.dumps(report)
    assert stream.getvalue() == 'PROJECT_JSON:' + expected + '\n'
    assert metrics['payload_bytes'] == len(expected.encode('ascii'))
    assert metrics['payload_sha256'] == hashlib.sha256(expected.encode('ascii')).hexdigest()
    assert max(stream.attempts) <= chunk_chars
    assert stream.flushes == 1


def test_short_writes_are_completed_without_losing_or_duplicating_bytes():
    report = {'value': list(range(31))}
    stream = RecordingStream(short=3)
    metrics = cli._write_project_json(report, stream, chunk_chars=17)
    assert stream.getvalue() == 'PROJECT_JSON:' + json.dumps(report) + '\n'
    assert metrics['max_write_characters'] <= 17


@pytest.mark.parametrize('returned', [None, 0, -1, True, 999999])
def test_invalid_or_stalled_write_never_reports_completion(returned):
    class Invalid:
        def write(self, text):
            return returned
        def flush(self):
            raise AssertionError('failed writes must not reach flush')
    with pytest.raises(OSError, match='invalid write count'):
        cli._write_project_json({'x': 1}, Invalid())


def test_flush_failure_is_not_a_completed_receipt():
    class Failing(RecordingStream):
        def flush(self):
            raise OSError('controlled terminal flush failure')
    stream = Failing()
    with pytest.raises(OSError, match='terminal flush'):
        cli._write_project_json({'x': 1}, stream)
    assert stream.getvalue().endswith('\n')  # Visible bytes alone are insufficient.


def test_encoding_error_propagates_after_partial_prefix():
    stream = RecordingStream()
    with pytest.raises(TypeError):
        cli._write_project_json({'unsupported': object()}, stream)
    assert stream.getvalue().startswith('PROJECT_JSON:')
    assert not stream.getvalue().endswith('\n') and stream.flushes == 0


def test_encoder_memory_error_propagates_without_terminal_success(monkeypatch):
    class Exhausted:
        def iterencode(self, value):
            yield '{"x":'
            raise MemoryError('controlled encoder exhaustion')
    monkeypatch.setattr(cli.json, 'JSONEncoder', Exhausted)
    stream = RecordingStream()
    with pytest.raises(MemoryError):
        cli._write_project_json({}, stream)
    assert not stream.getvalue().endswith('\n') and stream.flushes == 0


def test_giant_encoded_leaf_is_explicitly_distinct_from_write_bound():
    stream = RecordingStream()
    metrics = cli._write_project_json({'leaf': '汉' * 5000}, stream, chunk_chars=32)
    assert metrics['max_encoder_fragment_characters'] > 32
    assert metrics['max_write_characters'] <= 32


@pytest.mark.parametrize('invalid', [0, -1, True, None, 65537])
def test_invalid_internal_chunk_limits_are_rejected_before_output(invalid):
    stream = RecordingStream()
    with pytest.raises(ValueError):
        cli._write_project_json({}, stream, chunk_chars=invalid)
    assert stream.getvalue() == ''
