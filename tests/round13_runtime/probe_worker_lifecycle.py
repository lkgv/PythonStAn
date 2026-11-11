"""Bounded trusted CPython lifecycle witnesses; never execute target sources."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time


CHILD = r'''
import atexit, json, os, subprocess, sys, time
mode, progress = sys.argv[1:]
def event(phase):
    with open(progress, 'a') as stream:
        stream.write(json.dumps({'phase': phase, 'monotonic': time.monotonic()}) + '\n')
def delayed_teardown():
    event('atexit_started')
    time.sleep(0.75)
class Owned:
    def __del__(self):
        event('worker_local_release_started')
        time.sleep(0.75)
def worker():
    if mode in ('atexit', 'fast_atexit'):
        atexit.register(delayed_teardown)
    owned = Owned() if mode in ('return_cleanup', 'fast_return_cleanup') else None
    if mode == 'inherited_pipe':
        subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.75)'])
    report = {'status': 'incomplete', 'solver_completed': False, 'call_edges': [],
              'whole_program_soundness_proved': False}
    if mode == 'large':
        report['payload'] = 'x' * (1024 * 1024)
    encoded = json.dumps(report)
    event('json_ready')
    print('PROJECT_JSON:' + encoded, flush=True)
    event('output_completed')
    sys.stderr.flush()
    if mode.startswith('fast_'):
        os._exit(0)
    if mode == 'nonzero':
        os._exit(7)
worker()
'''


def observe(mode, *, deadline=0.35, delay_read=0.0):
    """Measure distinct output, EOF and wait boundaries, then reap the group."""
    with tempfile.TemporaryDirectory(prefix="round13-lifecycle-") as temporary:
        progress = Path(temporary) / "progress.jsonl"
        started = time.monotonic()
        process = subprocess.Popen([sys.executable, "-I", "-S", "-c", CHILD, mode, str(progress)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        selector = selectors.DefaultSelector()
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        output = {process.stdout.fileno(): bytearray(), process.stderr.fileno(): bytearray()}
        stdout_eof = child_exit = None
        before_read = None
        if delay_read:
            time.sleep(delay_read)
            before_read = progress.read_text().splitlines() if progress.exists() else []
        while time.monotonic() - started < deadline:
            if child_exit is None and process.poll() is not None:
                child_exit = time.monotonic() - started
            for key, _ in selector.select(timeout=0.005):
                chunk = os.read(key.fd, 65536)
                if chunk:
                    output[key.fd].extend(chunk)
                else:
                    selector.unregister(key.fileobj)
                    if key.fileobj is process.stdout:
                        stdout_eof = time.monotonic() - started
            if child_exit is not None and not selector.get_map():
                break
        timed_out = bool(selector.get_map()) or child_exit is None
        returncode_before_cleanup = process.poll()
        if timed_out:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=1)
        for key in list(selector.get_map().values()):
            selector.unregister(key.fileobj)
        selector.close()
        raw = bytes(output[process.stdout.fileno()])
        stderr = bytes(output[process.stderr.fileno()])
        process.stdout.close()
        process.stderr.close()
        phases = [json.loads(line) for line in progress.read_text().splitlines()]
        records = [json.loads(line[len(b"PROJECT_JSON:"):]) for line in raw.splitlines()
                   if line.startswith(b"PROJECT_JSON:")]
        return {"mode": mode, "deadline_seconds": deadline, "timed_out": timed_out,
                "child_exit_seconds": child_exit, "stdout_eof_seconds": stdout_eof,
                "returncode_before_cleanup": returncode_before_cleanup,
                "output_bytes": len(raw), "output_sha256": hashlib.sha256(raw).hexdigest(),
                "complete_records": len(records),
                "report_status": records[0]["status"] if records else None,
                "stderr": stderr.decode(errors="replace"),
                "phases": [{**row, "elapsed_seconds": row["monotonic"] - started} for row in phases],
                "phases_before_delayed_read": before_read}


def run_probes():
    rows = [observe(mode) for mode in ("normal", "atexit", "fast_atexit", "return_cleanup",
                                      "fast_return_cleanup", "inherited_pipe", "nonzero")]
    rows.append(observe("large", deadline=1.0, delay_read=0.1))
    by_mode = {row["mode"]: row for row in rows}
    assert not by_mode["normal"]["timed_out"]
    for mode in ("atexit", "return_cleanup"):
        assert by_mode[mode]["timed_out"] and by_mode[mode]["complete_records"] == 1
        assert by_mode[mode]["child_exit_seconds"] is None
    for mode in ("fast_atexit", "fast_return_cleanup"):
        assert not by_mode[mode]["timed_out"] and by_mode[mode]["complete_records"] == 1
    assert by_mode["inherited_pipe"]["timed_out"]
    assert by_mode["inherited_pipe"]["child_exit_seconds"] is not None
    assert by_mode["inherited_pipe"]["stdout_eof_seconds"] is None
    assert by_mode["nonzero"]["returncode_before_cleanup"] == 7
    assert not by_mode["large"]["timed_out"] and by_mode["large"]["complete_records"] == 1
    assert all('output_completed' not in line for line in by_mode["large"]["phases_before_delayed_read"])
    return {"schema": "round13_worker_lifecycle_witnesses_v1", "rows": rows,
            "child_sha256": hashlib.sha256(CHILD.encode()).hexdigest(),
            "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "python": sys.version, "target_source_executed": False,
            "real_project_delay_cause_established": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_probes()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"rows": len(result["rows"]), "controlled_assertions": "passed"}))
