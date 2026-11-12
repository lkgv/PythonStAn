"""Independent builtin oracle over the actual finite string dispatch service."""

from __future__ import annotations

from collections.abc import Mapping
from itertools import product

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.json_data import JsonScalar, require_object
from pythonstan.analysis.semantics.scalar_strings import (
    UnsupportedStringMethod,
    apply_string_method,
)

NAMES = ("upper", "lower", "startswith", "endswith", "strip", "split", "join", "format", "replace")
ARGUMENTS: tuple[tuple[JsonScalar, ...], ...] = (
    (),
    ("a",),
    (None,),
    (0,),
    (True,),
    (1.5,),
    ("a", "b"),
    ("a", 0),
    (None, 1),
    ("a", "b", 1),
    ("a", None, None),
    ("a", 0, -1),
    ("a", "b", "c", "d"),
)
KEYWORDS: tuple[dict[str, JsonScalar], ...] = (
    {},
    {"sep": " "},
    {"maxsplit": 1},
    {"sep": None, "maxsplit": False},
    {"x": "KW"},
    {"count": 1},
    {"unknown": 2},
)


@pytest.mark.parametrize(
    ("receiver", "name", "args", "keywords"),
    product(("", "a b a", "{0}:{x}", "{}"), NAMES, ARGUMENTS, KEYWORDS),
)
def test_finite_string_port_matches_builtin_results_and_error_classes(
    receiver: str,
    name: str,
    args: tuple[JsonScalar, ...],
    keywords: Mapping[str, JsonScalar],
) -> None:
    try:
        # The oracle invokes a trusted host builtin, never a target callable.
        expected: object = getattr(str, name)(receiver, *args, **keywords)
    except (TypeError, ValueError, IndexError, KeyError, AttributeError) as error:
        with pytest.raises(type(error)):
            apply_string_method(receiver, name, args, keywords)
    else:
        actual = apply_string_method(receiver, name, args, keywords)
        assert not isinstance(actual, UnsupportedStringMethod)
        assert type(actual) is type(expected) and actual == expected


def test_unexposed_name_returns_an_explicit_unsupported_result() -> None:
    assert apply_string_method("x", "unmodeled", (1,), {"bad": None}) is (
        UnsupportedStringMethod.UNSUPPORTED
    )


@pytest.mark.parametrize(
    ("expression", "expected"),
    (
        ("'aBc'.upper()", "ABC"),
        ("'aBc'.lower()", "abc"),
        ("'abc'.startswith('a',None,None)", True),
        ("'abc'.endswith('z')", False),
        ("' x '.strip()", "x"),
        ("'a,b'.split(',')[1]", "b"),
        ("'-'.join('abc')", "a-b-c"),
        ("'{0}:{x}'.format('a',x=2)", "a:2"),
        ("'aaaa'.replace('a','b',2)", "bbaa"),
    ),
)
def test_real_source_string_method_consumer(expression: str, expected: JsonScalar) -> None:
    query = ValueQuery("subject", "result")
    result = analyze(
        Source(f"result={expression}\n", module="subject"),
        config={"engine": "ai_primary"},
        queries=[query],
    )
    answer = result.query(query)
    assert answer.status == "answered" and result.coverage_status == "closed_under_profile"
    value = require_object(answer.value)
    assert value["constants"] == [expected]
    assert not value["top"] and not value["top_sorts"] and not value["may_unbound"]
