"""Independent concrete witnesses; production models never consume this output."""
import hashlib

import pytest

from pythonstan.analysis.pointer.ai.certified import closed_heap_effects as model
from pythonstan.analysis.pointer.ai.certified import heap_coroutines as coroutines


def derive(source, names=("result",), driver=("pending",), budget=None):
    return model.analyze_closed_source(source, list(names), await_globals=driver, budget=budget)


def concrete(source, driver=("pending",)):
    env = {}
    exec(compile(source, "<independent-coroutine-witness>", "exec"), env)
    for name in driver:
        try:
            env[name].send(None)
        except StopIteration:
            pass
        else:
            raise AssertionError("test witness unexpectedly suspended externally")
    return env


WITNESSES = [
    '''audit = []
result = []
async def child(value):
    audit.append(value)
    return audit
async def main():
    first = child("first")
    result.append(len(audit))
    same = await first
    await child("second")
    result.append(same is audit)
    result.append(list(same))
pending = main()
''',
    '''result = []
async def child():
    try:
        raise ValueError("child")
    finally:
        result.append("child-finally")
async def main():
    try:
        await child()
    except ValueError:
        result.append("caught")
    finally:
        result.append("parent-finally")
pending = main()
''',
    '''result = []
async def child():
    return "answer"
async def main():
    c = child()
    result.append(await c)
    try:
        await c
    except RuntimeError:
        result.append("reuse")
pending = main()
''',
    '''result = []
def default():
    result.append("default")
    return "value"
async def child(value=default()):
    result.append(value)
async def main():
    c = child()
    result.append("created")
    await c
pending = main()
''',
    '''result = []
async def child(value):
    result.append(value)
async def main():
    try:
        c = child()
    except TypeError:
        result.append("bind-before-body")
pending = main()
''',
    '''result = []
class Callback:
    async def __call__(self, value):
        result.append(value)
        return value
async def main():
    cb = Callback()
    result.append(await cb("called"))
pending = main()
''',
    '''result = []
async def child():
    try:
        result.append("body")
    finally:
        result.append("cleanup")
async def main():
    c = child()
    c.close()
    try:
        await c
    except RuntimeError:
        result.append("closed")
pending = main()
''',
    '''result = []
async def child():
    return 7
async def main():
    c = child()
    try:
        c.send(1)
    except TypeError:
        result.append("newborn")
    try:
        c.send(None)
    except StopIteration as done:
        result.append(done.value)
pending = main()
''',
    '''result = []
async def child():
    raise StopIteration("bad")
async def main():
    c = child()
    try:
        await c
    except RuntimeError:
        result.append("converted")
    c = child()
    try:
        c.throw(StopIteration("newborn"))
    except StopIteration:
        result.append("not-converted")
pending = main()
''',
    '''result = []
async def helper():
    raise
async def main():
    try:
        raise ValueError("ambient")
    except ValueError:
        try:
            await helper()
        except ValueError:
            result.append("inherited")
pending = main()
''',
]


@pytest.mark.parametrize("source", WITNESSES)
def test_shared_heap_await_tree_matches_cpython(source):
    expected = concrete(source)["result"]
    report = derive(source)
    assert report["status"] == "closed_model_completed", report
    assert report["observations"]["result"] == expected
    assert report["normal_module_completion"] is True
    assert report["normal_entry_completion"] is True


def test_module_creation_does_not_execute_coroutine_body():
    report = derive(WITNESSES[0], driver=())
    assert report["observations"] == {"result": []}
    assert "main" not in report["prefix_calls"]


def test_driver_binding_is_required_and_order_sensitive():
    source = WITNESSES[0]
    expected = concrete(source)["result"]
    candidate = {"source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                 "semantics_version": model.SEMANTICS_VERSION,
                 "globals": {"result": expected}}
    assert model.check_closed_effects(source, candidate, await_globals=("pending",))["status"] == "rejected"
    candidate["entry_sha256"] = coroutines.entry_binding(("pending",))[1]
    report = model.check_closed_effects(source, candidate, await_globals=("pending",))
    assert report["status"] == "conditional_verified", report
    assert model.check_closed_effects(source, candidate, await_globals=("pending", "pending"))["status"] == "rejected"
    assert model.check_closed_effects(source, candidate)["status"] == "rejected"


def test_repeated_driver_is_a_real_reuse_exception():
    report = derive(WITNESSES[0], driver=("pending", "pending"))
    assert report["status"] == "model_exception"
    assert report["normal_module_completion"] is True
    assert report["normal_entry_completion"] is False
    assert report["observations"] == {}


@pytest.mark.parametrize("driver", ["pending", (42,), ["bad-name"], ["p"] * 1001])
def test_driver_rejects_malformed_values(driver):
    with pytest.raises(ValueError):
        coroutines.entry_binding(driver)


def test_external_await_protocol_is_residual():
    source = '''result = []
class External:
    def __await__(self):
        yield "external"
async def main():
    await External()
    result.append("must-not-prove")
pending = main()
'''
    report = derive(source)
    assert report["status"] == "unsupported", report
    assert report["observations"] == {}


def test_budget_exhaustion_keeps_entry_incomplete():
    source = '''result = []
async def recurse():
    await recurse()
pending = recurse()
'''
    report = derive(source, budget=model.Budget(max_call_depth=4))
    assert report["status"] == "unknown", report
    assert report["normal_entry_completion"] is False
    assert report["observations"] == {}


def test_foreign_heap_coroutine_is_rejected():
    source = "async def task():\n    return 1\npending=task()\n"
    left = model.ClosedHeapMachine(source, model.Budget())
    right = model.ClosedHeapMachine(source, model.Budget())
    left.block(left.tree.body, left.env)
    with pytest.raises(model.Unsupported, match="another source/heap"):
        coroutines.await_value(right, left.env.lookup("pending"))
