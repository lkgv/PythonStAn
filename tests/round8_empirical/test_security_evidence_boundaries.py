"""Independent defensive-audit adversaries, with no target execution."""
from pythonstan.analysis.security import analyze_security


def scan(tmp_path, source, **kwargs):
    (tmp_path / "app.py").write_text(source)
    return analyze_security(tmp_path, ("app.py",), **kwargs)


def resolved(report, rule):
    return [f for f in report["findings"] if f["rule_id"] == rule and f["resolution"] == "source_import_binding"]


def test_module_attribute_replacement_cannot_retain_native_api_identity(tmp_path):
    report = scan(tmp_path, '''import subprocess
subprocess.run = lambda value: None
def caller(command):
    subprocess.run(command)
''')
    assert not resolved(report, "command_execution")


def test_safe_loader_name_replacement_cannot_be_policy_evidence(tmp_path):
    report = scan(tmp_path, '''import yaml
yaml.SafeLoader = object
def caller(content):
    return yaml.load(content, Loader=yaml.SafeLoader)
''')
    assert not any(policy["status"] == "known_safe_loader_binding"
                   for f in report["findings"] for policy in f["policy_evidence"])


def test_except_alias_hides_outer_import_throughout_function(tmp_path):
    report = scan(tmp_path, '''import subprocess
def caller(command):
    try:
        raise Exception()
    except Exception as subprocess:
        subprocess.run(command)
''')
    assert not resolved(report, "command_execution")


def test_builtin_shadow_does_not_create_code_execution_inventory(tmp_path):
    report = scan(tmp_path, '''def caller(eval, payload):
    return eval(payload)
''')
    assert not resolved(report, "python_code_execution")


def test_guard_after_call_is_not_sanitizer_or_earlier_guard(tmp_path):
    report = scan(tmp_path, '''import subprocess
def caller(command):
    subprocess.run(command)
    if is_valid(command):
        return True
''')
    finding = resolved(report, "command_execution")[0]
    assert finding["parameter_dependencies"] == ["command"]
    assert finding["enclosing_branch_conditions"] == []
    assert finding["sanitization_proved"] is False
    assert finding["vulnerability_confirmed"] is False


def test_enclosing_guard_and_false_shell_are_only_syntax_evidence(tmp_path):
    report = scan(tmp_path, '''import subprocess
def caller(command):
    if is_valid(command):
        subprocess.run(command, shell=False)
''')
    finding = resolved(report, "command_execution")[0]
    assert finding["enclosing_branch_conditions"]
    assert {p["name"]: p.get("value") for p in finding["policy_evidence"]}["shell"] is False
    assert finding["sanitization_proved"] is finding["vulnerability_confirmed"] is False


def test_argument_overwrite_does_not_retain_false_parameter_flow(tmp_path):
    report = scan(tmp_path, '''import subprocess
def caller(command):
    command = "fixed"
    subprocess.run(command)
''')
    assert resolved(report, "command_execution")[0]["parameter_dependencies"] == []


def test_attribute_name_alone_is_only_unresolved_candidate(tmp_path):
    report = scan(tmp_path, '''def caller(database, statement):
    database.execute(statement)
''')
    finding = report["findings"][0]
    assert finding["resolution"] == "unresolved_receiver_candidate"
    assert finding["resolved_symbol"] is None
    assert finding["vulnerability_confirmed"] is False


def test_partial_solver_never_supports_reachability(tmp_path):
    report = scan(tmp_path, '''import subprocess
def caller(command):
    subprocess.run(command)
''', call_edges=[("app.entry", "app.caller")], entry_points=["app.entry"], solver_completed=False)
    assert report["solver_supported_reachability"] == []


def test_completed_call_projection_is_not_interprocedural_taint(tmp_path):
    report = scan(tmp_path, '''import subprocess
def caller(command):
    subprocess.run(command)
''', call_edges=[("app.entry", "app.caller")], entry_points=["app.entry"], solver_completed=True)
    assert report["solver_supported_reachability"]
    assert report["interprocedural_taint_proved"] is False
    assert report["vulnerabilities_confirmed"] == 0
    assert all(not f["interprocedural_taint_proved"] for f in report["solver_supported_reachability"])


def test_first_party_module_shadow_is_not_a_native_subprocess_api(tmp_path):
    (tmp_path / "subprocess.py").write_text("def run(value):\n    return value\n")
    (tmp_path / "app.py").write_text("import subprocess\ndef caller(command):\n    return subprocess.run(command)\n")
    report = analyze_security(tmp_path, (".",))
    assert not resolved(report, "command_execution")


def test_first_party_yaml_module_does_not_supply_a_trusted_loader(tmp_path):
    (tmp_path / "yaml.py").write_text("SafeLoader=object\ndef load(value, Loader=None):\n    return value\n")
    (tmp_path / "app.py").write_text("import yaml\ndef caller(content):\n    return yaml.load(content,Loader=yaml.SafeLoader)\n")
    report = analyze_security(tmp_path, (".",))
    assert not any(p["status"] == "known_safe_loader_binding"
                   for f in report["findings"] for p in f["policy_evidence"])
