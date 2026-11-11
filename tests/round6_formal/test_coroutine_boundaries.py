"""Independent native-await state and source-arena boundary checks."""
import copy
import hashlib

import pytest

from pythonstan.analysis.pointer.ai.certified import closed_heap_effects as heap
from pythonstan.analysis.pointer.ai.certified import heap_coroutines as coroutines


WITNESSES = [
    pytest.param('''events = []
async def f():
    try:
        await c
    except RuntimeError:
        events.append("runtime")
    except ValueError:
        events.append("value")
c = f()
try:
    c.send(None)
except StopIteration:
    pass
result = events
''', ["value"], id="self_await_does_not_fabricate_closed_state"),
    pytest.param('''async def f():
    return 1
c = f()
try:
    c.throw(StopIteration("stop"))
except RuntimeError:
    result = "runtime"
except StopIteration:
    result = "stop"
''', "stop", id="newborn_throw_skips_pep479_conversion"),
    pytest.param('''async def f():
    return 1
c = f()
c.close()
try:
    c.throw(ValueError())
except RuntimeError:
    result = "runtime"
except ValueError:
    result = "value"
''', "runtime", id="closed_coroutine_throw_is_reuse_error"),
    pytest.param('''events = []
async def child():
    try:
        raise
    except ValueError:
        events.append("inherited")
    try:
        raise TypeError()
    except TypeError:
        events.append("child")
async def parent():
    try:
        raise ValueError()
    except ValueError:
        await child()
        try:
            raise
        except ValueError:
            events.append("restored")
c = parent()
try:
    c.send(None)
except StopIteration:
    pass
result = events
''', ["inherited", "child", "restored"], id="await_child_preserves_caller_handler_context"),
    pytest.param('''events = []
async def child():
    events.append("child")
    return "cleanup"
async def parent():
    try:
        return "done"
    finally:
        events.append(await child())
c = parent()
try:
    c.send(None)
except StopIteration as stop:
    result = [stop.value, events]
''', ["done", ["child", "cleanup"]], id="return_is_preserved_across_finally_await"),
]


@pytest.mark.parametrize("source, expected", WITNESSES)
def test_native_coroutine_agrees_with_trusted_python_witness(source, expected):
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == expected
    candidate = {"source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                 "semantics_version": heap.SEMANTICS_VERSION, "globals": {"result": expected}}
    report = heap.check_closed_effects(source, candidate)
    assert report["status"] == "conditional_verified", report
    assert report["runtime_observations_used"] is False


@pytest.mark.parametrize("forge", ["copy", "reconstruct", "subclass"])
def test_same_owner_pointer_cannot_forge_an_issued_coroutine(forge):
    source = "async def task():\n    return 7\npending = task()\n"
    machine = heap.ClosedHeapMachine(source, heap.Budget())
    machine.block(machine.tree.body, machine.env)
    issued = machine.env.lookup("pending")
    if forge == "copy":
        forged = copy.copy(issued)
    elif forge == "reconstruct":
        forged = coroutines.Coroutine(machine, issued.function, issued.env)
    else:
        class Forged(coroutines.Coroutine):
            pass
        forged = Forged(machine, issued.function, issued.env)
    with pytest.raises(heap.Unsupported):
        coroutines.await_value(machine, forged)
    assert issued.state == "created"
    assert coroutines.await_value(machine, issued) == 7
    assert issued.state == "closed"
