"""Independent target execution is restricted to these trusted test witnesses.

The production checker never executes its target. CPython here provides a
separate implementation differential, not evidence consumed by the checker.
"""
import hashlib
import json
from pathlib import Path

import pytest

from pythonstan.analysis.pointer.ai.certified.closed_heap_effects import (
    Budget, SEMANTICS_VERSION, analyze_closed_source, check_closed_effects,
)

ROOT = Path(__file__).resolve().parents[2]


def candidate(source, **claims):
    return {"source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "semantics_version": SEMANTICS_VERSION, "globals": claims}


WITNESSES = [
    '''items = [1, 2]
result = ["xyz".startswith(("x", 42)), "xyz".endswith(("z", 42)), items.pop(True)]
''',
    '''class A:
    __dict__ = 42
    __class__ = 43
a = A()
result = [a.__dict__, a.__class__]
''',
    '''class A:
    pass
a = A()
a.value = [1]
copy = dict(a.__dict__)
copy["value"].append(2)
copy["new"] = 3
result = [a.value, hasattr(a, "new"), a.__dict__ is a.__dict__]
''',
    '''d = {}
try:
    d.update([("ok", 1), ("bad",)])
except ValueError:
    result = d
''',
    '''d = {"x": 1}
view = d.values()
first = list(view)
d["x"] = 2
result = [first, list(view), list(view)]
''',
    '''items = [1]
it = iter(items)
a = next(it)
items.append(2)
b = next(it)
c = next(it, "done")
items.append(3)
result = [a, b, c, next(it, "still done")]
''',
    '''result = [range(0, 2) == range(2), range(0) == range(3, 3),
          isinstance(range(2), tuple), isinstance(range(2), range)]
''',
    '''class A:
    def __len__(self):
        return True
result = bool(A())
''',
    '''class A:
    pass
try:
    A()()
except TypeError:
    result = "type"
except AttributeError:
    result = "attribute"
''',
    '''audit = []
def mark(value):
    audit.append(value)
    return value
def decorator(name):
    audit.append("evaluate:" + name)
    def apply(fn):
        audit.append("apply:" + name)
        return fn
    return apply
@decorator("a")
@decorator("b")
def f(x=mark("default")):
    return x
result = [audit, f()]
''',
    '''def f(a, /, b=2, *args, c=3, **kwargs):
    return [a, b, list(args), c, kwargs]
result = f(1, 4, 5, c=6, a=7, extra=8)
''',
    '''def make(x):
    def f():
        return x
    return f
first = make(1)
second = make(2)
result = [first(), second(), first is second]
''',
    '''class Descriptor:
    def __get__(self, instance, owner):
        return owner.__name__
class A:
    field = Descriptor()
class B(A):
    pass
result = [B.field, B().field]
''',
    '''class Descriptor:
    def __get__(self, instance, owner):
        return 10
    def __set__(self, instance, value):
        instance.saved = value
class A:
    field = Descriptor()
a = A()
a.__dict__["field"] = 1
a.field = 2
result = [a.field, a.saved]
''',
    '''class A:
    def f(self):
        return "A"
class B(A):
    pass
class C(A):
    def f(self):
        return "C"
class D(B, C):
    pass
result = D().f()
''',
    '''class A:
    @classmethod
    def f(cls):
        return cls.__name__
    @staticmethod
    def g(x):
        return x
class B(A):
    pass
result = [B.f(), B().f(), B.g(7), B().g(8)]
''',
    '''audit = []
class Manager:
    def __enter__(self):
        audit.append("enter")
        return 1
    def __exit__(self, kind, value, traceback):
        audit.append(kind.__name__)
        return True
with Manager():
    raise ValueError("x")
result = audit
''',
    '''audit = []
class Manager:
    def __enter__(self):
        return None
    def __exit__(self, kind, value, traceback):
        audit.append(kind is None)
def f():
    with Manager():
        return 3
result = [f(), audit]
''',
    '''def f():
    try:
        return 1
    finally:
        return 2
result = f()
''',
    '''def reraiser():
    raise
try:
    try:
        raise ValueError("x")
    finally:
        reraiser()
except ValueError as error:
    result = list(error.args)
''',
    '''audit = []
for x in [1, 2, 3]:
    try:
        if x == 2:
            break
        audit.append(x)
    finally:
        audit.append("finally")
else:
    audit.append("else")
result = audit
''',
    '''x = 1
class A:
    x = 2
    class B:
        y = x
    items = [x for item in [1]]
result = [A.B.y, A.items]
''',
    '''class A:
    def __init__(self):
        self.__value = 3
    def get(self):
        return self.__value
result = A().get()
''',
    '''audit = []
class D:
    def __set_name__(self, owner, name):
        audit.append(owner.__name__ + ":" + name)
class A:
    first = D()
    second = D()
class B(A):
    pass
setattr(B, "late", D())
result = audit
''',
    '''audit = []
class D:
    def __set_name__(self, owner, name):
        audit.append(name)
        owner.late = D()
class A:
    first = D()
    second = D()
result = audit
''',
    '''class D:
    def __set_name__(self, owner, name):
        self.owner = owner
        self.name = name
    def __get__(self, instance, owner):
        return [self.owner.__name__, owner.__name__, self.name]
class A:
    x = D()
class B(A):
    pass
result = B.x
''',
]


@pytest.mark.parametrize("source", WITNESSES)
def test_checked_subset_matches_independent_python_execution(source):
    namespace = {}
    exec(compile(source, "<trusted-differential-witness>", "exec"), namespace)
    report = check_closed_effects(source, candidate(source, result=namespace["result"]))
    assert report["status"] == "conditional_verified", report
    assert report["observations"]["result"] == namespace["result"]
    assert report["runtime_observations_used"] is False
    assert report["full_python_soundness_proved"] is False


@pytest.mark.parametrize("source", [
    'import os\nresult = 1\n',
    'result = input()\n',
    'def g():\n    yield from [1]\nresult = list(g())\n',
    'class A:\n    __slots__ = ()\nresult = 1\n',
    'class A:\n    def __getattribute__(self, name):\n        return 1\nresult = A().x\n',
    'class Meta(type):\n    def mro(cls): return [cls]\nclass C(metaclass=Meta): pass\nresult = 1\n',
    'class A:\n    pass\nclass B(object, A):\n    pass\nresult = 1\n',
    'try:\n    result = [][0]\nexcept IndexError as error:\n    result = list(error.args)\n',
    'result = {"a": 1}.keys() == {"a": 2}.keys()\n',
    'result = ValueError("a") == ValueError("a")\n',
    'result = callable(NotImplemented)\n',
    'result = (1, 2)\n',
    'class D:\n    def __set_name__(self, owner, name):\n        raise ValueError("bad")\ntry:\n    class A:\n        x = D()\nexcept RuntimeError:\n    result = 1\n',
])
def test_unsupported_operations_never_discharge_claims(source):
    result = check_closed_effects(source, candidate(source, result=1))
    assert result["status"] == "unsupported"
    assert result["conditional_effects_verified"] == 0


@pytest.mark.parametrize("source", [
    'result = "x" * 1000000000\n',
    'def callback():\n    return 1\ncallback()\nwhile True:\n    pass\n',
    'def f():\n    return f()\nresult = f()\n',
])
def test_budgets_return_unknown_and_preserve_prefix(source):
    report = check_closed_effects(source, candidate(source, result=1), budget=Budget(max_steps=100, max_call_depth=10))
    assert report["status"] == "unknown"
    assert report["conditional_effects_verified"] == 0
    if "callback()" in source:
        assert report["prefix_calls"] == ["callback"]


@pytest.mark.parametrize("changed", ["source_sha256", "semantics_version", "globals"])
def test_tampering_never_verifies(changed):
    source = "result = [True]\n"
    proposal = candidate(source, result=[True])
    proposal[changed] = {"result": [1]} if changed == "globals" else "tampered"
    assert check_closed_effects(source, proposal)["status"] == "rejected"


def frozen_cases():
    for cohort in ("web_orm", "plugins", "validation"):
        manifest = ROOT / "benchmark/realworld_hardcases" / cohort / "manifest.json"
        for item in json.loads(manifest.read_text())["cases"]:
            yield manifest, item


@pytest.mark.parametrize("manifest,case", list(frozen_cases()), ids=lambda x: x["id"] if isinstance(x, dict) else x.parent.name)
def test_frozen_hardcases_have_exact_separate_conditional_boundary(manifest, case):
    source = (manifest.parent / case["source"]).read_text()
    assert hashlib.sha256(source.encode()).hexdigest() == case["sha256"]
    claims = {effect["runtime_global"]: effect["expected"] for effect in case["effects"]}
    from pythonstan.analysis.pointer.ai.certified.heap_coroutines import entry_binding
    driver = (case["await_global"],) if case.get("await_global") else ()
    proposal = candidate(source, **claims)
    if driver: proposal["entry_sha256"] = entry_binding(driver)[1]
    report = check_closed_effects(source, proposal, await_globals=driver)
    assert report["status"] == "conditional_verified", report
    assert report["conditional_effects_verified"] == len(claims)
    assert report["kcfa_results_certified"] is False
    assert report["full_python_soundness_proved"] is False
    if driver:
        # The async fixture's declared entry is module plus an explicit await.
        # A changed claim still fails; module-only is not this entry contract.
        wrong = dict(proposal)
        wrong["globals"] = dict(claims)
        wrong["globals"][next(iter(claims))] = "tampered"
        assert check_closed_effects(source, wrong, await_globals=driver)["status"] == "rejected"
        assert check_closed_effects(source, proposal)["status"] == "rejected"
    else:
        name = next(iter(claims))
        mutation = f'{name}.append("added")' if type(claims[name]) is list else f'{name} = None'
        mutated = source + '\n' + mutation + '\n'
        assert check_closed_effects(mutated, candidate(mutated, **claims))["status"] == "rejected"


def test_target_exec_eval_and_import_are_not_used(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("native target execution forbidden")
    monkeypatch.setattr("builtins.exec", forbidden)
    monkeypatch.setattr("builtins.eval", forbidden)
    report = analyze_closed_source("result = 1 + 2\n", ["result"])
    assert report["status"] == "closed_model_completed"
    assert report["observations"] == {"result": 3}
