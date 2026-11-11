import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_public_cli_preserves_finite_branch_return_across_cleanup():
    completed = subprocess.run([
        sys.executable, str(ROOT / "scripts/do_generator_protocol.py"),
        str(ROOT / "benchmarks/generator_protocol/cleanup_continuation.py"),
        "--function", "wrapper", "--domains", '{"flag":[true,false]}',
        "--operations", '[{"op":"next"},{"op":"send","value":"accepted"},{"op":"next"}]',
    ], capture_output=True, text=True, check=True)
    report = json.loads(completed.stdout)
    assert report["status"] == "complete"
    assert report["general_python_soundness_proved"] is False
    assert report["trace_count"] == 2
    assert [trace["steps"][-1]["outcomes"][0]["value"] for trace in report["traces"]] == ["accepted", "rejected"]
    assert all(trace["steps"][1]["outcomes"][0]["value"] == "cleanup" for trace in report["traces"])


def test_cli_invalid_operation_is_input_error_not_partial_complete():
    completed = subprocess.run([
        sys.executable, str(ROOT / "scripts/do_generator_protocol.py"),
        str(ROOT / "benchmarks/generator_protocol/cleanup_continuation.py"),
        "--function", "wrapper", "--arguments", '{"flag":true}',
        "--operations", '[{"op":"next","value":1}]',
    ], capture_output=True, text=True)
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["status"] == "input_error"
