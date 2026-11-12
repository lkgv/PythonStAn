"""Source-derived certificate checker for an explicitly limited Python model.

The model is a closed module of pure functions, with stable function/builtin
bindings, unbounded resources, and no tracing, concurrency or finalizer effects.
It models local bindings, direct calls, identity tests and structured control
flow.  Heap operations and suspension are rejected.  An accepted certificate
overapproximates finite call/raise prefixes by the language ``events*`` and
normal/exception exits with arbitrary return values and no mutation of the
pre-existing modeled user heap (runtime allocation identity is abstracted). It
does NOT certify event order, termination, or arbitrary Python execution.

The candidate controls no source edges or obligations.  The checker reparses
the original source, checks coverage, and checks F(candidate) <= candidate for
the entire recursive vector.  Inference is merely a candidate producer.
"""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import NoReturn

from pythonstan.analysis.contracts.json_data import JsonObject, JsonValue
from pythonstan.frontend.source import source_constant

SCHEMA_VERSION = 1
SEMANTICS_VERSION = "closed-call-effects-v2"
MAX_SOURCE_BYTES = 1_000_000
MAX_CERTIFICATE_FACTS = 100_000
EXCEPTION_TYPES = frozenset(
    {
        "Exception",
        "BaseException",
        "ValueError",
        "TypeError",
        "RuntimeError",
        "KeyError",
        "IndexError",
        "LookupError",
        "ArithmeticError",
        "ZeroDivisionError",
        "AssertionError",
        "AttributeError",
        "NameError",
        "UnboundLocalError",
        "StopIteration",
        "SystemExit",
        "KeyboardInterrupt",
        "NotImplementedError",
        "OverflowError",
        "RecursionError",
    }
)

# Fixed builtin hierarchy, independent of names or objects in the analyzed
# module. Matching may not invoke a user metaclass, descriptor or comparison.
_EXCEPTION_PARENT = {
    "Exception": "BaseException",
    "SystemExit": "BaseException",
    "KeyboardInterrupt": "BaseException",
    "ValueError": "Exception",
    "TypeError": "Exception",
    "RuntimeError": "Exception",
    "LookupError": "Exception",
    "KeyError": "LookupError",
    "IndexError": "LookupError",
    "ArithmeticError": "Exception",
    "ZeroDivisionError": "ArithmeticError",
    "OverflowError": "ArithmeticError",
    "AssertionError": "Exception",
    "AttributeError": "Exception",
    "NameError": "Exception",
    "UnboundLocalError": "NameError",
    "StopIteration": "Exception",
    "NotImplementedError": "RuntimeError",
    "RecursionError": "RuntimeError",
}


