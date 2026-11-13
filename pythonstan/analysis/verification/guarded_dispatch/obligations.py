"""Trusted source-derived obligations for the finite relational profile.

Symbolic payloads are tokens, never concrete executions or samples. All source
functions/statements are checked; a proposal cannot choose the relevant vector.
"""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from pythonstan.analysis.contracts import SemanticIdentity, VersionedDependency, value_snapshot
from pythonstan.analysis.contracts.cost_model import (
    STRUCTURAL_OPERATIONS_PER_TICK,
    StructuralWorkMeter,
    visit_json_text,
)
from pythonstan.analysis.contracts.identity import stable_digest
from pythonstan.analysis.contracts.json_data import FrozenJsonObject
from pythonstan.analysis.contracts.resources import RunLedger, WorkReservation

from .language import PROFILE

MODEL_VERSION = "guarded-dispatch-exact-dict-symbolic-payload-1"
MODEL_DIGEST = hashlib.sha256(MODEL_VERSION.encode()).hexdigest()


class UnsupportedSource(ValueError):
    pass


class VerificationUnknown(RuntimeError):
    pass


@dataclass(frozen=True)
class VerificationBudget:
    max_source_bytes: int = 1_000_000
    max_nodes: int = 100_000
    max_work: int = 300_000
    max_functions: int = 4096
    max_keys: int = 4096

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (
                self.max_source_bytes,
                self.max_nodes,
                self.max_work,
                self.max_functions,
                self.max_keys,
            )
        ):
            raise ValueError("verification budgets must be nonnegative integers")


class WorkMeter:
    def __init__(
        self,
        budget: VerificationBudget,
        *,
        resource_ledger: RunLedger | None = None,
        work_reservation: WorkReservation | None = None,
    ) -> None:
        self.budget = budget
        self.work = 0
        self.counts: dict[str, int] = {}
        self.resource_ledger = resource_ledger
        self.work_reservation = work_reservation

    def _admit(self, kind: str, count: int) -> None:
        if self.work_reservation is not None:
            if self.resource_ledger is None:
                raise VerificationUnknown("checker grant has no request ledger")
            try:
                self.work_reservation.consume(count, owner=self.resource_ledger)
            except ValueError as error:
                # A host may invalidate a finite readonly worker's grant while
                # checking is in progress. This is resource unavailability,
                # never a malformed candidate or a late refundable grant.
                raise VerificationUnknown("checker work grant unavailable") from error

    def tick(self, kind: str, count: int = 1) -> None:
        if type(count) is not int or count < 0:
            raise ValueError("verification work must be a nonnegative integer")
        if self.work + count > self.budget.max_work:
            available = max(0, self.budget.max_work - self.work)
            self._admit(kind, available)
            self.work += available
            self.counts[kind] = self.counts.get(kind, 0) + available
            raise VerificationUnknown("verification work budget exhausted during " + kind)
        self._admit(kind, count)
        self.work += count
        self.counts[kind] = self.counts.get(kind, 0) + count

    @contextmanager
    def traversal(self, kind: str) -> Iterator[Callable[[int], None]]:
        """Admit raw batches atomically, retaining every real traversal's fuel."""
        if self.resource_ledger is None:
            meter = StructuralWorkMeter(
                lambda amount, category: self.tick(category, amount),
                lambda category, amount: None,
                kind,
            )
        else:
            meter = self.resource_ledger.structural_meter(
                kind, charge=lambda amount: self.tick(kind, amount)
            )

        def visit(count: int = 1) -> None:
            if type(count) is not int or count < 0:
                raise ValueError("verification visits must be nonnegative")
            if count == 0:
                meter.visit(0)
                return
            # Every admission requests at most one tick. A local cap therefore
            # rejects atomically even when an input decoder prepays many raw
            # visits; rejected StructuralWorkMeter charges never partially debit.
            while count >= STRUCTURAL_OPERATIONS_PER_TICK:
                meter.visit(STRUCTURAL_OPERATIONS_PER_TICK)
                count -= STRUCTURAL_OPERATIONS_PER_TICK
            if count:
                meter.visit(count)

        try:
            yield visit
        finally:
            meter.close()

    def checkpoint(self, *, force: bool = False) -> None:
        if self.resource_ledger is not None:
            self.resource_ledger.checkpoint(force=force)

    def admit_json(self, value: object, kind: str) -> None:
        """Prepay a trusted, already prepared record before atomic JSON hashing."""
        with self.traversal(kind) as visit:

            def walk(current: object, depth: int = 0) -> None:
                if depth > 128:
                    raise VerificationUnknown("verification JSON nesting budget")
                if isinstance(current, str):
                    visit_json_text(current, visit)
                    return
                visit(1)
                if isinstance(current, Mapping):
                    rows: Mapping[object, object] = current
                    for key, item in rows.items():
                        if not isinstance(key, str):
                            raise TypeError("verification JSON keys must be strings")
                        visit_json_text(key, visit)
                        walk(item, depth + 1)
                elif isinstance(current, (list, tuple)):
                    items: tuple[object, ...] | list[object] = current
                    for item in items:
                        walk(item, depth + 1)
                elif current is not None and not isinstance(current, (bool, int, float)):
                    raise TypeError("verification JSON must contain scalar data")

            walk(value)


