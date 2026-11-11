import hashlib
import json
from pathlib import Path

import pytest

from pythonstan.analysis.security import analyze_security, evaluate_tasks


def check(tmp_path, source, **kwargs):
    (tmp_path / "app.py").write_text(source)
    return analyze_security(str(tmp_path), ("app.py",), **kwargs)


def test_intentional_command_is_not_a_vulnerability_verdict(tmp_path):
    result = check(tmp_path, "import subprocess\ndef run(command):\n    subprocess.run(command, shell=True)\n")
    finding, = result["findings"]
    assert finding["resolved_symbol"] == "subprocess.run"
    assert finding["parameter_dependencies"] == ["command"]
    assert finding["policy_evidence"] == [{"name":"shell","status":"literal","expression":"True","value":True}]
    assert result["vulnerabilities_confirmed"] == 0
    assert not result["security_complete"]


def test_branch_dependencies_union_and_literal_policy_separate(tmp_path):
    result = check(tmp_path, "import subprocess\ndef run(a,b,flag):\n    if flag:\n        command=a\n    else:\n        command=b\n    subprocess.run(command, shell=flag)\n")
    finding, = result["findings"]
    assert finding["parameter_dependencies"] == ["a","b","flag"]
    assert finding["policy_evidence"][0]["status"] == "unresolved_expression"
    assert not finding["sanitization_proved"]


def test_safe_loader_binding_is_narrow_evidence(tmp_path):
    result = check(tmp_path, "from yaml import load, SafeLoader\ndef parse(data):\n    return load(data, Loader=SafeLoader)\n")
    finding, = result["findings"]
    assert finding["resolved_symbol"] == "yaml.load"
    assert finding["policy_evidence"][0]["status"] == "known_safe_loader_binding"
    assert not finding["sanitization_proved"]


def test_solver_reachability_does_not_become_taint(tmp_path):
    source="import pickle\ndef entry(x):\n    return helper(x)\ndef helper(y):\n    return pickle.loads(y)\n"
    partial=check(tmp_path,source,call_edges=[("app.entry","app.helper")],entry_points=["app.entry"])
    assert not partial["solver_supported_reachability"]
    complete=check(tmp_path,source,call_edges=[("app.entry","app.helper")],solver_completed=True,entry_points=["app.entry"])
    path, = complete["solver_supported_reachability"]
    assert path["sink_function"]=="app.helper"
    assert path["interprocedural_taint_proved"] is False


def test_source_is_not_executed(tmp_path):
    marker=tmp_path/"created"
    result=check(tmp_path,f"open({str(marker)!r},'w').write('x')\n")
    assert not marker.exists()
    assert result["target_source_executed"] is False


def test_parse_and_budget_failures_keep_denominators(tmp_path):
    (tmp_path/"a.py").write_text("def broken(\n")
    (tmp_path/"b.py").write_text("import pickle\ndef f(x): return pickle.loads(x)\n")
    result=analyze_security(tmp_path,max_files=1)
    assert result["source_files"]==2
    assert result["parsed_files"]==0
    assert result["unmeasured_files"]==2
    assert result["status"]=="incomplete_inventory"


def task_for(source, expected=True):
    return {"id":"manual", "source":"app.py", "source_sha256":hashlib.sha256(source.encode()).hexdigest(),
            "qualname":"app.run", "expected":expected,
            "assertion":{"kind":"resolved_sink","rule_id":"command_execution","symbol":"subprocess.run"}}


def test_task_unknown_is_not_true_negative_or_false_negative(tmp_path):
    source="import subprocess\ndef run(x): subprocess.run(x)\n"
    task=task_for(source)
    report=check(tmp_path,source)
    measured=evaluate_tasks([task],report)
    assert measured["true_property_positives"]==1
    assert measured["vulnerability_precision"] is None
    task["source_sha256"]="0"*64
    unknown=evaluate_tasks([task],report)
    assert unknown["unmeasured"]==1
    assert unknown["false_property_negatives"]==unknown["true_property_negatives"]==0


def test_real_task_gold_frozen_before_predictions():
    folder=Path(__file__).resolve().parents[2]/"benchmark/round8_security"
    raw=(folder/"tasks.json").read_bytes()
    meta=json.loads((folder/"gold_freeze.json").read_text())
    data=json.loads(raw)
    assert hashlib.sha256(raw).hexdigest()==meta["sha256"]
    assert len(data["tasks"])==35
    assert len({task["project_id"] for task in data["tasks"]})==11
    assert sum(not task["expected"] for task in data["tasks"])==10
    assert all(task["adjudicated"] and task["review_note"] for task in data["tasks"])


@pytest.mark.parametrize("option",[{"max_files":True},{"max_files":0},{"max_source_bytes":0},{"solver_completed":1}])
def test_budgets_and_evidence_flags_are_validated(tmp_path,option):
    with pytest.raises(ValueError):analyze_security(tmp_path,**option)