def _exception_matches(exception: str, handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else (handler.type,)
    caught: set[str] = set()
    for node in nodes:
        if not isinstance(node, ast.Name):
            _unsupported(node, "handlers require exact unshadowed builtin exception bindings")
        caught.add(node.id)
    while True:
        if exception in caught:
            return True
        if exception not in _EXCEPTION_PARENT:
            return False
        exception = _EXCEPTION_PARENT[exception]


class UnsupportedSource(ValueError):
    """Source is outside the checker's declared semantic fragment."""


class InferenceUnknown(RuntimeError):
    """A resource limit was reached; no certificate has been proved."""


@dataclass(frozen=True)
class FunctionSummary:
    events: frozenset[str] = frozenset()
    normal_exit: bool = False
    exceptions: frozenset[str] = frozenset()
    suspends: bool = False
    heap_effect: str = "unchanged"

    def included_in(self, other: FunctionSummary) -> bool:
        return (
            self.events <= other.events
            and (not self.normal_exit or other.normal_exit)
            and self.exceptions <= other.exceptions
            and (not self.suspends or other.suspends)
            and self.heap_effect == other.heap_effect
        )


@dataclass(frozen=True)
class ProgramCertificate:
    source_sha256: str
    summaries: tuple[tuple[str, FunctionSummary], ...]
    schema_version: int = SCHEMA_VERSION
    semantics_version: str = SEMANTICS_VERSION

    def summary_map(self) -> dict[str, FunctionSummary]:
        return dict(self.summaries)


@dataclass(frozen=True)
class CheckResult:
    status: str
    diagnostics: tuple[str, ...] = ()
    source_sha256: str = ""
    checked_functions: tuple[str, ...] = ()
    obligations_checked: int = 0
    source_coverage: bool = False
    certificate: ProgramCertificate | None = None

    @property
    def certified(self) -> bool:
        return self.status == "accepted" and self.certificate is not None


def source_digest(source: str) -> str:
    try:
        return hashlib.sha256(source.encode("utf-8")).hexdigest()
    except (UnicodeError, AttributeError) as exc:
        raise UnsupportedSource("source must be valid UTF-8 text") from exc


def candidate_json_schema(function_names: Iterable[str] | None = None) -> JsonObject:
    """Wire schema; passing names gives a strict structured-output schema."""
    summary: JsonObject = {
        "type": "object",
        "additionalProperties": False,
        "required": ["events", "normal_exit", "exceptions", "suspends", "heap_effect"],
        "properties": {
            "events": {"type": "array", "items": {"type": "string"}},
            "normal_exit": {"type": "boolean"},
            "exceptions": {"type": "array", "items": {"type": "string"}},
            "suspends": {"type": "boolean", "enum": [False]},
            "heap_effect": {"type": "string", "enum": ["unchanged"]},
        },
    }
    summaries: JsonObject
    if function_names is None:
        summaries = {"type": "object", "additionalProperties": summary}
    else:
        names = sorted(set(function_names))
        properties: JsonObject = {}
        for name in names:
            properties[name] = summary
        summaries = {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": list[JsonValue](names),
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "semantics_version", "source_sha256", "summaries"],
        "properties": {
            "schema_version": {"type": "integer", "enum": [SCHEMA_VERSION]},
            "semantics_version": {"type": "string", "enum": [SEMANTICS_VERSION]},
            "source_sha256": {"type": "string"},
            "summaries": summaries,
        },
    }


def _keys(value: Mapping[str, object], required: set[str], label: str) -> None:
    if set(value) != required:
        raise ValueError(f"{label}: expected exactly {sorted(required)}")


def _object_mapping(value: object, message: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(message)
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(message)
        result[key] = item
    return result


def _string_set(value: object, label: str) -> frozenset[str]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label}: expected an array of strings")
    items: list[str] = []
    for item in value:
        if type(item) is not str:
            raise ValueError(f"{label}: expected an array of strings")
        items.append(item)
    if len(items) > MAX_CERTIFICATE_FACTS:
        raise ValueError(f"{label}: too many facts")
    if len(set(items)) != len(items):
        raise ValueError(f"{label}: duplicate facts")
    return frozenset(items)