@contextmanager
def verification_meter(
    budget: VerificationBudget,
    *,
    resource_ledger: RunLedger | None = None,
    work_reservation: WorkReservation | None = None,
) -> Iterator[WorkMeter]:
    """One registered finite CPU grant; the issuer owns hosted settlement."""
    if work_reservation is not None and resource_ledger is None:
        raise VerificationUnknown("checker grant requires its request ledger")
    ledger = resource_ledger or RunLedger(budget.max_work, 0, 0)
    owned = work_reservation is None
    grant = (
        ledger.reserve_work(
            min(budget.max_work, ledger.remaining_work), category="guarded_checker_cpu"
        )
        if work_reservation is None
        else work_reservation
    )
    try:
        if grant.category != "guarded_checker_cpu":
            raise VerificationUnknown("checker grant purpose mismatch")
        try:
            grant.consume(0, owner=ledger)
        except ValueError as error:
            raise VerificationUnknown("checker grant authority unavailable") from error
        ledger.checkpoint(force=True)
        meter = WorkMeter(budget, resource_ledger=ledger, work_reservation=grant)
        yield meter
    finally:
        if owned and grant.status == "reserved":
            grant.settle(grant.observed)


@dataclass(frozen=True)
class HandlerFact:
    name: str
    parameter: str
    relation: str
    allocation: str | None
    digest: str


@dataclass(frozen=True)
class SourceCall:
    target: str
    point: str
    key: str
    payload: FrozenJsonObject
    reachable: bool = True


@dataclass(frozen=True)
class ProgramFacts:
    identity: SemanticIdentity
    dispatch: str
    dispatch_parameters: tuple[str, ...]
    table_name: str
    table: tuple[tuple[str, str], ...]
    table_digest: str
    handlers: tuple[HandlerFact, ...]
    calls: tuple[SourceCall, ...]
    source_nodes: int


def _dependency_key(dependency: VersionedDependency) -> tuple[str, str]:
    return dependency.kind, dependency.identity


def _literal(node: ast.Constant) -> object:
    value: object = node.value
    return value


