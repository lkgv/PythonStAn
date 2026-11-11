"""Argument witnesses follow represented exports, never a name-selected body."""
import ast
import json

import pytest

from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.object import (
    AbstractObject, AllocKind, AllocSite, BuiltinFunctionObject, ClassObject,
    FunctionObject, InstanceObject, MethodObject, ModuleObject,
)
from pythonstan.analysis.project.entry_contracts import derive_exported_function_templates
from pythonstan.ir import IRClass, IRFunc, IRModule
from pythonstan.world import World
from tests.pointer.kcfa.test_ai_bridge import analyze_source


def objects(source="def selected(old, /): pass\n", name="subject.selected"):
    context = CallStringContext((), 1)
    module = IRModule("subject", ast.parse(source), "subject", "subject.py")
    module_object = ModuleObject(context, AllocSite(module, AllocKind.MODULE), module)
    scope = Scope.new(module_object, None, context, module, None)
    node = ast.parse(source).body[0]
    function = IRFunc(name, node)
    return FunctionObject(context, AllocSite(function, AllocKind.FUNCTION), scope, function), scope, context


def test_all_fixed_parameter_kinds_preserve_order_and_variadic_boundary():
    function, _, _ = objects("def selected(a, /, b, c=1, *rest, d, e=2, **extra): pass\n")
    before = ast.dump(function.ir.get_ast(), include_attributes=True)
    plan = derive_exported_function_templates([function])
    template, = plan.templates
    assert template.positional_only == ("a",)
    assert template.positional_or_keyword == ("b", "c")
    assert template.keyword_only == ("d", "e")
    assert (template.vararg, template.kwarg) == ("rest", "extra")
    assert template.functions == (function,)
    assert template.origin_node is function.ir.get_ast()
    assert plan.uncovered == ()
    assert ast.dump(function.ir.get_ast(), include_attributes=True) == before
    summary = plan.to_dict()
    assert summary["entry_argument_coverage_proved"] is False
    assert summary["runtime_code_metadata_stability_proved"] is False
    assert summary["templates"][0]["variadic_coverage_proved"] is False
    assert summary["templates"][0]["default_metadata_coverage_proved"] is False


def test_shape_coalescing_retains_all_actual_export_values_and_origins():
    first, _, _ = objects("def first(a, /, *, mode): pass\n", "subject.first")
    second, _, _ = objects("async def second(a, /, *, mode): pass\n", "subject.second")
    forward = derive_exported_function_templates([second, first])
    reverse = derive_exported_function_templates([first, second])
    assert forward.to_dict() == reverse.to_dict()
    template, = forward.templates
    assert set(template.functions) == {first, second}
    assert [origin.qualified_name for origin in template.origins] == ["subject.first", "subject.second"]
    assert [origin.is_async for origin in template.origins] == [False, True]
    assert forward.to_dict()["async_resume_coverage_proved"] is False


def test_same_qualified_name_distinct_shapes_do_not_alias():
    old, _, _ = objects("def selected(old, /): pass\n")
    new, _, _ = objects("def selected(*, fresh): pass\n")
    plan = derive_exported_function_templates([new, old])
    assert len(plan.templates) == 2
    by_shape = {(t.positional_only, t.keyword_only): t for t in plan.templates}
    assert by_shape[(("old",), ())].functions == (old,)
    assert by_shape[((), ("fresh",))].functions == (new,)
    assert plan.to_dict() == derive_exported_function_templates([old, new]).to_dict()


def test_unsupported_callable_categories_are_explicit_uncovered_values():
    function, scope, context = objects()
    ir_class = IRClass("subject.C", ast.parse("class C: pass\n").body[0])
    cls = ClassObject(context, AllocSite(ir_class, AllocKind.CLASS), scope, ir_class)
    instance = InstanceObject(context, AllocSite("instance", AllocKind.INSTANCE), cls)
    method = MethodObject(function.context, function.alloc_site, scope, function.ir, cls, instance)
    builtin = BuiltinFunctionObject(context, AllocSite("len", AllocKind.BUILTIN), "len")
    unknown = AbstractObject(context, AllocSite("opaque", AllocKind.UNKNOWN))
    plan = derive_exported_function_templates([method, cls, instance, builtin, unknown, function])
    assert len(plan.templates) == 1 and plan.templates[0].functions == (function,)
    assert {item.object_type for item in plan.uncovered} == {
        "MethodObject", "ClassObject", "InstanceObject", "BuiltinFunctionObject", "AbstractObject"}
    assert sum(item.count for item in plan.uncovered) == 5
    assert plan.represented_values == 6
    json.dumps(plan.to_dict())  # No raw object/AST values leak into reporting.