def parse_candidate(data: object) -> ProgramCertificate:
    data = _object_mapping(data, "certificate must be an object")
    _keys(
        data, {"schema_version", "semantics_version", "source_sha256", "summaries"}, "certificate"
    )
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    if data["semantics_version"] != SEMANTICS_VERSION:
        raise ValueError("unsupported semantics_version")
    digest = data["source_sha256"]
    if (
        type(digest) is not str
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        raise ValueError("source_sha256 must be a lowercase SHA256 digest")
    items = data["summaries"]
    if not isinstance(items, Mapping) or len(items) > MAX_CERTIFICATE_FACTS:
        raise ValueError("summaries must be a bounded object")
    summaries: list[tuple[str, FunctionSummary]] = []
    facts = 0
    records = _object_mapping(items, "invalid function summary")
    for name, raw_item in records.items():
        if type(name) is not str or not name.isidentifier():
            raise ValueError("invalid function summary")
        item = _object_mapping(raw_item, "invalid function summary")
        _keys(item, {"events", "normal_exit", "exceptions", "suspends", "heap_effect"}, name)
        if type(item["normal_exit"]) is not bool or type(item["suspends"]) is not bool:
            raise ValueError(f"{name}: exit flags must be booleans")
        if item["suspends"] or item["heap_effect"] != "unchanged":
            raise ValueError(f"{name}: unsupported suspension/heap contract")
        events = _string_set(item["events"], f"{name}.events")
        exceptions = _string_set(item["exceptions"], f"{name}.exceptions")
        facts += len(events) + len(exceptions) + 1
        if facts > MAX_CERTIFICATE_FACTS:
            raise ValueError("too many certificate facts")
        summaries.append((name, FunctionSummary(events, item["normal_exit"], exceptions)))
    return ProgramCertificate(digest, tuple(sorted(summaries)))


def certificate_to_dict(certificate: ProgramCertificate) -> JsonObject:
    return {
        "schema_version": certificate.schema_version,
        "semantics_version": certificate.semantics_version,
        "source_sha256": certificate.source_sha256,
        "summaries": {
            name: {
                "events": list[JsonValue](sorted(summary.events)),
                "normal_exit": summary.normal_exit,
                "exceptions": list[JsonValue](sorted(summary.exceptions)),
                "suspends": summary.suspends,
                "heap_effect": summary.heap_effect,
            }
            for name, summary in certificate.summaries
        },
    }


@dataclass(frozen=True)
class _Function:
    node: ast.FunctionDef
    parameters: tuple[str, ...]
    locals: frozenset[str]


@dataclass(frozen=True)
class _Program:
    functions: Mapping[str, _Function]
    node_count: int


def _unsupported(node: ast.AST, message: str) -> NoReturn:
    line: int | str = (
        node.lineno if isinstance(node, (ast.expr, ast.stmt, ast.ExceptHandler)) else "?"
    )
    raise UnsupportedSource(f"line {line}: {message}")


def _load_program(source: str) -> _Program:
    if type(source) is not str:
        raise UnsupportedSource("source must be text")
    try:
        encoded = source.encode("utf-8")
    except UnicodeError as exc:
        raise UnsupportedSource("source must be valid UTF-8 text") from exc
    if len(encoded) > MAX_SOURCE_BYTES:
        raise InferenceUnknown("source byte budget exhausted")
    try:
        module = ast.parse(source)
        # Parsing alone accepts some invalid bindings/control flow. Compilation
        # validates Python's symbol-table rules; the code object is never run.
        compile(module, "<certified-source>", "exec", dont_inherit=True)
    except (SyntaxError, ValueError) as exc:
        raise UnsupportedSource(f"invalid Python source: {exc}") from exc
    functions: dict[str, _Function] = {}
    body = module.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and type(source_constant(body[0].value)) is str
    ):
        body = body[1:]
    for node in body:
        if not isinstance(node, ast.FunctionDef):
            _unsupported(
                node, "closed modules contain only function definitions and an optional docstring"
            )
        if node.name in functions or node.name in EXCEPTION_TYPES or node.name == "__builtins__":
            _unsupported(node, "duplicate or reserved function binding")
        args = node.args
        params = tuple(arg.arg for arg in (*args.posonlyargs, *args.args))
        if (
            node.decorator_list
            or node.returns
            or node.type_params
            or args.defaults
            or args.kw_defaults
            or args.kwonlyargs
            or args.vararg
            or args.kwarg
            or any(arg.annotation for arg in (*args.posonlyargs, *args.args))
        ):
            _unsupported(
                node,
                "decorators, annotations, defaults, variadic and keyword-only parameters are unsupported",
            )
        if len(params) != len(set(params)):
            _unsupported(node, "duplicate parameters")
        local_names = set(params)
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                local_names.add(child.id)
            elif isinstance(child, ast.ExceptHandler) and child.name is not None:
                local_names.add(child.name)
        functions[node.name] = _Function(node, params, frozenset(local_names))
    if not functions:
        raise UnsupportedSource("at least one source function is required")
    validator = _Validator(functions)
    for function in functions.values():
        validator.function = function
        validator.statements(function.node.body, loop_depth=0)
    return _Program(functions, sum(1 for _ in ast.walk(module)))


