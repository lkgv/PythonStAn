"""Suspended AST control for the closed heap machine's ordinary generators.

This module adds a control component, not another expression interpreter. All
values, aliases, callbacks, mutations and budgets belong to the issuing closed
heap machine. A task stack retains pending returns/exceptions across suspension.
It is private operational machinery; no serialized or externally supplied state
is accepted. General Python soundness and an abstract heap bridge are not claimed.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field


_NO_LOCAL_EXCEPTION = object()


class _CloseYield(Exception):
    """An outward close failure, never a target exception in the frame."""


class _ScopedYield(ast.NodeVisitor):
    """Visit an executing scope, excluding bodies of newly declared scopes."""

    def __init__(self):
        self.found = False

    def visit_Yield(self, node):
        self.found = True

    visit_YieldFrom = visit_Yield

    def visit_FunctionDef(self, node):
        # Defaults and decorators execute here; their bodies do not.
        for item in (*node.decorator_list, *node.args.defaults):
            self.visit(item)
        for item in node.args.kw_defaults:
            if item is not None:
                self.visit(item)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        for item in (*node.args.defaults, *node.args.kw_defaults):
            if item is not None:
                self.visit(item)

    def visit_ClassDef(self, node):
        for item in (*node.decorator_list, *node.bases):
            self.visit(item)
        for item in node.keywords:
            self.visit(item.value)


def has_scoped_yield(node):
    visitor = _ScopedYield()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for statement in node.body:
            visitor.visit(statement)
    else:
        visitor.visit(node)
    return visitor.found


def _contains_yield(node):
    visitor = _ScopedYield()
    visitor.visit(node)
    return visitor.found


@dataclass(eq=False)
class HeapGenerator:
    machine: object
    function: object
    env: object
    tasks: list = field(default_factory=list)
    lifecycle: str = "created"
    running: bool = False
    started: bool = False
    waiting_targets: tuple = ()
    local_exception: object = _NO_LOCAL_EXCEPTION


@dataclass
class _TryBody:
    node: ast.Try


@dataclass
class _AfterTry:
    node: ast.Try


@dataclass
class _HandlerEnd:
    previous: object
    name: str | None


@dataclass
class _FinallyEnd:
    previous: object
    pending: object


@dataclass
class _Loop:
    node: object
    iterator: object = None


def create_heap_generator(machine, function, bound_env):
    """Allocate after Python argument binding; do not enter the body yet."""
    generator = machine.allocate(HeapGenerator(machine, function, bound_env))
    _push(generator, function.node.body)
    if not hasattr(machine, "heap_generators"):
        machine.heap_generators = []
    machine.heap_generators.append(generator)
    return generator


def _push(generator, statements):
    generator.tasks.extend(reversed(statements))


def resume_heap_generator(machine, generator, operation, args=()):
    """Return a yielded value; completion/target exceptions use machine Signal.

    ``throw`` currently admits its one-argument form with a modeled exception
    instance/class. Traceback/cause/context observation is outside the model.
    ``close`` follows the Python 3.12 return contract. Unsupported suspension
    shapes and exhausted budgets invalidate the enclosing closed calculation.
    """
    from .closed_heap_effects import (
        Builtin, BudgetExceeded, ExceptionValue, Signal, Unsupported, _UNBOUND,
    )

    if type(generator) is not HeapGenerator or generator.machine is not machine:
        raise Unsupported("foreign or reconstructed heap generator")
    if not any(item is generator for item in getattr(machine, "heap_generators", ())):
        raise Unsupported("unregistered heap generator")
    if operation not in {"next", "send", "throw", "close"}:
        raise Unsupported("generator operation " + str(operation))
    if operation == "throw" and len(args) in (2, 3):
        raise Unsupported("multi-argument generator throw normalization")
    if len(args) != (1 if operation in {"send", "throw"} else 0):
        machine.fail("TypeError", "generator method arguments")

    incoming = None
    sent = args[0] if operation == "send" else None
    if operation == "throw":
        exception = args[0]
        if isinstance(exception, Builtin):
            exception = machine.call(exception, [], {})
        # Closed-machine custom exception classes are allocated by that machine.
        if not isinstance(exception, ExceptionValue):
            from .closed_heap_effects import Class
            if isinstance(exception, Class) and getattr(exception, "exception_base", None):
                exception = machine.call(exception, [], {})
        if not isinstance(exception, ExceptionValue):
            machine.fail("TypeError", "throw requires a modeled exception")
        incoming = Signal("raise", exception)
    elif operation == "close":
        incoming = Signal("raise", ExceptionValue("GeneratorExit"))

    # Argument parsing/exception normalization precedes the running-frame check.
    # A reentrant send() with no argument still raises TypeError, not ValueError.
    if generator.running:
        machine.fail("ValueError", "generator already executing")

    if generator.lifecycle == "closed":
        if operation == "close":
            return None
        if operation == "throw":
            raise incoming
        raise Signal("raise", ExceptionValue("StopIteration"))
    if generator.lifecycle == "created":
        if operation == "send" and sent is not None:
            machine.fail("TypeError", "cannot send non-None to a just-started generator")
        if operation in {"throw", "close"}:
            generator.lifecycle = "closed"
            generator.tasks.clear()
            if operation == "close":
                return None
            # PEP 479 does not rewrite an exception before the frame starts.
            raise incoming

    if machine.depth >= machine.budget.max_call_depth:
        raise BudgetExceeded("generator call depth budget exhausted")
    ambient_exception = machine.active_exception
    generator.running = True
    machine.depth += 1

    def set_local(value):
        generator.local_exception = value
        machine.active_exception = ambient_exception if value is _NO_LOCAL_EXCEPTION else value

    def finalbody(node, pending):
        previous = generator.local_exception
        if pending is not None and pending.kind == "raise":
            set_local(pending.value)
        generator.tasks.append(_FinallyEnd(previous, pending))
        _push(generator, node.finalbody)

    def unwind(signal):
        """Find a handler/loop/finally boundary, or propagate a terminal exit."""
        while generator.tasks:
            task = generator.tasks.pop()
            if isinstance(task, _HandlerEnd):
                if task.name:
                    generator.env.bind(task.name, _UNBOUND)
                set_local(task.previous)
            elif isinstance(task, _FinallyEnd):
                set_local(task.previous)
                # A new exit replaces the pending exit held by this finally.
            elif isinstance(task, _TryBody):
                if signal.kind == "raise":
                    previous = generator.local_exception
                    set_local(signal.value)
                    matched = False
                    try:
                        for handler in task.node.handlers:
                            if handler.type is None or machine.exception_matches(
                                signal.value, machine.expr(handler.type, generator.env)
                            ):
                                generator.tasks.append(_AfterTry(task.node))
                                set_local(signal.value)
                                if handler.name:
                                    generator.env.bind(handler.name, signal.value)
                                generator.tasks.append(_HandlerEnd(previous, handler.name))
                                _push(generator, handler.body)
                                matched = True
                                return
                    except Signal as replacement:
                        signal = replacement
                    finally:
                        if not matched:
                            set_local(previous)
                finalbody(task.node, signal)
                return
            elif isinstance(task, _AfterTry):
                finalbody(task.node, signal)
                return
            elif isinstance(task, _Loop):
                if signal.kind == "break":
                    return
                if signal.kind == "continue":
                    generator.tasks.append(task)
                    return
        raise signal

    def step_loop(task):
        node = task.node
        machine.tick("generator_loop")
        if isinstance(node, ast.While):
            exhausted = not machine.truth(machine.expr(node.test, generator.env))
        else:
            try:
                value = machine.next_value(task.iterator)
            except Signal as signal:
                if signal.kind != "raise" or not machine.exception_matches(signal.value, Builtin("StopIteration")):
                    raise
                exhausted = True
            else:
                exhausted = False
                machine.assign(node.target, value, generator.env)
        if exhausted:
            _push(generator, node.orelse)
        else:
            generator.tasks.append(task)
            _push(generator, node.body)

    try:
        set_local(generator.local_exception)
        if not generator.started:
            generator.started = True
            machine.calls.append(generator.function.qualname)
        if incoming is not None:
            generator.waiting_targets = ()
            unwind(incoming)
        else:
            targets, generator.waiting_targets = generator.waiting_targets, ()
            try:
                for target in targets:
                    machine.assign(target, sent, generator.env)
            except Signal as signal:
                unwind(signal)

        generator.lifecycle = "suspended"
        while generator.tasks:
            task = generator.tasks.pop()
            try:
                if isinstance(task, _TryBody):
                    generator.tasks.append(_AfterTry(task.node))
                    _push(generator, task.node.orelse)
                elif isinstance(task, _AfterTry):
                    finalbody(task.node, None)
                elif isinstance(task, _HandlerEnd):
                    if task.name:
                        generator.env.bind(task.name, _UNBOUND)
                    set_local(task.previous)
                elif isinstance(task, _FinallyEnd):
                    set_local(task.previous)
                    if task.pending is not None:
                        raise task.pending
                elif isinstance(task, _Loop):
                    step_loop(task)
                elif isinstance(task, ast.Expr) and isinstance(task.value, ast.Yield):
                    value = machine.expr(task.value.value, generator.env) if task.value.value else None
                    if operation == "close":
                        raise _CloseYield()
                    return value
                elif isinstance(task, (ast.Assign, ast.AnnAssign)) and isinstance(task.value, ast.Yield):
                    value = machine.expr(task.value.value, generator.env) if task.value.value else None
                    generator.waiting_targets = tuple(task.targets) if isinstance(task, ast.Assign) else (task.target,)
                    if operation == "close":
                        raise _CloseYield()
                    return value
                elif isinstance(task, ast.If):
                    _push(generator, task.body if machine.truth(machine.expr(task.test, generator.env)) else task.orelse)
                elif isinstance(task, ast.Try):
                    generator.tasks.append(_TryBody(task))
                    _push(generator, task.body)
                elif isinstance(task, (ast.For, ast.While)):
                    iterator = machine.iterate(machine.expr(task.iter, generator.env)) if isinstance(task, ast.For) else None
                    generator.tasks.append(_Loop(task, iterator))
                else:
                    if _contains_yield(task):
                        raise Unsupported("unmodeled suspension shape " + type(task).__name__)
                    machine.stmt(task, generator.env)
                machine.tick("generator_task")
            except Signal as signal:
                unwind(signal)
        generator.lifecycle = "closed"
        if operation == "close":
            return None
        raise Signal("return", None)
    except _CloseYield:
        # Do not inject this outward failure into the suspended generator.
        raise Signal("raise", ExceptionValue("RuntimeError", ("generator ignored GeneratorExit",), implicit=True))
    except Signal as signal:
        generator.lifecycle = "closed"
        generator.tasks.clear()
        generator.waiting_targets = ()
        if signal.kind == "return":
            if operation == "close":
                return None
            raise Signal("raise", ExceptionValue("StopIteration", (signal.value,)))
        if signal.kind == "raise":
            if operation == "close" and machine.exception_matches(signal.value, Builtin("GeneratorExit")):
                return None
            if machine.exception_matches(signal.value, Builtin("StopIteration")):
                # Cause/context are deliberately not exposed by this model.
                raise Signal("raise", ExceptionValue("RuntimeError", ("generator raised StopIteration",), implicit=True))
        raise
    finally:
        generator.running = False
        machine.depth -= 1
        machine.active_exception = ambient_exception
