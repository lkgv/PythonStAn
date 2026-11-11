"""Source-derived, finite generator continuation semantics for a closed fragment.

This module interprets a checked AST; it never executes target source. It tracks
created/suspended/closed states, locals, handler scopes and pending finally exits.
The value domain is an explicitly finite set of exact builtin scalar inputs.
Heap operations, external callbacks, async, delegation and loops are rejected.

A complete step covers one declared input state/operation in this fragment. It
is not a proof about the general KCFA/AI heap, arbitrary Python or pluggy. The
legacy allocation-summary generator model deliberately remains incomplete.
"""
from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from itertools import product
from typing import Any, Mapping, Optional

SEMANTICS_VERSION = "closed-generator-continuations-py312-v1"
_EXCEPTION_PARENT = {
    "Exception": "BaseException", "GeneratorExit": "BaseException",
    "SystemExit": "BaseException", "KeyboardInterrupt": "BaseException",
    "ValueError": "Exception", "TypeError": "Exception", "RuntimeError": "Exception",
    "LookupError": "Exception", "KeyError": "LookupError", "IndexError": "LookupError",
    "ArithmeticError": "Exception", "ZeroDivisionError": "ArithmeticError",
    "OverflowError": "ArithmeticError", "AssertionError": "Exception",
    "AttributeError": "Exception", "NameError": "Exception", "UnboundLocalError": "NameError",
    "StopIteration": "Exception", "NotImplementedError": "RuntimeError",
    "RecursionError": "RuntimeError",
}
EXCEPTION_TYPES = frozenset(_EXCEPTION_PARENT) | {"BaseException"}


class UnsupportedGeneratorSource(ValueError):
    """The source falls outside the checked continuation fragment."""


class InvalidContinuation(ValueError):
    """The handle was not issued by this exact model, or was forged."""


class InvalidGeneratorInput(ValueError):
    """An operation or parameter binding is outside the finite input contract."""


class _BudgetExceeded(Exception):
    pass


@dataclass(frozen=True)
class ProtocolBudget:
    max_source_bytes: int = 100_000
    max_ast_nodes: int = 10_000
    max_steps: int = 10_000
    max_states: int = 10_000
    max_call_depth: int = 64
    max_value_size: int = 4096

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class ProtocolException:
    kind: str
    args: Optional[tuple] = ()  # None denotes unspecified implicit-exception arguments.

    def __post_init__(self):
        if type(self.kind) is not str or self.kind not in EXCEPTION_TYPES:
            raise InvalidGeneratorInput("unsupported builtin exception type")
        if self.args is not None and type(self.args) is not tuple:
            raise InvalidGeneratorInput("exception args must be a tuple or unknown")


@dataclass(frozen=True)
class ProtocolEvent:
    kind: str
    name: str
    line: int


@dataclass(frozen=True)
class Continuation:
    """Opaque model-issued handle. Diagnostic serialization is not resumable."""
    source_sha256: str
    function: str
    status: str
    state_id: int


@dataclass(frozen=True)
class ProtocolOutcome:
    kind: str  # yield, return (StopIteration.value), raise, close
    value: Any
    exception: Optional[ProtocolException]
    continuation: Continuation
    events: tuple[ProtocolEvent, ...] = ()


@dataclass(frozen=True)
class ProtocolStep:
    status: str  # complete or unknown; unknown does not denote an empty behavior set.
    outcomes: tuple[ProtocolOutcome, ...]
    source_sha256: str
    diagnostics: tuple[str, ...] = ()
    residual: Optional[Continuation] = None
    semantics_version: str = SEMANTICS_VERSION

    def to_dict(self):
        return {
            "status": self.status, "source_sha256": self.source_sha256,
            "semantics_version": self.semantics_version,
            "diagnostics": list(self.diagnostics),
            "scope": "closed finite scalar generator state/operation; stable source bindings; explicit caller exception context; Python 3.12",
            "external_heap_effect": "unchanged" if self.status == "complete" else "unproved",
            "general_python_soundness_proved": False,
            "residual_state_id": self.residual.state_id if self.residual else None,
            "outcomes": [{
                "kind": o.kind, "value": _json_value(o.value),
                "exception": None if o.exception is None else {
                    "kind": o.exception.kind, "args": _json_value(o.exception.args)},
                "continuation": {"state_id": o.continuation.state_id,
                                 "status": o.continuation.status,
                                 "function": o.continuation.function},
                "events": [dict(kind=e.kind, name=e.name, line=e.line) for e in o.events],
            } for o in self.outcomes],
        }