class _Validator:
    def __init__(self, functions: Mapping[str, _Function]) -> None:
        self.functions = functions
        self.function: _Function

    def value(self, node: ast.AST, allow_calls: bool = True) -> None:
        if isinstance(node, ast.Constant):
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in self.function.locals and node.id not in self.functions:
                _unsupported(node, f"unmodeled global name {node.id!r}")
            return
        if isinstance(node, ast.Call) and allow_calls:
            if (
                not isinstance(node.func, ast.Name)
                or node.func.id not in self.functions
                or node.func.id in self.function.locals
            ):
                _unsupported(node, "calls require an unshadowed closed-module function binding")
            if node.keywords or any(isinstance(arg, ast.Starred) for arg in node.args):
                _unsupported(node, "keyword and starred arguments are unsupported")
            if len(node.args) != len(self.functions[node.func.id].parameters):
                _unsupported(node, "call arity mismatch")
            for arg in node.args:
                self.value(arg)
            return
        _unsupported(node, f"unsupported expression {type(node).__name__}")

    def condition(self, node: ast.AST) -> None:
        if isinstance(node, ast.Constant) and type(source_constant(node)) is bool:
            return
        if isinstance(node, ast.Compare) and all(
            isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops
        ):
            for value in (node.left, *node.comparators):
                self.value(value, allow_calls=False)
            return
        if isinstance(node, ast.BoolOp):
            for value in node.values:
                self.condition(value)
            return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            self.condition(node.operand)
            return
        _unsupported(
            node,
            "conditions require Boolean literals or pure identity tests; arbitrary truthiness is unsupported",
        )

    def statements(self, nodes: Iterable[ast.stmt], loop_depth: int) -> None:
        for node in nodes:
            if isinstance(node, ast.Pass):
                continue
            if isinstance(node, ast.Assign):
                if any(not isinstance(t, ast.Name) for t in node.targets):
                    _unsupported(node, "assignment targets must be local names")
                self.value(node.value)
            elif isinstance(node, ast.Expr):
                self.value(node.value)
            elif isinstance(node, ast.Return):
                if node.value is not None:
                    self.value(node.value)
            elif isinstance(node, ast.If):
                self.condition(node.test)
                self.statements(node.body, loop_depth)
                self.statements(node.orelse, loop_depth)
            elif isinstance(node, ast.While):
                self.condition(node.test)
                self.statements(node.body, loop_depth + 1)
                self.statements(node.orelse, loop_depth)
            elif isinstance(node, ast.Try):
                self.statements(node.body, loop_depth)
                for handler in node.handlers:
                    types = (
                        handler.type.elts
                        if isinstance(handler.type, ast.Tuple)
                        else (handler.type,)
                    )
                    for exception in types:
                        if exception is None:
                            continue
                        if (
                            not isinstance(exception, ast.Name)
                            or exception.id not in EXCEPTION_TYPES
                            or exception.id in self.function.locals
                            or exception.id in self.functions
                        ):
                            _unsupported(
                                exception,
                                "handlers require exact unshadowed builtin exception bindings",
                            )
                    self.statements(handler.body, loop_depth)
                self.statements(node.orelse, loop_depth)
                self.statements(node.finalbody, loop_depth)
            elif isinstance(node, (ast.Break, ast.Continue)):
                if not loop_depth:
                    _unsupported(node, "loop control outside a loop")
            elif isinstance(node, ast.Raise):
                if node.cause is not None:
                    _unsupported(node, "explicit exception chaining is unsupported")
                name = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                if (
                    not isinstance(name, ast.Name)
                    or name.id not in EXCEPTION_TYPES
                    or name.id in self.function.locals
                    or name.id in self.functions
                ):
                    _unsupported(node, "raise requires an exact modeled builtin exception binding")
                if isinstance(node.exc, ast.Call):
                    if node.exc.keywords or any(
                        isinstance(arg, ast.Starred) for arg in node.exc.args
                    ):
                        _unsupported(node, "exception keyword/starred arguments are unsupported")
                    for arg in node.exc.args:
                        self.value(arg)
            else:
                _unsupported(node, f"unsupported statement {type(node).__name__}")


@dataclass(frozen=True)
class _Bindings:
    must: frozenset[str]
    may: frozenset[str]

    @staticmethod
    def join(left: _Bindings | None, right: _Bindings | None) -> _Bindings | None:
        if left is None:
            return right
        if right is None:
            return left
        return _Bindings(left.must & right.must, left.may | right.may)

    def bind(self, names: Iterable[str]) -> _Bindings:
        bound = frozenset(names)
        return _Bindings(self.must | bound, self.may | bound)


