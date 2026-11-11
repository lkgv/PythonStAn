"""Independent project-level export activation contracts.

The audit snapshots actual export objects before activation and inspects the
installed production CallConstraints after solving. No fabricated function
object or name-picked body may fill an unavailable export binding.
"""
import ast

import pytest

from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint
from pythonstan.analysis.pointer.kcfa.object import FunctionObject
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.pointer.kcfa.variable import VariableKind
import pythonstan.analysis.project.analysis as project_analysis


def run(tmp_path, monkeypatch, source, policy):
    (tmp_path / "subject.py").write_text(source)
    captured = []
    exports = []
    original_add = PointerSolver.add_constraint
    def add(self, scope, context, constraint):
        if (isinstance(constraint, CallConstraint)
                and constraint.call_site.scope_name == "<conditional external project entry>"):
            captured.append((self.state, scope, context, constraint))
        return original_add(self, scope, context, constraint)
    monkeypatch.setattr(PointerSolver, "add_constraint", add)
    original_activate = project_analysis._activate_exported_entries
    def activate(driver, modules, available, selected):
        for name in selected:
            for module in modules.values():
                prefix = module.get_qualname() + "."
                if name.startswith(prefix) and "." not in name[len(prefix):]:
                    scope = driver.state.get_scope_for_ir(module)
                    symbol = driver.solver.variable_factory.make_variable(name[len(prefix):], VariableKind.GLOBAL)
                    contextual = driver.state.get_variable(scope, scope.context, symbol)
                    exports.extend(driver.state.get_points_to(contextual))
        return original_activate(driver, modules, available, selected)
    monkeypatch.setattr(project_analysis, "_activate_exported_entries", activate)
    report = project_analysis.analyze_project({
        "project_root": str(tmp_path), "entry_file": "subject.py", "source_roots": ["subject.py"],
        "import_roots": ["."], "entry_points": ["subject.selected"],
        "auto_framework_entries": False, "synthetic_method_contexts": False,
        "work_scheduling_policy": policy, "max_iterations": 50000,
    })
    assert report["solver_completed"], report.get("analysis_error")
    assert report["whole_program_soundness_proved"] is False
    return report, captured, exports


@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_noncallable_decorator_export_remains_uncovered_without_original_body_invocation(
        tmp_path, monkeypatch, policy):
    report, captured, exports = run(tmp_path, monkeypatch, '''def unreachable(): return "bad"
def discard(function): return 0
@discard
def selected(*, source_only): return unreachable()
''', policy)
    assert exports and not any(type(obj) is FunctionObject for obj in exports)
    assert captured == []
    activation, = report["exported_entry_activation"]
    assert activation["status"] == "exported_binding_uncovered"
    assert activation["calls_installed"] == 0
    assert activation["argument_templates"]["uncovered_values"]
    assert ["subject.selected", "subject.unreachable"] not in report["call_edges"]


@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_incompatible_wrapper_shapes_keep_actual_callee_and_argument_correlation(
        tmp_path, monkeypatch, policy):
    report, captured, exports = run(tmp_path, monkeypatch, '''def left_callback(): return "left"
def right_callback(): return "right"
def unreachable(): return "bad"
def choose(original):
    if flag:
        def wrapper(left, /): return left_callback()
        return wrapper
    def wrapper(*, right): return right_callback()
    return wrapper
@choose
def selected(source_only): return unreachable()
''', policy)
    assert len(captured) == 2
    observed_shapes = set()
    for state, scope, context, call in captured:
        callees = set(state.get_points_to(state.get_variable(scope, context, call.callee)))
        assert callees and callees <= set(exports)
        assert all(type(obj) is FunctionObject for obj in callees)
        # A shape-specific callee slot must not contain every alternative from
        # the export snapshot. Parameter shape belongs to those actual values.
        assert len(callees) == 1
        for obj in callees:
            args = obj.ir.get_ast().args
            positional = tuple(arg.arg for arg in args.posonlyargs)
            keyword = tuple(arg.arg for arg in args.kwonlyargs)
            assert len(call.args) == len(positional)
            assert {name for name, _ in call.kwargs} == set(keyword)
            observed_shapes.add((positional, keyword))
    assert observed_shapes == {(("left",), ()), ((), ("right",))}
    activation, = report["exported_entry_activation"]
    assert activation["binding_model"] == "snapshot_of_exported_function_points"
    assert activation["argument_templates"]["entry_argument_coverage_proved"] is False
    assert activation["argument_templates"]["exhaustive_export_binding_proved"] is False
    assert ["subject.selected", "subject.unreachable"] not in report["call_edges"]
    assert ["subject.choose.wrapper", "subject.left_callback"] in report["call_edges"]
    assert ["subject.choose.wrapper", "subject.right_callback"] in report["call_edges"]


@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_variadic_and_default_witnesses_are_explicitly_nonexhaustive(tmp_path, monkeypatch, policy):
    report, captured, exports = run(tmp_path, monkeypatch, '''def marker(): return "marker"
def replace(original):
    def actual(first="default", /, *tail, mode="default", **rest):
        return marker()
    return actual
@replace
def selected(unrelated): return "unused"
''', policy)
    assert len(captured) == 1
    state, scope, context, call = captured[0]
    assert set(state.get_points_to(state.get_variable(scope, context, call.callee))) <= set(exports)
    assert len(call.args) == 2  # fixed first plus one explicitly finite vararg witness
    assert {name for name, _ in call.kwargs} == {"mode"}
    activation, = report["exported_entry_activation"]
    template, = activation["argument_templates"]["templates"]
    assert template["variadic_coverage_proved"] is False
    assert template["default_metadata_coverage_proved"] is False
    assert template["async_resume_coverage_proved"] is False
    assert any("continuation_coverage" in row["effects"]
               and "export_binding_mutation" in row["effects"]
               for row in report["residual_effects"])