@dataclass(frozen=True)
class _FunctionValue:
    name: str


@dataclass(frozen=True)
class _ExceptionClass:
    name: str


def _json_value(value):
    if isinstance(value, _FunctionValue):
        return {"source_function": value.name}
    if isinstance(value, _ExceptionClass):
        return {"exception_class": value.name}
    if isinstance(value, ProtocolException):
        return {"exception": value.kind, "args": _json_value(value.args)}
    if type(value) is tuple:
        return {"tuple": [_json_value(x) for x in value]}
    return value


@dataclass(frozen=True)
class _Exit:
    kind: str  # normal/return/raise
    value: Any = None


@dataclass(frozen=True)
class _TryEnd:
    node: ast.Try
    stage: str
    previous_exception: Optional[ProtocolException]
    binding: Optional[str] = None


@dataclass(frozen=True)
class _FinallyEnd:
    pending: _Exit
    previous_exception: Optional[ProtocolException]


@dataclass(frozen=True)
class _Machine:
    function: str
    status: str
    env: tuple[tuple[str, Any], ...]
    tasks: tuple
    active_exception: Optional[ProtocolException] = None
    resume_target: Optional[str] = None


class _Raised(Exception):
    def __init__(self, exception):
        self.exception = exception


class _Fuel:
    def __init__(self, budget):
        self.budget, self.steps = budget, 0

    def use(self):
        self.steps += 1
        if self.steps > self.budget.max_steps:
            raise _BudgetExceeded("continuation step budget exhausted")


class _Validator:
    def __init__(self, functions):
        self.functions = functions

    def statements(self, statements):
        for node in statements:
            if isinstance(node, ast.Pass):
                continue
            if isinstance(node, ast.Expr):
                self.expr(node.value, allow_yield=True)
            elif isinstance(node, ast.Assign):
                if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                    self.unsupported(node, "only local name assignment is supported")
                self.expr(node.value, allow_yield=True)
            elif isinstance(node, ast.Return):
                self.expr(node.value)
            elif isinstance(node, ast.If):
                self.expr(node.test)
                self.statements(node.body)
                self.statements(node.orelse)
            elif isinstance(node, ast.Raise):
                self.expr(node.exc)
                self.expr(node.cause)
            elif isinstance(node, ast.Try):
                self.statements(node.body)
                self.statements(node.orelse)
                self.statements(node.finalbody)
                for h in node.handlers:
                    types = h.type.elts if isinstance(h.type, ast.Tuple) else (h.type,)
                    if any(t is not None and (not isinstance(t, ast.Name) or t.id not in EXCEPTION_TYPES)
                           for t in types):
                        self.unsupported(h, "handlers must name fixed builtin exception types")
                    self.statements(h.body)
            else:
                self.unsupported(node, "statement has no checked continuation transfer")

    def expr(self, node, allow_yield=False):
        if node is None:
            return
        if isinstance(node, ast.Constant):
            if type(node.value) not in (type(None), bool, int, str):
                self.unsupported(node, "constant is outside the exact scalar domain")
        elif isinstance(node, ast.Name):
            return
        elif isinstance(node, ast.Tuple):
            for item in node.elts:
                self.expr(item)
        elif isinstance(node, ast.Yield) and allow_yield:
            self.expr(node.value)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.UAdd, ast.USub)):
            self.expr(node.operand)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
            self.expr(node.left)
            self.expr(node.right)
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            for item in node.values:
                self.expr(item)
        elif isinstance(node, ast.Compare):
            for op, left, right in zip(node.ops, [node.left, *node.comparators[:-1]], node.comparators):
                if not isinstance(op, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot)):
                    self.unsupported(node, "comparison has no checked transfer")
                if isinstance(op, (ast.Is, ast.IsNot)) and not any(
                    isinstance(x, ast.Constant) and type(x.value) in (type(None), bool) for x in (left, right)
                ):
                    self.unsupported(node, "identity comparisons require a None/bool literal")
            self.expr(node.left)
            for item in node.comparators:
                self.expr(item)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in self.functions.keys() | EXCEPTION_TYPES:
                self.unsupported(node, "only source-defined helpers and fixed exception constructors can be called")
            if any(isinstance(x, ast.Starred) for x in node.args) or any(k.arg is None for k in node.keywords):
                self.unsupported(node, "argument unpacking is outside the checked fragment")
            for arg in node.args:
                self.expr(arg)
            for arg in node.keywords:
                self.expr(arg.value)
        else:
            self.unsupported(node, "expression has no checked continuation transfer")

    @staticmethod
    def unsupported(node, reason):
        raise UnsupportedGeneratorSource(f"{type(node).__name__} at line {getattr(node, 'lineno', '?')}: {reason}")