@dataclass
class _Flow:
    """Prefix events plus binding states indexed by pending exit kind.

    A raised event survives catching or overriding its exceptional exit. The
    state at the exit is retained so a handler/finally cannot assume bindings
    from a source suffix that was never evaluated.
    """

    normal: _Bindings | None
    events: set[str] = field(default_factory=set)
    returns: _Bindings | None = None
    breaks: _Bindings | None = None
    continues: _Bindings | None = None
    exceptions: dict[str, _Bindings] = field(default_factory=dict)

    def add_exits(self, other: _Flow) -> None:
        self.events.update(other.events)
        self.returns = _Bindings.join(self.returns, other.returns)
        self.breaks = _Bindings.join(self.breaks, other.breaks)
        self.continues = _Bindings.join(self.continues, other.continues)
        for name, bindings in other.exceptions.items():
            self.exception_exit(name, bindings)

    def exception_exit(self, name: str, bindings: _Bindings) -> None:
        joined = _Bindings.join(self.exceptions.get(name), bindings)
        if joined is None:
            raise AssertionError("A reached exception exit requires a binding state")
        self.exceptions[name] = joined

    def raises(self, name: str, bindings: _Bindings) -> None:
        self.events.add("raise:" + name)
        self.exception_exit(name, bindings)

    def merge(self, other: _Flow) -> _Flow:
        result = _Flow(_Bindings.join(self.normal, other.normal))
        result.add_exits(self)
        result.add_exits(other)
        return result

    def delete_local(self, name: str) -> None:
        # Python clears an `except E as name` target on every handler exit,
        # even when it replaced a binding that existed before the handler.
        def delete_present(bindings: _Bindings) -> _Bindings:
            return _Bindings(bindings.must - {name}, bindings.may - {name})

        def delete(bindings: _Bindings | None) -> _Bindings | None:
            return None if bindings is None else delete_present(bindings)

        self.normal = delete(self.normal)
        self.returns = delete(self.returns)
        self.breaks = delete(self.breaks)
        self.continues = delete(self.continues)
        self.exceptions = {
            kind: delete_present(bindings) for kind, bindings in self.exceptions.items()
        }

    def pending_exits(self) -> tuple[tuple[str, _Bindings | None], ...]:
        return (
            ("normal", self.normal),
            ("returns", self.returns),
            ("breaks", self.breaks),
            ("continues", self.continues),
        )

    def restore_pending(self, tag: str, bindings: _Bindings) -> None:
        match tag:
            case "normal":
                self.normal = _Bindings.join(self.normal, bindings)
            case "returns":
                self.returns = _Bindings.join(self.returns, bindings)
            case "breaks":
                self.breaks = _Bindings.join(self.breaks, bindings)
            case "continues":
                self.continues = _Bindings.join(self.continues, bindings)
            case _:
                self.exception_exit(tag, bindings)


