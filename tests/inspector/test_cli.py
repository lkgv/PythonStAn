"""Thin CLI commands exercise the same core on a real captured bundle."""
import json
import subprocess
import sys
from pathlib import Path


def test_seven_cli_commands_real_bundle(tmp_path):
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "subject.py"
    source.write_text("source=1\ntarget=source\n")
    bundle = tmp_path / "bundle"
    def call(*args):
        result = subprocess.run([sys.executable, "-m", "pythonstan_inspector", *args],
            cwd=root, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    assert call("capture", str(source), str(bundle), "--trace")["bundle"] == str(bundle)
    assert call("summary", str(bundle))["snapshot_ref"]
    shown = call("show", str(bundle), "--args", '{"view":"node_points"}')
    assert shown["evidence"]
    row = shown["evidence"][0]
    explained = call("explain", str(bundle), "--args", json.dumps(row))
    assert explained["requested_claim"] == ""
    assert call("events", str(bundle))["evidence"]
    checked = call("check", str(bundle), "--claim", "registered_normal_pfg_closure_v1")
    assert checked["check_status"] == "passed"
    assert call("profile", str(bundle))["query"] == "profile"
    assert call("diff", str(bundle), "--other", str(bundle))["query"] == "diff"


def test_cli_rejects_malformed_parameters(tmp_path):
    result = subprocess.run([sys.executable, "-m", "pythonstan_inspector", "show", str(tmp_path),
        "--args", "[]"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert json.loads(result.stderr)["status"] == "error"


def test_cli_rejects_unencodable_query_text_without_traceback(tmp_path):
    from pythonstan_inspector.runner import run_source
    from pythonstan_inspector.storage import write_bundle
    source = tmp_path / "subject.py"
    source.write_text("x=1\n")
    snapshot, _, _ = run_source(source)
    bundle = write_bundle(snapshot, tmp_path / "bundle")
    result = subprocess.run([sys.executable, "-m", "pythonstan_inspector", "show", str(bundle),
        "--args", '{"label":"\\ud800"}'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert json.loads(result.stderr)["status"] == "error"


def test_capture_project_cli_preserves_surrogate_native_report_as_json(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("pass\n")
    config = {"project_root": str(tmp_path), "entry_file": "app.py", "source_roots": ["app.py"],
              "auto_framework_entries": False, "external_frameworks": [["declared", "\ud800"]]}
    configuration = tmp_path / "project.json"
    configuration.write_text(json.dumps(config))
    result = subprocess.run([sys.executable, "-m", "pythonstan_inspector", "capture-project",
        str(configuration), str(tmp_path / "captures"), "--timeout", "15"],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["native_report"]["effective_config"]["external_frameworks"] == [["declared", "\ud800"]]
    assert payload["capture_state"]["status"] == "refused"
    assert payload["bundle"] is None
    assert "Traceback" not in result.stderr


def test_capture_project_cli_bounds_file_before_json_decode(tmp_path):
    configuration = tmp_path / "oversized.json"
    configuration.write_bytes(b"{" + b" " * (1024 * 1024) + b"}")
    result = subprocess.run([sys.executable, "-m", "pythonstan_inspector", "capture-project",
        str(configuration), str(tmp_path / "captures")], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert json.loads(result.stderr)["error"] == "project configuration file exceeds 1 MiB"
    assert not (tmp_path / "captures").exists()


def test_capture_project_cli_invalid_config_encoding_has_error_envelope(tmp_path):
    configuration = tmp_path / "invalid.json"
    configuration.write_bytes(b"\xff")
    result = subprocess.run([sys.executable, "-m", "pythonstan_inspector", "capture-project",
        str(configuration), str(tmp_path / "captures")], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert json.loads(result.stderr)["status"] == "error"
    assert "Traceback" not in result.stderr


def test_capture_project_cli_deep_json_has_typed_error_before_worker(tmp_path):
    configuration = tmp_path / "deep.json"
    configuration.write_text('{"deep":' + "[" * 20000 + "0" + "]" * 20000 + "}")
    result = subprocess.run([sys.executable, "-m", "pythonstan_inspector", "capture-project",
        str(configuration), str(tmp_path / "captures")], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert json.loads(result.stderr)["status"] == "error"
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "captures").exists()