class ProtocolModel:
    """A checked source model and its private, source-bound continuation store."""
    def __init__(self, source, function, budget=None):
        if type(source) is not str or type(function) is not str:
            raise UnsupportedGeneratorSource("source and function must be strings")
        self.budget = budget or ProtocolBudget()
        if not isinstance(self.budget, ProtocolBudget):
            raise ValueError("budget must be ProtocolBudget")
        encoded = source.encode("utf-8")
        if len(encoded) > self.budget.max_source_bytes:
            raise UnsupportedGeneratorSource("source size budget exceeded")
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError) as exc:
            raise UnsupportedGeneratorSource(str(exc)) from exc
        if sum(1 for _ in ast.walk(tree)) > self.budget.max_ast_nodes:
            raise UnsupportedGeneratorSource("AST size budget exceeded")
        self.source_sha256 = hashlib.sha256(encoded).hexdigest()
        self.function = function
        self._functions = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                if node.name in self._functions or node.name in EXCEPTION_TYPES:
                    raise UnsupportedGeneratorSource("duplicate or exception-shadowing source definition")
                self._functions[node.name] = node
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and type(node.value.value) is str:
                continue
            elif isinstance(node, ast.ImportFrom) and node.module == "__future__" and all(
                    n.name == "annotations" and n.asname is None for n in node.names):
                continue
            else:
                _Validator.unsupported(node, "module must contain only stable source function definitions")
        if function not in self._functions:
            raise UnsupportedGeneratorSource("selected generator is not defined")
        self._locals = {}
        self._generators = set()
        validator = _Validator(self._functions)
        for name, node in self._functions.items():
            if node.decorator_list or node.returns or node.args.vararg or node.args.kwarg or node.type_comment or getattr(node, "type_params", ()):
                raise UnsupportedGeneratorSource("decorators, annotations and variadic parameters are unsupported")
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            if any(arg.annotation is not None for arg in args):
                raise UnsupportedGeneratorSource("parameter annotations are unsupported")
            for default in [*node.args.defaults, *(x for x in node.args.kw_defaults if x is not None)]:
                self._literal(default)
            validator.statements(node.body)
            local = {arg.arg for arg in args}
            for item in ast.walk(node):
                if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store):
                    local.add(item.id)
                if isinstance(item, ast.ExceptHandler) and item.name:
                    local.add(item.name)
                if isinstance(item, ast.Yield):
                    self._generators.add(name)
            self._locals[name] = frozenset(local)
            if local & EXCEPTION_TYPES:
                raise UnsupportedGeneratorSource("builtin exception bindings must not be shadowed")
            for item in ast.walk(node):
                if (isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
                        and item.id not in local and item.id not in self._functions
                        and item.id not in EXCEPTION_TYPES):
                    raise UnsupportedGeneratorSource("unmodeled global/builtin binding: " + item.id)
        if function not in self._generators:
            raise UnsupportedGeneratorSource("selected function is not a generator")
        # Reject syntactically visible recursive helper cycles. Local aliases
        # can still redirect a permitted call name; the runtime depth/step
        # budget returns unknown if this creates a hidden recursive cycle.
        calls = {name: {x.func.id for x in ast.walk(node) if isinstance(x, ast.Call)
                       and isinstance(x.func, ast.Name) and x.func.id in self._functions}
                 for name, node in self._functions.items()}
        visited = set()
        def visit(name, ancestors):
            if name in ancestors:
                raise UnsupportedGeneratorSource("recursive helper calls need an unimplemented fixpoint")
            if name in visited:
                return
            for callee in calls[name]:
                visit(callee, ancestors | {name})
            visited.add(name)
        for name in calls:
            visit(name, set())
        # Compile only for CPython's static syntax checks (duplicate parameters,
        # repeated keywords, handler ordering, etc.). The code object is never
        # executed and its bytecode is not used as an effect proof.
        try:
            compile(source, "<generator-protocol>", "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as exc:
            raise UnsupportedGeneratorSource(str(exc)) from exc
        self._states = {}
        self._handles = {}

    def _check_value(self, value, *, internal=False, depth=0):
        if depth > 32:
            raise _BudgetExceeded("finite value nesting budget exceeded")
        if type(value) in (type(None), bool):
            return value
        if type(value) is int and value.bit_length() <= self.budget.max_value_size:
            return value
        if type(value) is str and len(value) <= self.budget.max_value_size:
            return value
        if type(value) is tuple and len(value) <= self.budget.max_value_size:
            for item in value:
                self._check_value(item, internal=internal, depth=depth + 1)
            return value
        if internal and isinstance(value, (_FunctionValue, _ExceptionClass, ProtocolException)):
            if isinstance(value, ProtocolException) and value.args is not None:
                self._check_value(value.args, internal=True, depth=depth + 1)
            return value
        if type(value) in (int, str, tuple):
            raise _BudgetExceeded("finite value size budget exceeded")
        raise InvalidGeneratorInput("only exact builtin None/bool/int/str/tuple inputs are supported")

    def _literal(self, node):
        if isinstance(node, ast.Constant):
            value = node.value
        elif isinstance(node, ast.Tuple):
            value = tuple(self._literal(x) for x in node.elts)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)) and isinstance(node.operand, ast.Constant) and type(node.operand.value) is int:
            value = node.operand.value if isinstance(node.op, ast.UAdd) else -node.operand.value
        else:
            raise UnsupportedGeneratorSource("defaults must be finite literal scalar values")
        try:
            return self._check_value(value)
        except (InvalidGeneratorInput, _BudgetExceeded) as exc:
            raise UnsupportedGeneratorSource(str(exc)) from exc

    def _issue(self, machine):
        if len(self._handles) >= self.budget.max_states:
            raise _BudgetExceeded("continuation state budget exhausted")
        identifier = len(self._handles)
        handle = Continuation(self.source_sha256, self.function, machine.status, identifier)
        self._handles[identifier] = handle
        self._states[identifier] = machine
        return handle

    def _machine(self, handle):
        if type(handle) is not Continuation or self._handles.get(handle.state_id) is not handle:
            raise InvalidContinuation("continuation is forged, deserialized, or belongs to another model")
        machine = self._states[handle.state_id]
        if (handle.source_sha256 != self.source_sha256 or handle.function != self.function
                or handle.status != machine.status):
            raise InvalidContinuation("continuation metadata no longer matches its issued state")
        return machine

    def initial(self, arguments: Optional[Mapping[str, Any]] = None):
        """Create a fresh state from declared parameter bindings, not Python kwargs.

        This API takes an already bound parameter environment (including
        positional-only names). Unknown/missing bindings are invalid inputs;
        they do not enter the generator body or produce a proof.
        """
        if arguments is None:
            arguments = {}
        if type(arguments) is not dict or any(type(k) is not str for k in arguments):
            raise InvalidGeneratorInput("arguments must be a string-keyed dictionary")
        node = self._functions[self.function]
        args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        names = {a.arg for a in args}
        if set(arguments) - names:
            raise InvalidGeneratorInput("unknown parameter binding")
        defaults = {arg.arg: self._literal(value) for arg, value in zip(
            [*node.args.posonlyargs, *node.args.args][-len(node.args.defaults):] if node.args.defaults else [], node.args.defaults)}
        defaults.update({arg.arg: self._literal(value) for arg, value in zip(node.args.kwonlyargs, node.args.kw_defaults) if value is not None})
        env = dict(defaults, **arguments)
        if set(env) != names:
            raise InvalidGeneratorInput("missing parameter binding")
        try:
            for value in env.values():
                self._check_value(value)
            return self._issue(_Machine(self.function, "created", tuple(sorted(env.items())), tuple(reversed(node.body))))
        except _BudgetExceeded as exc:
            raise InvalidGeneratorInput(str(exc)) from exc

    def initial_alternatives(self, domains: Mapping[str, tuple]):
        """Enumerate an explicitly finite product without merging correlations."""
        if type(domains) is not dict or any(type(k) is not str or type(v) is not tuple or not v for k, v in domains.items()):
            raise InvalidGeneratorInput("finite domains must be nonempty tuples under parameter names")
        count = 1
        for values in domains.values():
            count *= len(values)
            if count + len(self._handles) > self.budget.max_states:
                raise InvalidGeneratorInput("finite input product exceeds state budget")
        names = tuple(domains)
        return tuple(self.initial(dict(zip(names, values))) for values in product(*(domains[n] for n in names)))

    def resume(self, continuation, operation="next", *, value=None, exception=None, ambient_exception=None):
        """Transfer one operation under an explicit caller exception context.

        None means that the caller has no active exception. Generator-local
        handled exceptions override this context; the caller context is never
        stored into a suspended continuation.
        """
        machine = self._machine(continuation)
        if type(operation) is not str or operation not in {"next", "send", "throw", "close"}:
            raise InvalidGeneratorInput("unsupported generator operation")
        if operation != "send" and value is not None:
            raise InvalidGeneratorInput("value is only valid for send")
        if operation != "throw" and exception is not None:
            raise InvalidGeneratorInput("exception is only valid for throw")
        if operation == "throw" and type(exception) is not ProtocolException:
            raise InvalidGeneratorInput("throw requires a fixed builtin ProtocolException")
        if ambient_exception is not None and type(ambient_exception) is not ProtocolException:
            raise InvalidGeneratorInput("ambient exception requires a fixed builtin ProtocolException")
        events = []
        try:
            self._check_value(value)
            for error in (exception, ambient_exception):
                if error is not None and error.args is not None:
                    self._check_value(error.args)
            if machine.status == "closed":
                kind, output, error = ("raise", None, exception) if operation == "throw" else (
                    ("close", None, None) if operation == "close" else ("return", None, None))
                return self._result(continuation, kind, output, error, machine, events)
            if machine.status == "created" and operation == "send" and value is not None:
                return self._result(continuation, "raise", None, ProtocolException("TypeError", None), machine, events)
            if machine.status == "created" and operation == "close":
                closed = _Machine(machine.function, "closed", machine.env, ())
                return self._result(continuation, "close", None, None, closed, events)
            injected = exception if operation == "throw" else (
                ProtocolException("GeneratorExit") if operation == "close" else None)
            if machine.status == "created" and injected is not None:
                # No body/finally region has been entered before first resume.
                closed = _Machine(machine.function, "closed", machine.env, ())
                return self._result(continuation, "raise", None, injected, closed, events)
            else:
                env = dict(machine.env)
                if injected is None and machine.resume_target is not None:
                    env[machine.resume_target] = value if operation == "send" else None
                running = _Machine(machine.function, "running", tuple(sorted(env.items())), machine.tasks, machine.active_exception)
                exit_, result_machine = self._execute(running, _Fuel(self.budget), events, 0,
                                                     _Exit("raise", injected) if injected else None,
                                                     ambient_exception)
            if exit_.kind == "yield":
                if operation == "close":
                    return self._result(continuation, "raise", None, ProtocolException("RuntimeError", ("generator ignored GeneratorExit",)), result_machine, events)
                return self._result(continuation, "yield", exit_.value, None, result_machine, events)
            if exit_.kind == "return":
                return self._result(continuation, "close" if operation == "close" else "return",
                                    None if operation == "close" else exit_.value, None, result_machine, events)
            error = exit_.value
            if operation == "close" and error.kind == "GeneratorExit":
                return self._result(continuation, "close", None, None, result_machine, events)
            if error.kind == "StopIteration":
                error = ProtocolException("RuntimeError", ("generator raised StopIteration",))
            return self._result(continuation, "raise", None, error, result_machine, events)
        except (_BudgetExceeded, RecursionError) as exc:
            return ProtocolStep("unknown", (), self.source_sha256, (str(exc),), continuation)

    def _result(self, old_handle, kind, value, exception, machine, events):
        # Reuse unchanged state, including failed send(non-None) to a new generator.
        handle = old_handle if machine == self._machine(old_handle) else self._issue(machine)
        return ProtocolStep("complete", (ProtocolOutcome(kind, value, exception, handle, tuple(events)),), self.source_sha256)

    def _execute(self, machine, fuel, events, depth, injected=None, ambient=None):
        if depth > self.budget.max_call_depth:
            raise _BudgetExceeded("helper call depth budget exhausted")
        env, tasks = dict(machine.env), list(machine.tasks)
        active = machine.active_exception

        def finish(exit_):
            return exit_, _Machine(machine.function, "closed", tuple(sorted(env.items())), (), active)

        def cleanup(frame):
            nonlocal active
            if frame.stage == "handler":
                if frame.binding is not None:
                    env.pop(frame.binding, None)
                active = frame.previous_exception

        def finally_for(frame, pending):
            nonlocal active
            if frame.node.finalbody:
                previous = active
                if pending.kind == "raise":
                    active = pending.value
                tasks.append(_FinallyEnd(pending, previous))
                tasks.extend(reversed(frame.node.finalbody))
                return True
            return False

        def unwind(pending):
            nonlocal active
            while tasks:
                frame = tasks.pop()
                if isinstance(frame, _FinallyEnd):
                    active = frame.previous_exception
                elif isinstance(frame, _TryEnd):
                    cleanup(frame)
                    if frame.stage == "body" and pending.kind == "raise":
                        for handler in frame.node.handlers:
                            if self._matches(pending.value.kind, handler):
                                previous = active
                                active = pending.value
                                if handler.name:
                                    env[handler.name] = pending.value
                                tasks.append(_TryEnd(frame.node, "handler", previous, handler.name))
                                tasks.extend(reversed(handler.body))
                                return None
                    if finally_for(frame, pending):
                        return None
            return pending

        if injected is not None:
            escaped = unwind(injected)
            if escaped is not None:
                return finish(escaped)
        while tasks:
            fuel.use()
            task = tasks.pop()
            try:
                if isinstance(task, _FinallyEnd):
                    active = task.previous_exception
                    if task.pending.kind != "normal":
                        escaped = unwind(task.pending)
                        if escaped is not None:
                            return finish(escaped)
                    continue
                if isinstance(task, _TryEnd):
                    cleanup(task)
                    if task.stage == "body" and task.node.orelse:
                        tasks.append(_TryEnd(task.node, "else", active))
                        tasks.extend(reversed(task.node.orelse))
                    else:
                        finally_for(task, _Exit("normal"))
                    continue
                if isinstance(task, ast.Pass):
                    continue
                if isinstance(task, ast.Try):
                    tasks.append(_TryEnd(task, "body", active))
                    tasks.extend(reversed(task.body))
                    continue
                if isinstance(task, ast.If):
                    condition = self._expr(task.test, machine.function, env, active or ambient, fuel, events, depth)
                    tasks.extend(reversed(task.body if self._truth(condition) else task.orelse))
                    continue
                if isinstance(task, (ast.Assign, ast.Expr)):
                    expression = task.value
                    target = task.targets[0].id if isinstance(task, ast.Assign) else None
                    if isinstance(expression, ast.Yield):
                        value = self._expr(expression.value, machine.function, env, active or ambient, fuel, events, depth)
                        return _Exit("yield", value), _Machine(machine.function, "suspended", tuple(sorted(env.items())), tuple(tasks), active, target)
                    value = self._expr(expression, machine.function, env, active or ambient, fuel, events, depth)
                    if target is not None:
                        env[target] = value
                    continue
                if isinstance(task, ast.Return):
                    pending = _Exit("return", self._expr(task.value, machine.function, env, active or ambient, fuel, events, depth))
                elif isinstance(task, ast.Raise):
                    if task.exc is None:
                        error = active or ambient or ProtocolException("RuntimeError", None)
                    else:
                        error = self._expr(task.exc, machine.function, env, active or ambient, fuel, events, depth)
                        if isinstance(error, _ExceptionClass):
                            error = ProtocolException(error.name)
                        if not isinstance(error, ProtocolException):
                            error = ProtocolException("TypeError", None)
                    if task.cause is not None:
                        cause = self._expr(task.cause, machine.function, env, active or ambient, fuel, events, depth)
                        if cause is not None and not isinstance(cause, (_ExceptionClass, ProtocolException)):
                            error = ProtocolException("TypeError", None)
                    events.append(ProtocolEvent("raise", error.kind, task.lineno))
                    pending = _Exit("raise", error)
                else:
                    raise AssertionError("validated statement has no transfer")
                escaped = unwind(pending)
                if escaped is not None:
                    return finish(escaped)
            except _Raised as raised:
                escaped = unwind(_Exit("raise", raised.exception))
                if escaped is not None:
                    return finish(escaped)
        return finish(_Exit("return", None))

    def _expr(self, node, function, env, active, fuel, events, depth):
        fuel.use()
        if node is None:
            return None
        if isinstance(node, ast.Constant):
            return self._check_value(node.value, internal=True)
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            if node.id in self._locals[function]:
                raise _Raised(ProtocolException("UnboundLocalError", None))
            if node.id in self._functions:
                return _FunctionValue(node.id)
            if node.id in EXCEPTION_TYPES:
                return _ExceptionClass(node.id)
            raise _Raised(ProtocolException("NameError", None))
        if isinstance(node, ast.Tuple):
            return self._check_value(tuple(self._expr(x, function, env, active, fuel, events, depth) for x in node.elts), internal=True)
        if isinstance(node, ast.UnaryOp):
            value = self._expr(node.operand, function, env, active, fuel, events, depth)
            if isinstance(node.op, ast.Not):
                return not self._truth(value)
            if type(value) not in (bool, int):
                raise _Raised(ProtocolException("TypeError", None))
            return self._check_value(+value if isinstance(node.op, ast.UAdd) else -value, internal=True)
        if isinstance(node, ast.BoolOp):
            for expression in node.values:
                value = self._expr(expression, function, env, active, fuel, events, depth)
                if isinstance(node.op, ast.And) and not self._truth(value):
                    return value
                if isinstance(node.op, ast.Or) and self._truth(value):
                    return value
            return value
        if isinstance(node, ast.BinOp):
            left = self._expr(node.left, function, env, active, fuel, events, depth)
            right = self._expr(node.right, function, env, active, fuel, events, depth)
            numeric = type(left) in (int, bool) and type(right) in (int, bool)
            if isinstance(node.op, ast.Add) and (numeric or type(left) is type(right) is str or type(left) is type(right) is tuple):
                if type(left) in (str, tuple) and len(left) + len(right) > self.budget.max_value_size:
                    raise _BudgetExceeded("finite value size budget exceeded")
                value = left + right
            elif isinstance(node.op, ast.Sub) and numeric:
                value = left - right
            elif isinstance(node.op, ast.Mult) and numeric:
                if int(left).bit_length() + int(right).bit_length() > self.budget.max_value_size + 1:
                    raise _BudgetExceeded("integer multiplication budget exceeded")
                value = left * right
            elif isinstance(node.op, ast.Mult) and ((type(left) in (str, tuple) and type(right) in (int, bool)) or (type(right) in (str, tuple) and type(left) in (int, bool))):
                seq, count = (left, right) if type(left) in (str, tuple) else (right, left)
                if len(seq) * max(0, int(count)) > self.budget.max_value_size:
                    raise _BudgetExceeded("sequence repetition budget exceeded")
                try:
                    value = seq * count
                except OverflowError:
                    # CPython converts a repeat count to Py_ssize_t even for
                    # an empty sequence or a very large negative multiplier.
                    raise _Raised(ProtocolException("OverflowError", None))
            else:
                raise _Raised(ProtocolException("TypeError", None))
            return self._check_value(value, internal=True)
        if isinstance(node, ast.Compare):
            left = self._expr(node.left, function, env, active, fuel, events, depth)
            for op, right_node in zip(node.ops, node.comparators):
                right = self._expr(right_node, function, env, active, fuel, events, depth)
                if isinstance(op, (ast.Is, ast.IsNot)):
                    same = type(left) in (type(None), bool) and type(left) is type(right) and left == right
                    answer = same if isinstance(op, ast.Is) else not same
                else:
                    # Equality/hash hooks cannot occur for these exact builtin values.
                    if not self._comparable_scalar(left) or not self._comparable_scalar(right):
                        raise _BudgetExceeded("non-scalar equality requires a richer identity domain")
                    try:
                        if isinstance(op, ast.Eq): answer = left == right
                        elif isinstance(op, ast.NotEq): answer = left != right
                        elif isinstance(op, ast.Lt): answer = left < right
                        elif isinstance(op, ast.LtE): answer = left <= right
                        elif isinstance(op, ast.Gt): answer = left > right
                        else: answer = left >= right
                    except TypeError:
                        raise _Raised(ProtocolException("TypeError", None))
                if not answer:
                    return False
                left = right
            return True
        if isinstance(node, ast.Call):
            callee = self._expr(node.func, function, env, active, fuel, events, depth)
            args = [self._expr(x, function, env, active, fuel, events, depth) for x in node.args]
            kwargs = {k.arg: self._expr(k.value, function, env, active, fuel, events, depth) for k in node.keywords}
            if isinstance(callee, _ExceptionClass):
                if kwargs:
                    # NameError/AttributeError have specialized keyword
                    # constructors. Their metadata is outside this domain.
                    raise _BudgetExceeded("exception constructor keyword semantics are unmodeled")
                return ProtocolException(callee.name, tuple(args))
            if not isinstance(callee, _FunctionValue):
                raise _Raised(ProtocolException("TypeError", None))
            if callee.name in self._generators:
                raise _BudgetExceeded("nested generator allocation requires shared heap identity semantics")
            child_env = self._bind_call(self._functions[callee.name], args, kwargs)
            events.append(ProtocolEvent("call", callee.name, node.lineno))
            child = _Machine(callee.name, "running", tuple(sorted(child_env.items())), tuple(reversed(self._functions[callee.name].body)), active)
            outcome, _ = self._execute(child, fuel, events, depth + 1)
            if outcome.kind == "raise":
                raise _Raised(outcome.value)
            return outcome.value
        raise AssertionError("validated expression has no transfer")

    def _bind_call(self, function, args, kwargs):
        positional = [*function.args.posonlyargs, *function.args.args]
        if len(args) > len(positional):
            raise _Raised(ProtocolException("TypeError", None))
        env = {p.arg: value for p, value in zip(positional, args)}
        posonly = {p.arg for p in function.args.posonlyargs}
        names = {p.arg for p in [*positional, *function.args.kwonlyargs]}
        for name, value in kwargs.items():
            if name not in names or name in env or name in posonly:
                raise _Raised(ProtocolException("TypeError", None))
            env[name] = value
        defaults = {p.arg: self._literal(v) for p, v in zip(positional[-len(function.args.defaults):] if function.args.defaults else [], function.args.defaults)}
        defaults.update({p.arg: self._literal(v) for p, v in zip(function.args.kwonlyargs, function.args.kw_defaults) if v is not None})
        for name in names:
            if name not in env:
                if name not in defaults:
                    raise _Raised(ProtocolException("TypeError", None))
                env[name] = defaults[name]
        return env

    @staticmethod
    def _truth(value):
        if isinstance(value, (_FunctionValue, _ExceptionClass, ProtocolException)):
            return True
        return bool(value)  # Only validated exact immutable builtin values reach here.

    @staticmethod
    def _comparable_scalar(value):
        if type(value) in (type(None), bool, int, str):
            return True
        return type(value) is tuple and all(ProtocolModel._comparable_scalar(x) for x in value)

    @staticmethod
    def _matches(kind, handler):
        if handler.type is None:
            return True
        types = handler.type.elts if isinstance(handler.type, ast.Tuple) else (handler.type,)
        caught = {t.id for t in types}
        while True:
            if kind in caught:
                return True
            if kind not in _EXCEPTION_PARENT:
                return False
            kind = _EXCEPTION_PARENT[kind]


def compile_generator_protocol(source, function, *, budget=None):
    try:
        return ProtocolModel(source, function, budget)
    except RecursionError as exc:
        raise UnsupportedGeneratorSource("syntax/model recursion limit exceeded") from exc
