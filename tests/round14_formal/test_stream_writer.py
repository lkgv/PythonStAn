"""Independent encoder oracle and stream-failure controls; no project workers."""
import hashlib
import json
import math

import pytest

from scripts import analyze_project


class RecordingStream:
    def __init__(self, *, short=False):
        self.parts = []
        self.requests = []
        self.flushed = False
        self.short = short

    def write(self, text):
        self.requests.append(text)
        consumed = min(len(text), 2) if self.short else len(text)
        self.parts.append(text[:consumed])
        return consumed

    def flush(self):
        self.flushed = True


@pytest.mark.parametrize("chunk", [1, 17, 65536])
@pytest.mark.parametrize("short", [False, True])
def test_ascii_default_payload_bytes_hash_and_bounded_writes_match_independent_encoder(chunk, short):
    report = {"status": "incomplete", "text": "\ud800\u00e9\U0001f600\"\\\n" * 100,
              "many": ["leaf", {"nested": 7}] * 20,
              "nonfinite": [float("nan"), float("inf"), float("-inf")]}
    expected = json.dumps(report).encode("ascii")
    stream = RecordingStream(short=short)
    receipt = analyze_project._write_project_json(report, stream, chunk_chars=chunk)
    actual = "".join(stream.parts)
    assert actual == "PROJECT_JSON:" + expected.decode("ascii") + "\n"
    assert stream.flushed
    assert max(map(len, stream.requests)) <= chunk
    assert receipt["max_write_characters"] == max(map(len, stream.requests))
    assert receipt["payload_bytes"] == len(expected)
    assert receipt["payload_sha256"] == hashlib.sha256(expected).hexdigest()
    assert receipt["max_encoder_fragment_characters"] > 1000
    decoded = json.loads(actual[len("PROJECT_JSON:"):-1])
    assert decoded["status"] == "incomplete"
    assert decoded["text"] == report["text"]
    assert math.isnan(decoded["nonfinite"][0])
    assert decoded["nonfinite"][1:] == [float("inf"), float("-inf")]


@pytest.mark.parametrize("invalid", [0, None, True, -1, "oversized"])
def test_invalid_stream_counts_cannot_return_successful_receipt(invalid):
    stream = RecordingStream()

    def bad_write(text):
        return len(text) + 1 if invalid == "oversized" else invalid

    stream.write = bad_write
    with pytest.raises(OSError):
        analyze_project._write_project_json({"status": "incomplete"}, stream, chunk_chars=8)
    assert stream.flushed is False


@pytest.mark.parametrize("phase", ["write-oserror", "write-memoryerror", "flush-oserror", "encode-memoryerror"])
def test_encoding_write_and_flush_failures_propagate_without_a_receipt(monkeypatch, phase):
    stream = RecordingStream()
    if phase.startswith("write"):
        def write_failure(_text):
            if phase == "write-memoryerror":
                raise MemoryError("controlled pipe allocation failure")
            raise OSError("controlled pipe failure")
        stream.write = write_failure
    elif phase == "flush-oserror":
        def flush_failure():
            raise OSError("controlled flush failure")
        stream.flush = flush_failure
    else:
        def failed_encoder(_self, _value):
            yield '{"status":'
            raise MemoryError("controlled encoder failure")
        monkeypatch.setattr(analyze_project.json.JSONEncoder, "iterencode", failed_encoder)
    error = MemoryError if "memoryerror" in phase else OSError
    with pytest.raises(error):
        analyze_project._write_project_json({"status": "incomplete"}, stream, chunk_chars=8)
    assert stream.flushed is False
    if phase == "encode-memoryerror":
        assert not "".join(stream.parts).endswith("\n")
