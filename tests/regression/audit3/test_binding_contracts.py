"""Independent finite binder checks and lexical-scope isolation controls."""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from itertools import product

import pytest

from pythonstan.analysis.contracts.arguments import CallArguments
from pythonstan.analysis.contracts.json_data import validate_json
from pythonstan.analysis.semantics.binding import bind_arguments
from pythonstan.analysis.semantics.names import BindingClassification, classify_bindings


def plain(a: int, b: int = 2) -> None:
    pass


def mixed(a: int, /, b: int = 2, *args: int, c: int, d: int = 3, **kwargs: int) -> None:
    pass


def keyword_only(*, c: int, d: int = 3) -> None:
    pass


@dataclass(frozen=True)
class SignatureCase:
    source: str
    signature: inspect.Signature
    defaults: tuple[int, ...]
    kw_defaults: tuple[tuple[str, int], ...]


SIGNATURES = (
    SignatureCase("def f(a,b=2):pass", inspect.signature(plain), (2,), ()),
    SignatureCase(
        "def f(a,/,b=2,*args,c,d=3,**kwargs):pass", inspect.signature(mixed), (2,), (("d", 3),)
    ),
    SignatureCase("def f(*,c,d=3):pass", inspect.signature(keyword_only), (), (("d", 3),)),
)
KEYWORD_NAMES = (
    (),
    ("a",),
    ("b",),
    ("c",),
    ("d",),
    ("extra",),
    ("a", "c"),
    ("b", "c"),
    ("b", "c", "d"),
    ("c", "extra"),
    ("c", "d", "extra"),
)


@pytest.mark.parametrize(
    ("case", "positional_count", "names"), product(SIGNATURES, range(6), KEYWORD_NAMES)
)
def test_complete_binding_matches_independent_python_signature(
    case: SignatureCase, positional_count: int, names: tuple[str, ...]
) -> None:
    function = ast.parse(case.source).body[0]
    assert isinstance(function, ast.FunctionDef)
    positional = tuple(range(positional_count))
    keywords = tuple((name, index + 10) for index, name in enumerate(names))
    result = bind_arguments(
        function.args, CallArguments(positional, keywords), case.defaults, case.kw_defaults
    )
    try:
        expected = case.signature.bind(*positional, **dict(keywords))
    except TypeError:
        assert result.errors
        return
    assert not result.errors
    expected.apply_defaults()
    bound: dict[str, object] = dict(result.bindings)
    if function.args.vararg is not None:
        bound[function.args.vararg.arg] = result.varargs
    if function.args.kwarg is not None:
        bound[function.args.kwarg.arg] = dict(result.kwargs)
    assert validate_json(bound) == validate_json(expected.arguments)


def test_duplicate_keyword_occurrences_keep_original_error_semantics() -> None:
    function = ast.parse("def f(a,b=2):pass").body[0]
    assert isinstance(function, ast.FunctionDef)
    arguments = CallArguments((1,), (("b", 3), ("b", 4)))
    assert arguments.keywords == (("b", 3), ("b", 4))
    result = bind_arguments(function.args, arguments, (2,))
    assert result.errors == ("duplicate_keyword:b", "multiple_values:b")


def test_named_scope_record_preserves_outer_and_nested_scope_boundaries() -> None:
    tree = ast.parse("""def outer():
    retained = 0
    def target(a, /, b=2, *args, c=3, **kwargs):
        global published
        nonlocal retained
        import package as alias
        value = a
        def child():
            child_local = 1
        class Inner:
            class_local = 2
        try:
            yield from args
        except Error as problem:
            pass
    return target
""")
    outer = tree.body[0]
    assert isinstance(outer, ast.FunctionDef)
    target = outer.body[1]
    assert isinstance(target, ast.FunctionDef)
    result = classify_bindings(target)
    assert isinstance(result, BindingClassification)
    assert result.locals == {
        "a",
        "b",
        "args",
        "c",
        "kwargs",
        "alias",
        "value",
        "child",
        "Inner",
        "problem",
    }
    assert result.globals == {"published"}
    assert result.nonlocals == {"retained"}
    assert result.has_yield
    assert "child_local" not in result.locals and "class_local" not in result.locals


def test_nested_generator_and_lambda_parameters_keep_their_own_scopes() -> None:
    function = ast.parse(
        "def parent(x):\n    def child(): yield 1\n    thunk=lambda private: private\n"
    ).body[0]
    assert isinstance(function, ast.FunctionDef)
    result = classify_bindings(function)
    assert result.locals == {"x", "child", "thunk"} and not result.has_yield
    expression = ast.parse("lambda a,*args,**kwargs: a", mode="eval").body
    assert isinstance(expression, ast.Lambda)
    assert classify_bindings(expression).locals == {"a", "args", "kwargs"}
