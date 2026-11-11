"""Adversarial lexical-scope and audit-coverage obligations."""
from pathlib import Path

from pythonstan.analysis.security import analyze_security


def scan(tmp_path, source):
    Path(tmp_path, "demo.py").write_text(source)
    return analyze_security(tmp_path)


def trusted(report, symbol):
    return [finding for finding in report["findings"]
            if finding["resolved_symbol"] == symbol
            and finding["trusted_external_api_identity"]]


def test_comprehension_target_shadows_import(tmp_path):
    report = scan(tmp_path, "import subprocess\ndef f(xs):\n    return [subprocess.run() for subprocess in xs]\n")
    assert not trusted(report, "subprocess.run")
    assert any(r["kind"] == "comprehension_control_or_resumption_unproved" for r in report["residuals"])


def test_first_comprehension_iterable_uses_outer_import(tmp_path):
    report = scan(tmp_path, "import subprocess\ndef f():\n    return [subprocess for subprocess in subprocess.run()]\n")
    assert len(trusted(report, "subprocess.run")) == 1


def test_later_target_shadows_earlier_body_and_iterable(tmp_path):
    report = scan(tmp_path, "import subprocess\ndef f():\n    return [x for x in (1,) for subprocess in subprocess.run()]\n")
    assert not trusted(report, "subprocess.run")


def test_dict_and_generator_comprehensions_use_local_scope(tmp_path):
    report = scan(tmp_path, "import subprocess\ndef f(xs):\n    a = {subprocess.run(): 1 for subprocess in xs}\n    return (subprocess.run() for subprocess in xs)\n")
    assert not trusted(report, "subprocess.run")


def test_comprehension_named_expression_invalidates_outer_api(tmp_path):
    report = scan(tmp_path, "import yaml\n[None for i in xs if (yaml := replacement)]\nyaml.load(data, Loader=yaml.SafeLoader)\n")
    assert not trusted(report, "yaml.load")
    assert any(r["kind"] == "comprehension_outer_assignment_unproved" for r in report["residuals"])
    assert not any(p["status"] == "known_safe_loader_binding"
                   for f in report["findings"] for p in f["policy_evidence"])


def test_comprehension_dependency_does_not_capture_shadowed_parameter(tmp_path):
    report = scan(tmp_path, "def f(p, xs):\n    eval([p for p in xs])\n")
    finding = next(f for f in report["findings"] if f["resolved_symbol"] == "builtins.eval")
    assert finding["parameter_dependencies"] == ["xs"]


def test_conditional_definition_is_discovered(tmp_path):
    report = scan(tmp_path, "if True:\n    def f(x):\n        eval(x)\n")
    function = next(f for f in report["functions"] if f["qualified_name"] == "demo.f")
    assert function["definition_context_unproved"] is True
    assert trusted(report, "builtins.eval")[0]["parameter_dependencies"] == ["x"]


def test_handler_and_match_definitions_are_discovered(tmp_path):
    report = scan(tmp_path, "try:\n    pass\nexcept Exception:\n    def f(x):\n        eval(x)\nmatch value:\n    case 1:\n        def g(x):\n            eval(x)\n")
    assert {f["qualified_name"] for f in report["functions"]} == {"demo.f", "demo.g"}
    assert len(trusted(report, "builtins.eval")) == 2


def test_conditional_import_is_not_promoted_to_unconditional_identity(tmp_path):
    report = scan(tmp_path, "if condition:\n    import subprocess\n    def f(x):\n        subprocess.run(x)\n")
    assert any(f["qualified_name"] == "demo.f" for f in report["functions"])
    assert not trusted(report, "subprocess.run")


def test_captured_parameter_has_explicit_residual(tmp_path):
    report = scan(tmp_path, "def outer(p):\n    def inner():\n        eval(p)\n    return inner\n")
    residual = next(r for r in report["residuals"] if r["kind"] == "captured_free_variable_flow_unproved")
    assert residual["function"] == "demo.outer.inner"
    assert residual["names"] == ["p"]
    inner = next(f for f in report["findings"] if f["function"] == "demo.outer.inner")
    assert inner["parameter_dependencies"] == []


def test_inner_parameter_shadows_outer_capture(tmp_path):
    report = scan(tmp_path, "def outer(p):\n    def inner(p):\n        eval(p)\n    return inner\n")
    assert not any(r["kind"] == "captured_free_variable_flow_unproved" for r in report["residuals"])
    assert trusted(report, "builtins.eval")[0]["parameter_dependencies"] == ["p"]


def test_lambda_and_class_body_are_explicit_unproved_regions(tmp_path):
    report = scan(tmp_path, "f = lambda p: eval(p)\nclass C:\n    eval(value)\n")
    assert {r["kind"] for r in report["residuals"]} >= {"lambda_body_unanalyzed", "class_body_execution_unproved"}
    assert report["security_complete"] is False