def _digest(node: ast.AST, meter: WorkMeter) -> str:
    with meter.traversal("source_ir_digest") as visit:
        for current in ast.walk(node):
            visit(1)
            fields: Mapping[str, object] = vars(current)
            for name in current._fields:
                value = fields.get(name)
                if isinstance(value, str):
                    visit_json_text(value, visit)
                elif isinstance(value, bytes):
                    visit(1 + (len(value) + 1023) // 1024)
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()


def _point(source_digest: str, node: ast.expr | ast.stmt) -> str:
    return f"{source_digest[:16]}:{node.lineno}:{node.col_offset}:{type(node).__name__}"


def _body(function: ast.FunctionDef, meter: WorkMeter) -> list[ast.stmt]:
    with meter.traversal("function_body_copy") as visit:
        visit(1 + len(function.body))
    body = list(function.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and type(_literal(body[0].value)) is str
    ):
        body.pop(0)
    return body


def _signature(function: ast.FunctionDef, meter: WorkMeter) -> tuple[str, ...]:
    meter.tick("function_signature")
    args = function.args
    if (
        function.decorator_list
        or function.returns is not None
        or function.type_params
        or args.defaults
        or args.kw_defaults
        or args.kwonlyargs
        or args.posonlyargs
        or args.vararg
        or args.kwarg
    ):
        raise UnsupportedSource(
            "decorators/defaults/annotations/advanced binding are outside guarded-dispatch-v1"
        )
    with meter.traversal("function_parameters") as visit:
        for arg in args.args:
            visit(1)
            if arg.annotation is not None:
                raise UnsupportedSource(
                    "decorators/defaults/annotations/advanced binding are outside guarded-dispatch-v1"
                )
    with meter.traversal("signature_name_copy") as visit:
        visit(1 + len(args.args))
    names = tuple(arg.arg for arg in args.args)
    if len(set(names)) != len(names) or "__debug__" in names:
        raise UnsupportedSource("duplicate parameters")
    return names


def _handler(function: ast.FunctionDef, source_digest: str, meter: WorkMeter) -> HandlerFact:
    parameters = _signature(function, meter)
    if len(parameters) != 1:
        raise UnsupportedSource("handler must have one symbolic payload parameter")
    parameter = parameters[0]
    env = {parameter: "input"}
    returned = None
    allocation = None
    for statement in _body(function, meter):
        meter.tick("handler_statements")
        if returned is not None:
            raise UnsupportedSource("source after handler return is outside the finite profile")
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and isinstance(statement.value, ast.Name)
        ):
            if statement.value.id not in env:
                raise UnsupportedSource("handler reads an unbound/nonlocal name")
            env[statement.targets[0].id] = env[statement.value.id]
        elif isinstance(statement, ast.Return):
            value = statement.value
            if isinstance(value, ast.Name) and env.get(value.id) == "input":
                returned = "input"
            elif (
                isinstance(value, ast.Dict)
                and len(value.keys) == 1
                and isinstance(value.keys[0], ast.Constant)
                and _literal(value.keys[0]) == "value"
                and isinstance(value.values[0], ast.Name)
                and env.get(value.values[0].id) == "input"
            ):
                returned = "fresh_box"
                allocation = _point(source_digest, value)
            else:
                raise UnsupportedSource(
                    "handler return must be payload or a fresh {'value':payload} box"
                )
        else:
            raise UnsupportedSource(
                "handler mutation/control/call/suspension is outside the finite profile"
            )
    if returned is None:
        raise UnsupportedSource("handler must return explicitly")
    return HandlerFact(function.name, parameter, returned, allocation, _digest(function, meter))


def _dispatch(function: ast.FunctionDef, meter: WorkMeter) -> tuple[str, ...]:
    parameters = _signature(function, meter)
    if len(parameters) != 3:
        raise UnsupportedSource("dispatch must have table,key,payload parameters")
    env = dict(zip(parameters, ("table", "key", "payload"), strict=False))

    def expression(node: ast.expr | None) -> str:
        if isinstance(node, ast.Name) and node.id in env:
            return env[node.id]
        if (
            isinstance(node, ast.Subscript)
            and expression(node.value) == "table"
            and expression(node.slice) == "key"
        ):
            return "selected_handler"
        if (
            isinstance(node, ast.Call)
            and not node.keywords
            and len(node.args) == 1
            and expression(node.func) == "selected_handler"
            and expression(node.args[0]) == "payload"
        ):
            return "dispatch_result"
        raise UnsupportedSource("dispatch has an unmodeled operation")

    returned = False
    for statement in _body(function, meter):
        meter.tick("dispatch_statements")
        if returned:
            raise UnsupportedSource("source after dispatch return is outside the profile")
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            env[statement.targets[0].id] = expression(statement.value)
        elif isinstance(statement, ast.Return) and expression(statement.value) == "dispatch_result":
            returned = True
        else:
            raise UnsupportedSource("dispatch control/mutation is outside the finite profile")
    if not returned:
        raise UnsupportedSource("dispatch must return its selected handler result")
    return parameters


