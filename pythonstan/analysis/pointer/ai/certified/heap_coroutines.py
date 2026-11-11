"""Native coroutines in the finite closed heap model.

The admitted await graph consists exclusively of native source coroutines which
finish without external suspension. No event loop, task scheduling, arbitrary
``__await__``, cancellation or async generator is approximated as completion.
Arguments bind at creation; the AST body runs only at an explicit drive/await.
All frames refer to the same modeled heap and never execute target Python code.
This is private operational machinery, not a serialization or mutable-state API.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
from typing import Any


@dataclass(eq=False)
class Coroutine:
    owner: Any
    function: Any
    env: Any
    state: str = "created"


def entry_binding(await_globals=()):
    """Validate an explicit ordered driver, never infer it from effect claims."""
    if type(await_globals) not in (tuple, list) or any(
            type(name) is not str or not name.isidentifier()
            for name in await_globals):
        raise ValueError("await_globals must be an ordered list/tuple of identifiers")
    if len(await_globals) > 1000:
        raise ValueError("await driver exceeds 1000 operations")
    spec = {"kind": "module_then_await_native_v1", "await_globals": list(await_globals)}
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return spec, digest


def create(machine, function, args, kwargs):
    from .closed_heap_effects import Env, Unsupported
    if not isinstance(function.node, ast.AsyncFunctionDef):
        raise Unsupported("native coroutine requires async source function")
    env = Env(function.closure, local_names=function.local_names, function=True,
              mangle=function.mangle, qualname=function.qualname)
    machine.bind_arguments(function, args, dict(kwargs), env)
    coroutine = machine.allocate(Coroutine(machine, function, env))
    if not hasattr(machine, "_issued_native_coroutines"):
        machine._issued_native_coroutines = {}
    machine._issued_native_coroutines[id(coroutine)] = coroutine
    return coroutine


def _check_owner(machine, coroutine):
    from .closed_heap_effects import Unsupported
    if type(coroutine) is not Coroutine or coroutine.owner is not machine:
        raise Unsupported("coroutine belongs to another source/heap arena")
    if getattr(machine, "_issued_native_coroutines", {}).get(id(coroutine)) is not coroutine:
        raise Unsupported("unregistered or reconstructed native coroutine")
    if coroutine.state == "unknown":
        raise Unsupported("coroutine has an unfinished unsupported transition")


def await_value(machine, value):
    from .closed_heap_effects import (
        BudgetExceeded, Builtin, Signal, Unsupported,
    )
    if not isinstance(value, Coroutine):
        # Some non-coroutines can implement user __await__. Do not replace
        # that missing protocol with a fabricated target TypeError.
        raise Unsupported("await requires a modeled native coroutine")
    _check_owner(machine, value)
    machine.tick("coroutine-await")
    if value.state == "closed":
        machine.fail("RuntimeError", "cannot reuse already awaited coroutine")
    if value.state == "running":
        machine.fail("ValueError", "coroutine already executing")
    if machine.depth >= machine.budget.max_call_depth:
        raise BudgetExceeded("coroutine call depth budget exhausted")
    value.state = "running"
    machine.depth += 1
    machine.calls.append(value.function.qualname)
    try:
        try:
            machine.block(value.function.node.body, value.env)
        except Signal as signal:
            if signal.kind == "return":
                return signal.value
            if signal.kind == "raise" and machine.exception_matches(
                    signal.value, Builtin("StopIteration")):
                machine.fail("RuntimeError", "coroutine raised StopIteration")
            raise
        return None
    except (Unsupported, BudgetExceeded):
        value.state = "unknown"
        raise
    finally:
        machine.depth -= 1
        if value.state == "running":
            value.state = "closed"


def method(machine, coroutine, name, args, kwargs):
    from .closed_heap_effects import (
        Builtin, ExceptionValue, Signal, Unsupported, _EXCEPTIONS,
    )
    _check_owner(machine, coroutine)
    machine.tick("coroutine-" + name)
    if kwargs:
        machine.fail("TypeError", "coroutine methods do not accept keywords")
    if name == "send":
        if len(args) != 1:
            machine.fail("TypeError", "send expects one argument")
        if coroutine.state == "created" and args[0] is not None:
            machine.fail("TypeError", "cannot send non-None to newborn coroutine")
        result = await_value(machine, coroutine)
        # This is protocol termination, outside the coroutine body; therefore
        # PEP 479 conversion must not apply here. Its value is exact.
        raise Signal("raise", ExceptionValue("StopIteration", (result,)))
    if name == "close":
        if args:
            machine.fail("TypeError", "close expects no arguments")
        if coroutine.state == "running":
            machine.fail("ValueError", "coroutine already executing")
        coroutine.state = "closed"
        return None
    if name == "throw":
        if len(args) != 1:
            raise Unsupported("only one-argument coroutine throw is modeled")
        value = args[0]
        if isinstance(value, Builtin) and value.name in _EXCEPTIONS:
            value = ExceptionValue(value.name)
        if not isinstance(value, ExceptionValue):
            raise Unsupported("coroutine throw exception normalization")
        if coroutine.state == "closed":
            machine.fail("RuntimeError", "cannot reuse already awaited coroutine")
        if coroutine.state == "running":
            machine.fail("ValueError", "coroutine already executing")
        # A newborn frame has entered no handlers/finally suites. Throw closes
        # it and propagates directly, including StopIteration itself.
        coroutine.state = "closed"
        raise Signal("raise", value)
    raise Unsupported("coroutine method " + name)