class _Transfer:
    def __init__(
        self, program: _Program, summaries: Mapping[str, FunctionSummary], step_budget: int
    ) -> None:
        self.program = program
        self.summaries = summaries
        self.remaining = step_budget
        self.steps = 0
        self.function: _Function

    def tick(self) -> None:
        self.steps += 1
        self.remaining -= 1
        if self.remaining < 0:
            raise InferenceUnknown("source transfer budget exhausted")

    def value(self, node: ast.AST, bindings: _Bindings) -> _Flow:
        self.tick()
        flow = _Flow(bindings)
        if isinstance(node, ast.Name) and node.id in self.function.locals:
            if node.id not in bindings.must:
                flow.raises("UnboundLocalError", bindings)
            if node.id not in bindings.may:
                flow.normal = None
        elif isinstance(node, ast.Call):
            for arg in node.args:
                if flow.normal is None:
                    break
                arg_flow = self.value(arg, flow.normal)
                flow.add_exits(arg_flow)
                flow.normal = arg_flow.normal
            if flow.normal is not None:
                if not isinstance(node.func, ast.Name):
                    _unsupported(node, "calls require an unshadowed closed-module function binding")
                name = node.func.id
                flow.events.add("call:" + name)
                summary = self.summaries[name]
                flow.events.update(summary.events)
                for exception in summary.exceptions:
                    flow.exception_exit(exception, flow.normal)
                if not summary.normal_exit:
                    flow.normal = None
        return flow

    def condition(self, node: ast.AST, bindings: _Bindings) -> tuple[_Flow, frozenset[bool]]:
        self.tick()
        flow = _Flow(bindings)
        if isinstance(node, ast.Constant):
            constant = source_constant(node)
            if not isinstance(constant, bool):
                _unsupported(
                    node,
                    "conditions require Boolean literals or pure identity tests; arbitrary truthiness is unsupported",
                )
            return flow, frozenset({constant})
        if isinstance(node, ast.UnaryOp):
            flow, truth = self.condition(node.operand, bindings)
            return flow, frozenset(not value for value in truth)
        if isinstance(node, ast.BoolOp):
            possible = frozenset({isinstance(node.op, ast.And)})
            proceed_on = isinstance(node.op, ast.And)
            for part in node.values:
                if proceed_on not in possible or flow.normal is None:
                    break
                part_flow, truth = self.condition(part, flow.normal)
                flow.add_exits(part_flow)
                halted = possible - {proceed_on}
                possible = halted | truth
                if part_flow.normal is None and not halted:
                    flow.normal = None
            return flow, possible if flow.normal is not None else frozenset()
        # Chained identity comparisons short-circuit from left to right.
        if not isinstance(node, ast.Compare):
            _unsupported(
                node,
                "conditions require Boolean literals or pure identity tests; arbitrary truthiness is unsupported",
            )
        values = (node.left, *node.comparators)
        left_flow = self.value(values[0], bindings)
        flow.add_exits(left_flow)
        flow.normal = left_flow.normal
        possible = frozenset({True})
        for left, op, right in zip(values, node.ops, values[1:], strict=False):
            if True not in possible or flow.normal is None:
                break
            right_flow = self.value(right, flow.normal)
            flow.add_exits(right_flow)
            halted = possible - {True}
            if right_flow.normal is None:
                possible = halted
                if not halted:
                    flow.normal = None
                break
            known: bool | None = None
            if isinstance(left, ast.Name) and isinstance(right, ast.Name) and left.id == right.id:
                known = True
            elif (
                isinstance(left, ast.Constant)
                and isinstance(right, ast.Constant)
                and (source_constant(left) is None or type(source_constant(left)) is bool)
                and (source_constant(right) is None or type(source_constant(right)) is bool)
            ):
                known = source_constant(left) is source_constant(right)
            truth = (
                frozenset({True, False})
                if known is None
                else frozenset({known != isinstance(op, ast.IsNot)})
            )
            possible = halted | truth
        return flow, possible if flow.normal is not None else frozenset()

    def statements(self, nodes: Iterable[ast.stmt], bindings: _Bindings) -> _Flow:
        flow = _Flow(bindings)
        for node in nodes:
            if flow.normal is None:
                break
            part = self.statement(node, flow.normal)
            flow.add_exits(part)
            flow.normal = part.normal
        return flow

    def cleanup(self, flow: _Flow, nodes: Iterable[ast.stmt]) -> _Flow:
        """Execute finally for each reached pending exit, never for divergence.

        Only normal cleanup restores a pending exit. An abrupt cleanup exit
        replaces it. Prefix events from the body and from all cleanup runs
        remain, including runs with no finite exit.
        """
        result = _Flow(None, events=set(flow.events))
        pending = list(flow.pending_exits())
        pending.extend((name, bindings) for name, bindings in flow.exceptions.items())
        for tag, bindings in pending:
            if bindings is None:
                continue
            cleaned = self.statements(nodes, bindings)
            result.add_exits(cleaned)
            if cleaned.normal is not None:
                result.restore_pending(tag, cleaned.normal)
        return result

    def try_statement(self, node: ast.Try, bindings: _Bindings) -> _Flow:
        body = self.statements(node.body, bindings)
        result = _Flow(
            None,
            events=set(body.events),
            returns=body.returns,
            breaks=body.breaks,
            continues=body.continues,
        )
        # Else is outside the handlers' protected region. Handler exceptions
        # and else exceptions propagate directly to the pending-finally join.
        if body.normal is not None:
            result = result.merge(self.statements(node.orelse, body.normal))
        for exception, state in body.exceptions.items():
            for handler in node.handlers:
                if _exception_matches(exception, handler):
                    entry = state.bind((handler.name,)) if handler.name else state
                    handled = self.statements(handler.body, entry)
                    if handler.name:
                        handled.delete_local(handler.name)
                    result = result.merge(handled)
                    break
            else:
                result.exception_exit(exception, state)
        return self.cleanup(result, node.finalbody) if node.finalbody else result

    def statement(self, node: ast.stmt, bindings: _Bindings) -> _Flow:
        self.tick()
        if isinstance(node, ast.Pass):
            return _Flow(bindings)
        if isinstance(node, ast.Assign):
            flow = self.value(node.value, bindings)
            if flow.normal is not None:
                names: list[str] = []
                for target in node.targets:
                    if not isinstance(target, ast.Name):
                        _unsupported(target, "assignment targets must be local names")
                    names.append(target.id)
                flow.normal = flow.normal.bind(names)
            return flow
        if isinstance(node, ast.Expr):
            return self.value(node.value, bindings)
        if isinstance(node, ast.Return):
            flow = self.value(node.value, bindings) if node.value is not None else _Flow(bindings)
            flow.returns = _Bindings.join(flow.returns, flow.normal)
            flow.normal = None
            return flow
        if isinstance(node, ast.Raise):
            flow = _Flow(bindings)
            if isinstance(node.exc, ast.Call):
                for arg in node.exc.args:
                    if flow.normal is None:
                        break
                    part = self.value(arg, flow.normal)
                    flow.add_exits(part)
                    flow.normal = part.normal
                if not isinstance(node.exc.func, ast.Name):
                    _unsupported(node, "raise requires an exact modeled builtin exception binding")
                name = node.exc.func.id
            else:
                if not isinstance(node.exc, ast.Name):
                    _unsupported(node, "raise requires an exact modeled builtin exception binding")
                name = node.exc.id
            if flow.normal is not None:
                flow.raises(name, flow.normal)
            flow.normal = None
            return flow
        if isinstance(node, ast.Break):
            return _Flow(None, breaks=bindings)
        if isinstance(node, ast.Continue):
            return _Flow(None, continues=bindings)
        if isinstance(node, ast.If):
            condition, truth = self.condition(node.test, bindings)
            branches = _Flow(None)
            if condition.normal is not None:
                if True in truth:
                    branches = branches.merge(self.statements(node.body, condition.normal))
                if False in truth:
                    branches = branches.merge(self.statements(node.orelse, condition.normal))
            branches.add_exits(condition)
            return branches
        if isinstance(node, ast.Try):
            return self.try_statement(node, bindings)
        # The validator has established that the remaining node is While.
        if not isinstance(node, ast.While):
            _unsupported(node, f"unsupported statement {type(node).__name__}")
        invariant = bindings
        accumulated = _Flow(None)
        break_states = None
        while True:
            condition, truth = self.condition(node.test, invariant)
            accumulated.add_exits(condition)
            body = _Flow(None)
            if condition.normal is not None and True in truth:
                body = self.statements(node.body, condition.normal)
                accumulated.events.update(body.events)
                accumulated.returns = _Bindings.join(accumulated.returns, body.returns)
                for exception, state in body.exceptions.items():
                    accumulated.exception_exit(exception, state)
                break_states = _Bindings.join(break_states, body.breaks)
            back = _Bindings.join(body.normal, body.continues)
            next_invariant = _Bindings.join(bindings, back)
            if next_invariant == invariant:
                break
            if next_invariant is None:
                raise AssertionError("A loop header retains its initial reached binding state")
            invariant = next_invariant
        fallthrough = _Flow(None)
        if condition.normal is not None and False in truth:
            fallthrough = self.statements(node.orelse, condition.normal)
        fallthrough.add_exits(accumulated)
        fallthrough.normal = _Bindings.join(fallthrough.normal, break_states)
        return fallthrough

    def function_summary(self, function: _Function) -> FunctionSummary:
        self.function = function
        bindings = _Bindings(frozenset(function.parameters), frozenset(function.parameters))
        flow = self.statements(function.node.body, bindings)
        return FunctionSummary(
            frozenset(flow.events),
            flow.returns is not None or flow.normal is not None,
            frozenset(flow.exceptions),
        )