def generate_obligations(
    source: object,
    *,
    policy_digest: str = "default-policy",
    state_version: str = "0",
    budget: VerificationBudget | None = None,
    meter: WorkMeter | None = None,
    resource_ledger: RunLedger | None = None,
    work_reservation: WorkReservation | None = None,
) -> ProgramFacts:
    budget = budget or VerificationBudget()
    if meter is None:
        with verification_meter(
            budget, resource_ledger=resource_ledger, work_reservation=work_reservation
        ) as local_meter:
            return generate_obligations(
                source,
                policy_digest=policy_digest,
                state_version=state_version,
                budget=budget,
                meter=local_meter,
            )
    if not isinstance(source, str) or type(source) is not str:
        raise UnsupportedSource("source must be text")
    byte_count = 0
    with meter.traversal("source_byte_scan") as visit:
        visit(1)
        for character in source:
            visit(1)
            codepoint = ord(character)
            byte_count += (
                1
                if codepoint < 0x80
                else 2
                if codepoint < 0x800
                else 3
                if codepoint < 0x10000
                else 4
            )
            if byte_count > budget.max_source_bytes:
                raise VerificationUnknown("source byte budget exhausted")
    if budget.max_nodes == 0:
        raise VerificationUnknown("source node budget exhausted")
    meter.tick("source_parser")
    try:
        tree = ast.parse(source, mode="exec", type_comments=True, feature_version=(3, 12))
    except (SyntaxError, ValueError) as error:
        raise UnsupportedSource(str(error)) from error
    except RecursionError as error:
        raise VerificationUnknown("source parser recursion budget") from error
    meter.checkpoint(force=True)
    nodes = 1
    meter.tick("source_nodes")
    pending: list[ast.AST] = [tree]
    while pending:
        current = pending.pop()
        for child in ast.iter_child_nodes(current):
            if nodes >= budget.max_nodes:
                raise VerificationUnknown("source node budget exhausted")
            meter.tick("source_nodes")
            nodes += 1
            pending.append(child)
    # AST parsing alone accepts a few invalid binding/placement forms.
    # Compile only for those static checks; never execute the code object.
    with meter.traversal("source_validity_compile") as visit:
        visit(nodes)
    try:
        compile(tree, "<guarded-dispatch-v1>", "exec", dont_inherit=True)
    except (SyntaxError, ValueError) as error:
        raise UnsupportedSource(str(error)) from error
    except RecursionError as error:
        raise VerificationUnknown("source validity recursion budget") from error
    meter.checkpoint(force=True)
    with meter.traversal("source_text_hash") as visit:
        visit(1 + len(source))
    source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    functions: list[ast.FunctionDef] = []
    with meter.traversal("function_vector") as visit:
        for node in tree.body:
            visit(1)
            if isinstance(node, ast.FunctionDef):
                if len(functions) >= budget.max_functions:
                    raise VerificationUnknown("function-vector budget exhausted")
                functions.append(node)
    with meter.traversal("function_name_copy") as visit:
        visit(1 + len(functions))
    names = [function.name for function in functions]
    if len(names) != len(set(names)) or "__debug__" in names:
        raise UnsupportedSource("function bindings must be unique and stable")
    dispatches = [function for function in functions if len(_signature(function, meter)) == 3]
    if len(dispatches) != 1:
        raise UnsupportedSource("source must define exactly one dispatch function")
    dispatch = dispatches[0]
    parameters = _dispatch(dispatch, meter)
    with meter.traversal("handler_vector_copy") as visit:
        visit(1 + len(functions))
    handlers = tuple(
        _handler(function, source_digest, meter)
        for function in functions
        if function is not dispatch
    )
    if not handlers:
        raise UnsupportedSource("source has no handler vector")
    handler_names = {handler.name for handler in handlers}
    tables: list[tuple[str, tuple[tuple[str, str], ...], ast.Assign]] = []
    calls: list[SourceCall] = []
    bound_names = set(names)
    defined_functions: set[str] = set()
    module_normal = True
    for statement in tree.body:
        meter.tick("module_statements")
        if isinstance(statement, ast.FunctionDef):
            defined_functions.add(statement.name)
            continue
        if (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and type(statement.value.value) is str
        ):
            continue
        if not (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            raise UnsupportedSource(
                "module has effects outside definitions/table/finite dispatch calls"
            )
        target = statement.targets[0].id
        if target in bound_names or target == "__debug__":
            raise UnsupportedSource(
                "module rebinding/alias mutation invalidates stable source bindings"
            )
        bound_names.add(target)
        value = statement.value
        if isinstance(value, ast.Dict):
            pairs: list[tuple[str, str]] = []
            for key, handler in zip(value.keys, value.values, strict=False):
                if len(pairs) >= budget.max_keys:
                    raise VerificationUnknown("finite-key budget exhausted")
                meter.tick("table_entries")
                if not (
                    isinstance(key, ast.Constant)
                    and isinstance(_literal(key), str)
                    and isinstance(handler, ast.Name)
                    and handler.id in handler_names
                    and handler.id in defined_functions
                ):
                    raise UnsupportedSource(
                        "table must be an exact literal str-key dict of known source handlers"
                    )
                key_value = _literal(key)
                if not isinstance(key_value, str) or type(key_value) is not str:
                    raise UnsupportedSource("table keys must be literal strings")
                pairs.append((key_value, handler.id))
            if len(pairs) > budget.max_keys:
                raise VerificationUnknown("finite-key budget exhausted")
            if not pairs or len({key for key, _ in pairs}) != len(pairs):
                raise UnsupportedSource("table keys must be finite, nonempty and unique")
            tables.append((target, tuple(pairs), statement))
        elif isinstance(value, ast.Call):
            if (
                not isinstance(value.func, ast.Name)
                or value.func.id != dispatch.name
                or value.keywords
                or len(value.args) != 3
                or not isinstance(value.args[0], ast.Name)
                or not isinstance(value.args[1], ast.Constant)
                or not isinstance(value.args[2], ast.Constant)
            ):
                raise UnsupportedSource(
                    "finite module call must use literal key/payload and the source table"
                )
            key_value = _literal(value.args[1])
            payload = _literal(value.args[2])
            if (
                not isinstance(key_value, str)
                or type(key_value) is not str
                or payload is not None
                and type(payload) not in (str, int, bool)
            ):
                raise UnsupportedSource(
                    "finite module call must use literal key/payload and the source table"
                )
            if dispatch.name not in defined_functions:
                raise UnsupportedSource("dispatch is not initialized at the source call")
            if len(tables) != 1 or value.args[0].id != tables[0][0]:
                raise UnsupportedSource("dispatch call precedes/changes its exact source table")
            meter.tick("source_call_payload")
            calls.append(
                SourceCall(
                    target,
                    _point(source_digest, value),
                    key_value,
                    value_snapshot(constants=(payload,)),
                    module_normal,
                )
            )
            if key_value not in {key for key, _ in tables[0][1]}:
                module_normal = False
        else:
            raise UnsupportedSource("module assignment outside the finite profile")
    if len(tables) != 1:
        raise UnsupportedSource("exactly one source table is required")
    table_name, table, table_node = tables[0]
    with meter.traversal("dependency_vector_copy") as visit:
        visit(3 + len(functions))
    dependencies = tuple(
        sorted(
            [
                VersionedDependency("function", function.name, _digest(function, meter))
                for function in functions
            ]
            + [
                VersionedDependency("table", table_name, _digest(table_node, meter)),
                VersionedDependency("model", PROFILE, MODEL_DIGEST),
            ],
            key=_dependency_key,
        )
    )
    identity = SemanticIdentity(
        source_digest,
        _digest(tree, meter),
        MODEL_DIGEST,
        policy_digest,
        PROFILE,
        state_version=state_version,
        dependencies=dependencies,
    )
    meter.admit_json({"table": table, "source": source_digest}, "table_identity_json")
    meter.tick("source_fact_publication")
    meter.checkpoint(force=True)
    return ProgramFacts(
        identity,
        dispatch.name,
        parameters,
        table_name,
        table,
        stable_digest({"table": table, "source": source_digest}),
        handlers,
        tuple(calls),
        nodes,
    )
