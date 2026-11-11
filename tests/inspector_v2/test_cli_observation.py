"""CLI carries closed observation choices into the one production runner."""
import json

import pytest

from pythonstan_inspector.cli import main


def test_targeted_cli_preserves_selector_views_and_report_limit(tmp_path, monkeypatch, capsys):
    from pythonstan_inspector import project_runner
    configuration = tmp_path / "project.json"
    configuration.write_text('{"project_root":"declared"}')
    observed = {}

    def run(configuration, output, **options):
        observed.update(configuration=configuration, output=output, options=options)
        return {"status": "controlled_receipt"}

    monkeypatch.setattr(project_runner, "run_project", run)
    selector = {"file": "app.py", "line": 3, "model": "keyword_collector"}
    assert main(["capture-project", str(configuration), str(tmp_path / "run"),
                 "--observation-mode", "targeted", "--trace-selector", json.dumps(selector),
                 "--views", "ctx_points,kwargs_model", "--report-mode", "reference",
                 "--max-report-bytes", "65536"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "controlled_receipt"
    options = observed["options"]
    assert options["trace_selector"] == selector
    assert options["observation_mode"] == "targeted"
    assert options["request"].views == ("ctx_points", "kwargs_model")
    assert options["report_mode"] == "reference" and options["max_report_bytes"] == 65536


@pytest.mark.parametrize("value", ["[", " " * 8193])
def test_cli_refuses_invalid_selector_before_worker(tmp_path, monkeypatch, capsys, value):
    from pythonstan_inspector import project_runner
    configuration = tmp_path / "project.json"
    configuration.write_text("{}")

    def forbidden(*args, **kwargs):
        pytest.fail("worker must not start for malformed selector")

    monkeypatch.setattr(project_runner, "run_project", forbidden)
    assert main(["capture-project", str(configuration), str(tmp_path / "run"),
                 "--observation-mode", "targeted", "--trace-selector", value]) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "error"


def test_capture_cli_selects_model_view(tmp_path, monkeypatch, capsys):
    from pythonstan_inspector import runner
    observed = {}

    class Reference:
        __dict__ = {"run_id": "controlled", "snapshot_id": "controlled"}

    class Snapshot:
        ref = Reference()

    def source(path, **options):
        observed.update(options)
        return Snapshot(), None, {}

    monkeypatch.setattr(runner, "run_source", source)
    monkeypatch.setattr("pythonstan_inspector.cli.write_bundle", lambda *a, **kw: tmp_path)
    assert main(["capture", "app.py", str(tmp_path), "--views", "ctx_points,kwargs_model"]) == 0
    assert observed["request"].views == ("ctx_points", "kwargs_model")
    assert json.loads(capsys.readouterr().out)["bundle"] == str(tmp_path)