def _apply(
    program: _Program, summaries: Mapping[str, FunctionSummary], budget: int
) -> tuple[dict[str, FunctionSummary], int]:
    transfer = _Transfer(program, summaries, budget)
    result = {
        name: transfer.function_summary(function) for name, function in program.functions.items()
    }
    return result, transfer.steps


def infer_candidate(source: str, *, max_iterations: int = 1000) -> ProgramCertificate:
    """Finite ascending inference; success still requires independent checking."""
    if type(max_iterations) is not int or max_iterations <= 0:
        raise InferenceUnknown("inference iteration budget exhausted")
    try:
        program = _load_program(source)
        summaries = {name: FunctionSummary() for name in program.functions}
        for _ in range(max_iterations):
            next_summaries, _steps = _apply(program, summaries, max(1000, program.node_count * 100))
            if next_summaries == summaries:
                return ProgramCertificate(source_digest(source), tuple(sorted(summaries.items())))
            summaries = next_summaries
    except RecursionError as exc:
        raise InferenceUnknown("checker recursion budget exhausted") from exc
    raise InferenceUnknown("inference iteration budget exhausted")


def check_source(source: str, candidate: object, *, max_iterations: int = 1000) -> CheckResult:
    """Check a complete source-bound vector without trusting its producer."""
    try:
        digest = source_digest(source) if type(source) is str else ""
    except UnsupportedSource as exc:
        return CheckResult("unsupported", (str(exc),))
    if type(max_iterations) is not int or max_iterations <= 0:
        return CheckResult("unknown", ("checking budget exhausted",), digest)
    try:
        # Round-tripping also defends against mutable or malformed direct class construction.
        cert = parse_candidate(
            certificate_to_dict(candidate)
            if isinstance(candidate, ProgramCertificate)
            else candidate
        )
    except (ValueError, TypeError, AttributeError) as exc:
        return CheckResult("rejected", (f"malformed certificate: {exc}",), digest)
    if cert.source_sha256 != digest:
        return CheckResult("rejected", ("certificate source digest mismatch",), digest)
    try:
        program = _load_program(source)
        names = tuple(sorted(program.functions))
        proposed = cert.summary_map()
        if set(proposed) != set(program.functions):
            return CheckResult(
                "rejected",
                ("certificate must cover exactly all source functions",),
                digest,
                names,
                source_coverage=True,
            )
        alphabet = {"call:" + name for name in names} | {
            "raise:" + name for name in EXCEPTION_TYPES
        }
        for name, summary in proposed.items():
            if not summary.events <= alphabet or not summary.exceptions <= EXCEPTION_TYPES:
                return CheckResult(
                    "rejected",
                    (f"{name}: facts outside the modeled alphabet",),
                    digest,
                    names,
                    source_coverage=True,
                )
        inferred, steps = _apply(program, proposed, max_iterations * 1000)
        diagnostics = []
        for name in names:
            actual, claim = inferred[name], proposed[name]
            if not actual.included_in(claim):
                diagnostics.append(f"{name}: source-derived post-fixpoint obligation failed")
                if actual.events - claim.events:
                    diagnostics.append(
                        f"{name}: missing prefix events {sorted(actual.events - claim.events)}"
                    )
                if actual.exceptions - claim.exceptions:
                    diagnostics.append(
                        f"{name}: missing exception exits {sorted(actual.exceptions - claim.exceptions)}"
                    )
                if actual.normal_exit and not claim.normal_exit:
                    diagnostics.append(f"{name}: missing normal exit")
        if diagnostics:
            return CheckResult("rejected", tuple(diagnostics), digest, names, steps, True)
        return CheckResult("accepted", (), digest, names, steps, True, cert)
    except UnsupportedSource as exc:
        return CheckResult("unsupported", (str(exc),), digest)
    except (InferenceUnknown, RecursionError) as exc:
        return CheckResult("unknown", (str(exc),), digest)