@pytest.mark.parametrize("mutation", ["not_function", "duplicate_argument", "bad_identifier", "not_arguments"])
def test_malformed_argument_metadata_fails_closed(mutation):
    function, _, _ = objects()
    node = function.ir.get_ast()
    if mutation == "not_function":
        function.ir.stmt = ast.parse("value = 1\n").body[0]
    elif mutation == "duplicate_argument":
        node.args.args.append(ast.arg(arg="old"))
    elif mutation == "bad_identifier":
        node.args.posonlyargs[0].arg = "bad-name"
    elif mutation == "not_arguments":
        node.args = None
    plan = derive_exported_function_templates([function])
    assert plan.templates == ()
    assert plan.uncovered[0].reason == "unrecognized_source_argument_shape"


def test_empty_export_never_falls_back_to_a_source_declaration():
    plan = derive_exported_function_templates([])
    assert plan.templates == plan.uncovered == ()
    assert plan.to_dict()["empty_binding_observed"] is True
    assert plan.to_dict()["exhaustive_export_binding_proved"] is False


@pytest.mark.parametrize("intrinsics", [False, True])
@pytest.mark.parametrize("index_sensitive", [False, True])
@pytest.mark.parametrize("context_policy", ["0-cfa", "1-cfa", "2-cfa"])
def test_actual_decorator_export_and_saved_alias_have_their_own_signature(
        tmp_path, intrinsics, index_sensitive, context_policy):
    source = '''
def selected(old_source_name, /): return "old"
saved = selected
def replacement(*, actual_keyword): return actual_keyword
def replace(original): return replacement
@replace
def selected(unrelated_source_name): return "not-exported"
'''
    query, _ = analyze_source(tmp_path, source, enable_ai_intrinsics=intrinsics,
                              index_sensitive=index_sensitive, context_policy=context_policy,
                              enable_synthetic_method_contexts=False)
    assert query.get_statistics()["converged"]
    module = World().get_entry_module()
    saved = derive_exported_function_templates(query.points_to_at("saved", module))
    exported = derive_exported_function_templates(query.points_to_at("selected", module))
    # Flow-insensitive exports can retain the earlier definition. Every shape
    # must still correspond to a represented function, rather than a random
    # equal-name source declaration chosen for this external entry.
    assert any(template.positional_only == ("old_source_name",) for template in saved.templates)
    assert any(template.keyword_only == ("actual_keyword",) for template in exported.templates)
    assert all("unrelated_source_name" not in (*template.positional_only,
               *template.positional_or_keyword, *template.keyword_only) for template in exported.templates)
    assert all(function in query.points_to_at("selected", module)
               for template in exported.templates for function in template.functions)


def test_returned_wrappers_with_incompatible_shapes_stay_correlated(tmp_path):
    query, _ = analyze_source(tmp_path, '''
def choose(flag):
    if flag:
        def wrapper(left, /): return left
        return wrapper
    def wrapper(*, right): return right
    return wrapper
selected = choose(True)
''', enable_synthetic_method_contexts=False)
    plan = derive_exported_function_templates(query.points_to_at("selected", World().get_entry_module()))
    assert len(plan.templates) == 2
    assert {(t.positional_only, t.keyword_only) for t in plan.templates} == {(("left",), ()), ((), ("right",))}
    for template in plan.templates:
        for function in template.functions:
            node = function.ir.get_ast()
            assert tuple(a.arg for a in node.args.posonlyargs) == template.positional_only
            assert tuple(a.arg for a in node.args.kwonlyargs) == template.keyword_only
