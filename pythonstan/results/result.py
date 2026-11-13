"""Detached immutable result records and their read-only public adapters."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from threading import RLock
from typing import Protocol, override
from weakref import WeakSet

from pythonstan.analysis.contracts.cost_model import (
    COST_MODEL_DIGEST,
    COST_MODEL_VERSION,
    visit_json_text,
)
from pythonstan.analysis.contracts.json_data import (
    FrozenJsonObject,
    FrozenJsonValue,
    JsonObject,
    JsonValue,
    require_array,
    require_object,
    require_string,
    thaw_json,
)
from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger, WorkReservation
from pythonstan.analysis.contracts.wire import freeze_snapshot, thaw_snapshot

from .output import OutputSpec, parse_output
from .projection import (
    ProjectedPayload,
    project_retained_payload,
    project_retained_value_query,
    retained_module_for_qualname,
)
from .queries import (
    PropertyQuery,
    QueryInput,
    QuerySpec,
    ValueQuery,
    ViewQuery,
    parse_queries,
    parse_query,
    query_to_wire,
    resolved_query_key,
)


def _frozen_object(value: FrozenJsonValue) -> FrozenJsonObject:
    if not isinstance(value, Mapping):
        raise TypeError("Expected retained JSON object")
    return value


def _frozen_array(value: FrozenJsonValue) -> tuple[FrozenJsonValue, ...]:
    if not isinstance(value, tuple):
        raise TypeError("Expected retained JSON array")
    return value


def _frozen_string(value: FrozenJsonValue) -> str:
    if not isinstance(value, str):
        raise TypeError("Expected retained JSON string")
    return value


type ReadCopy = Callable[[FrozenJsonValue], JsonValue]
type Visitor = Callable[[int], None]


class _TerminalCapacity:
    def __init__(self, permits: tuple[WorkReservation, ...]) -> None:
        self.permits = permits
        self.lock = RLock()

    @property
    def held(self) -> int:
        return sum(permit.held for permit in self.permits if permit.status == "reserved")

    def consume(self, amount: int) -> None:
        with self.lock:
            if type(amount) is not int or not 0 <= amount <= self.held:
                raise ResourceStop("terminal_capacity")
            remaining = amount
            for permit in self.permits:
                if permit.status == "reserved" and remaining:
                    take = min(remaining, permit.held)
                    permit.consume_terminal(take)
                    remaining -= take


def _bounded_ledger(ledger: RunLedger) -> JsonObject:
    return {
        "cost_model": {"version": COST_MODEL_VERSION, "digest": COST_MODEL_DIGEST},
        "work": {
            "limit": ledger.work.limit,
            "used": ledger.work.used,
            "reserved": ledger.work.reserved,
            "remaining": ledger.work.remaining,
        },
        "stop_reason": ledger.stop_reason,
        "scope": "before_checked_terminal_freeze",
    }


def _terminal_frame(engine: str, status: str, reason: str, ledger: RunLedger) -> JsonObject:
    bounded_reason = reason[:64]
    return {
        "schema_version": 1,
        "actual_engine": engine,
        "solver_status": status,
        "coverage_status": "open",
        "proof_status": "unknown",
        "values": {},
        "returns": {},
        "calls": [],
        "outcomes": {},
        "effects": {},
        "effective_config": {"output": {}},
        "inventory": [],
        "entry_contract": {"entry_coverage": "not_exported"},
        "resource_ledger": _bounded_ledger(ledger),
        "resource_ledger_scope": "before_checked_terminal_freeze",
        "stats": {
            "work": 0,
            "total_work": ledger.work.used,
            "global_work_used": ledger.work.used,
            "input_publications": 0,
            "memory_checks": ledger.memory_checks,
            "resident_memory_bytes": ledger.memory_used,
            "total_work_scope": "before_checked_terminal_freeze",
        },
        "obligations": [
            {
                "kind": "time_budget" if reason == "max_seconds" else "resource_termination",
                "code": "budget:" + bounded_reason,
                "reason": bounded_reason,
            }
        ],
        "phase_trace": [{"phase": "resource_termination", "status": bounded_reason}],
        "projection": {
            "truncated": True,
            "complete": False,
            "omitted_fields": ["analysis_facts", "full_configuration", "proof_history"],
        },
        "terminal_publication": {"truncated": True, "controller_only": True},
    }


@dataclass(frozen=True, eq=False)
class _PublicationOwnership:
    document: FrozenJsonObject


_ISSUED_OWNERSHIP: WeakSet[_PublicationOwnership] = WeakSet()


@dataclass(frozen=True)
class PublishedSnapshot:
    """An owned checked record; only the factory can issue an adoption receipt."""

    document: FrozenJsonObject
    solver_status: str
    coverage_status: str
    proof_status: str
    actual_engine: str
    _ownership: _PublicationOwnership | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_wire(cls, data: object, *, visit: Visitor | None = None) -> PublishedSnapshot:
        document = _frozen_object(freeze_snapshot(data, visit=visit))
        receipt = _PublicationOwnership(document)
        result = cls(
            document,
            _frozen_string(document["solver_status"]),
            _frozen_string(document["coverage_status"]),
            _frozen_string(document.get("proof_status", "not_requested")),
            _frozen_string(document["actual_engine"]),
            receipt,
        )
        _ISSUED_OWNERSHIP.add(receipt)
        return result

    def _owns(self, payload: FrozenJsonValue, path: tuple[str, ...]) -> bool:
        receipt = self._ownership
        if (
            type(receipt) is not _PublicationOwnership
            or receipt not in _ISSUED_OWNERSHIP
            or receipt.document is not self.document
        ):
            return False
        current: FrozenJsonValue = self.document
        for part in path:
            if not isinstance(current, Mapping) or part not in current:
                return False
            current = current[part]
        return current is payload


@dataclass(frozen=True)
class QueryAnswer(Mapping[str, JsonValue]):
    query: FrozenJsonObject
    status: str
    _payload: FrozenJsonValue
    approximation: str = "flow_sensitive_may"
    coverage_status: str = "open"
    proof_status: str = "not_requested"
    _copy: ReadCopy | None = field(default=None, repr=False, compare=False)
    _terminal: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", _frozen_object(freeze_snapshot(self.query)))
        object.__setattr__(self, "_payload", freeze_snapshot(self._payload))

    @classmethod
    def _construct(
        cls,
        query: FrozenJsonObject,
        status: str,
        payload: FrozenJsonValue,
        approximation: str,
        coverage_status: str,
        proof_status: str,
        copy: ReadCopy | None,
        terminal: bool,
    ) -> QueryAnswer:
        answer = object.__new__(cls)
        for name, value in (
            ("query", query),
            ("status", status),
            ("_payload", payload),
            ("approximation", approximation),
            ("coverage_status", coverage_status),
            ("proof_status", proof_status),
            ("_copy", copy),
            ("_terminal", terminal),
        ):
            object.__setattr__(answer, name, value)
        return answer

    @classmethod
    def create(
        cls,
        query: QuerySpec,
        status: str,
        payload: FrozenJsonValue,
        approximation: str,
        coverage_status: str,
        proof_status: str = "not_requested",
        *,
        visit: Visitor | None = None,
        copy: ReadCopy | None = None,
        terminal: bool = False,
    ) -> QueryAnswer:
        owned_query = _frozen_object(freeze_snapshot(query_to_wire(query), visit=visit))
        owned_payload = freeze_snapshot(payload, visit=visit)
        return cls._construct(
            owned_query,
            status,
            owned_payload,
            approximation,
            coverage_status,
            proof_status,
            copy,
            terminal,
        )

    @classmethod
    def _from_published(
        cls,
        query: QuerySpec,
        status: str,
        payload: FrozenJsonValue,
        approximation: str,
        coverage_status: str,
        proof_status: str = "not_requested",
        *,
        owner: PublishedSnapshot,
        path: tuple[str, ...],
        visit: Visitor | None = None,
        copy: ReadCopy | None = None,
        terminal: bool = False,
    ) -> QueryAnswer:
        if not owner._owns(payload, path):
            raise ValueError("Payload has no issued publication ownership receipt")
        owned_query = _frozen_object(freeze_snapshot(query_to_wire(query), visit=visit))
        return cls._construct(
            owned_query,
            status,
            payload,
            approximation,
            coverage_status,
            proof_status,
            copy,
            terminal,
        )

    def _read(self, value: FrozenJsonValue) -> JsonValue:
        return thaw_snapshot(value) if self._copy is None else self._copy(value)

    def _unavailable(self) -> JsonObject:
        object.__setattr__(self, "status", "unavailable")
        object.__setattr__(self, "coverage_status", "open")
        object.__setattr__(self, "proof_status", "unknown")
        return {
            "query": {"kind": _frozen_string(self.query.get("kind", "value"))},
            "status": "unavailable",
            "value": None,
            "approximation": self.approximation,
            "coverage_status": "open",
            "proof_status": "unknown",
            "projection": {
                "complete": False,
                "truncated": True,
                "omitted_fields": ["query_scope", "value"],
            },
        }

    @property
    def value(self) -> JsonValue:
        if self._terminal and self.status == "unavailable":
            return None
        try:
            return self._read(self._payload)
        except ResourceStop as error:
            if not self._terminal or error.reason != "terminal_capacity":
                raise
            self._unavailable()
            return None

    @property
    def answer(self) -> JsonValue:
        return self.value

    def to_dict(self) -> JsonObject:
        if self._terminal and self.status == "unavailable":
            return self._unavailable()
        wire: dict[str, FrozenJsonValue] = {
            "query": self.query,
            "status": self.status,
            "value": self._payload,
            "approximation": self.approximation,
            "coverage_status": self.coverage_status,
            "proof_status": self.proof_status,
        }
        try:
            return require_object(self._read(wire))
        except ResourceStop as error:
            if not self._terminal or error.reason != "terminal_capacity":
                raise
            return self._unavailable()

    @override
    def __getitem__(self, key: str) -> JsonValue:
        if key == "value":
            return self.value
        if key == "query":
            if self._terminal and self.status == "unavailable":
                return self._unavailable()["query"]
            try:
                return self._read(self.query)
            except ResourceStop as error:
                if not self._terminal or error.reason != "terminal_capacity":
                    raise
                return self._unavailable()["query"]
        values: dict[str, JsonValue] = {
            "status": self.status,
            "approximation": self.approximation,
            "coverage_status": self.coverage_status,
            "proof_status": self.proof_status,
        }
        return values[key]

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(
            ("query", "status", "value", "approximation", "coverage_status", "proof_status")
        )

    @override
    def __len__(self) -> int:
        return 6


class TextDestination(Protocol):
    def write(self, text: str, /) -> object: ...


@dataclass(frozen=True)
class _RetainedAnswer:
    status: str
    payload: FrozenJsonValue
    path: tuple[str, ...] | None = None
    proof_status: str = "not_requested"


def _resolved_queries(queries: Sequence[QueryInput]) -> tuple[QuerySpec, ...]:
    """Keep the actual resolved run untouched; parse only legacy external construction."""
    result: list[QuerySpec] = []
    for query in queries:
        if not isinstance(query, (ValueQuery, PropertyQuery, ViewQuery)):
            return parse_queries(queries)
        result.append(query)
    return tuple(result)


class AnalysisResult:
    """Published facts stay independent of session lifetime and later runs."""

    _snapshot: PublishedSnapshot
    _queries: tuple[QuerySpec, ...]
    _query_keys: frozenset[str]
    output: OutputSpec

    def __init__(
        self,
        data: object,
        queries: Sequence[QueryInput],
        output: OutputSpec | None = None,
        *,
        resource_ledger: RunLedger | None = None,
        terminal_permit: WorkReservation | None = None,
    ) -> None:
        self._resource_ledger = resource_ledger
        self._terminal_permit = terminal_permit
        self._terminal_capacity: _TerminalCapacity | None = None
        self._controller_only = False
        self._cached_terminal: AnalysisResult | None = None
        try:
            if resource_ledger is None:
                self._snapshot = PublishedSnapshot.from_wire(data)
            else:
                if terminal_permit is not None:
                    self._terminal_permit = resource_ledger.ensure_terminal_permit(terminal_permit)
                with (
                    resource_ledger.phase("export"),
                    resource_ledger.structural_meter("result_publication") as meter,
                ):
                    self._snapshot = PublishedSnapshot.from_wire(data, visit=meter.visit)
        except ResourceStop as error:
            if resource_ledger is None:
                raise
            fallback = type(self).from_terminal(
                data if isinstance(data, Mapping) else None,
                queries,
                output,
                resource_ledger=resource_ledger,
                terminal_permit=self._terminal_permit,
                reason=error.reason,
            )
            self._snapshot = fallback._snapshot
            self._resource_ledger = fallback._resource_ledger
            self._terminal_permit = fallback._terminal_permit
            self._terminal_capacity = fallback._terminal_capacity
            self._controller_only = fallback._controller_only
            self._cached_terminal = fallback._cached_terminal
            self._queries = fallback._queries
            self._query_keys = fallback._query_keys
            self.output = fallback.output
            return
        try:
            self._queries, self._query_keys = self._register_queries(queries)
        except ResourceStop as error:
            if resource_ledger is None:
                raise
            fallback = type(self).from_terminal(
                self._snapshot.document,
                queries,
                output,
                resource_ledger=resource_ledger,
                terminal_permit=self._terminal_permit,
                reason=error.reason,
                engine=self._snapshot.actual_engine,
            )
            self._snapshot = fallback._snapshot
            self._terminal_permit = fallback._terminal_permit
            self._terminal_capacity = fallback._terminal_capacity
            self._controller_only = fallback._controller_only
            self._cached_terminal = fallback._cached_terminal
            self._queries, self._query_keys = fallback._queries, fallback._query_keys
            self.output = fallback.output
            return
        if output is None:
            config = _frozen_object(self._snapshot.document["effective_config"])
            self.output = parse_output(thaw_json(config["output"]))
        else:
            self.output = output
        if self._terminal_permit is not None and self._terminal_permit.status == "reserved":
            self._terminal_permit.settle(self._terminal_permit.observed)

    @classmethod
    def from_terminal(
        cls,
        partial: Mapping[str, JsonValue | FrozenJsonValue] | None,
        queries: Sequence[QueryInput],
        output: OutputSpec | None = None,
        *,
        resource_ledger: RunLedger,
        terminal_permit: WorkReservation | None = None,
        reason: str | None = None,
        status: str = "budget_exhausted",
        engine: str = "ai_primary",
    ) -> AnalysisResult:
        """Publish only an admitted prefix; insufficient capacity has a fixed response."""
        first = resource_ledger.stop_reason or reason or "resource_termination"
        if engine not in {"native", "hybrid", "ai_primary", "native_then_ai"}:
            engine = "unknown"
        initial = resource_ledger.ensure_terminal_permit(terminal_permit)
        extra = resource_ledger.ensure_terminal_permit(
            amount=min(65536, resource_ledger.remaining_work)
        )
        capacity = _TerminalCapacity((initial, extra))
        allowance = capacity.held // 3
        publication_used = 0

        def admit(amount: int) -> None:
            nonlocal publication_used
            if amount + publication_used > allowance:
                raise ResourceStop("terminal_capacity")
            capacity.consume(amount)
            publication_used += amount

        bounded = _terminal_frame(engine, status, first, resource_ledger)
        base: dict[str, object] = dict(bounded)
        if partial is not None:
            for key in ("values", "returns", "calls", "outcomes", "effects"):
                if key in partial:
                    base[key] = partial[key]
            raw_stats = partial.get("stats")
            if isinstance(raw_stats, Mapping):
                stats = require_object(bounded["stats"])
                for key in ("work", "input_publications"):
                    value: object = raw_stats.get(key)
                    if type(value) is int:
                        stats[key] = value
            base["terminal_publication"] = {"truncated": True, "controller_only": False}
        result = object.__new__(cls)
        result._resource_ledger = resource_ledger
        result._terminal_permit = initial
        result._terminal_capacity = capacity
        result._controller_only = False
        result._cached_terminal = None
        try:
            with resource_ledger.structural_meter("terminal_publication", charge=admit) as meter:
                result._snapshot = PublishedSnapshot.from_wire(base, visit=meter.visit)
        except (ResourceStop, TypeError, ValueError):
            result._snapshot = PublishedSnapshot.from_wire(
                _terminal_frame(engine, status, first, resource_ledger)
            )
            result._controller_only = True
        result._queries = ()
        result._query_keys = frozenset()
        if not result._controller_only:
            try:
                if len(queries) > 32:
                    raise ResourceStop("terminal_capacity")
                result._queries, result._query_keys = result._register_queries(queries)
            except ResourceStop:
                result._snapshot = PublishedSnapshot.from_wire(
                    _terminal_frame(engine, status, first, resource_ledger)
                )
                result._controller_only = True
                result._queries, result._query_keys = (), frozenset()
        result.output = output if output is not None else OutputSpec()
        return result

    def _register_queries(
        self, queries: Sequence[QueryInput]
    ) -> tuple[tuple[QuerySpec, ...], frozenset[str]]:
        if self._resource_ledger is None:
            resolved = _resolved_queries(queries)
            return resolved, frozenset(resolved_query_key(query) for query in resolved)
        resolved_list: list[QuerySpec] = []
        keys: set[str] = set()
        with self._read_operation("result_query_registration") as visit:
            for raw in queries:
                if visit is not None:
                    visit(1)
                if isinstance(raw, Mapping):
                    if len(raw) > 5:
                        raise ValueError("A query has too many fields")
                    for key, item in raw.items():
                        if key not in {"kind", "module", "symbol", "point", "property"}:
                            raise ValueError("Unsupported query field")
                        if not isinstance(item, str):
                            raise TypeError("Query fields require strings")
                        if visit is not None:
                            visit_json_text(key, visit)
                            visit_json_text(item, visit)
                elif isinstance(raw, str) and visit is not None:
                    visit_json_text(raw, visit)
                query = parse_query(raw)
                wire = query_to_wire(query)
                if visit is not None:
                    for key, value in wire.items():
                        visit_json_text(key, visit)
                        visit_json_text(require_string(value), visit)
                resolved_list.append(query)
                keys.add(resolved_query_key(query))
        return tuple(resolved_list), frozenset(keys)

    def _copy_json(self, value: object) -> JsonValue:
        if self._controller_only or self._resource_ledger is None:
            return thaw_snapshot(value)
        if self._terminal_capacity is not None:
            with self._resource_ledger.structural_meter(
                "result_projection_copy", charge=self._terminal_capacity.consume
            ) as meter:
                return thaw_snapshot(value, visit=meter.visit)
        with (
            self._resource_ledger.phase("export"),
            self._resource_ledger.structural_meter("result_projection_copy") as meter,
        ):
            return thaw_snapshot(value, visit=meter.visit)

    def _fallback(self, reason: str) -> AnalysisResult:
        if self._cached_terminal is not None:
            return self._cached_terminal
        ledger = self._resource_ledger
        if ledger is None:
            raise ResourceStop(reason)
        if self._terminal_capacity is not None:
            result = object.__new__(type(self))
            result._snapshot = PublishedSnapshot.from_wire(
                _terminal_frame(
                    self.actual_engine, self.solver_status, ledger.stop_reason or reason, ledger
                )
            )
            result._resource_ledger = ledger
            result._terminal_capacity = self._terminal_capacity
            result._terminal_permit = self._terminal_permit
            result._controller_only = True
            result._cached_terminal = None
            result._queries, result._query_keys, result.output = (
                self._queries,
                self._query_keys,
                self.output,
            )
        else:
            result = type(self).from_terminal(
                self._snapshot.document,
                self._queries,
                self.output,
                resource_ledger=ledger,
                terminal_permit=self._terminal_permit,
                reason=reason,
                engine=self.actual_engine,
            )
        self._cached_terminal = result
        return result

    def _terminal_after_read(self) -> AnalysisResult | None:
        """Observe a fallback that a paid projection may just have published."""
        return self._cached_terminal

    def _read_field(self, name: str, default: FrozenJsonValue) -> JsonValue:
        try:
            return self._copy_json(self._snapshot.document.get(name, default))
        except ResourceStop as error:
            if error.reason == "terminal_capacity" or (
                self._resource_ledger is not None and self._resource_ledger.stop_reason is not None
            ):
                return self._fallback(error.reason)._read_field(name, default)
            raise

    @property
    def solver_status(self) -> str:
        return self._snapshot.solver_status

    @property
    def coverage_status(self) -> str:
        return self._snapshot.coverage_status

    @property
    def proof_status(self) -> str:
        return self._snapshot.proof_status

    @property
    def effective_config(self) -> JsonObject:
        return require_object(self._read_field("effective_config", {}))

    @property
    def phase_trace(self) -> list[JsonValue]:
        return require_array(self._read_field("phase_trace", ()))

    @property
    def actual_engine(self) -> str:
        return self._snapshot.actual_engine

    @property
    def is_complete(self) -> bool:
        """Compatibility only: declared convergence/coverage, independent of proof."""
        return self.solver_status == "converged" and self.coverage_status == "closed_under_profile"

    def query(self, query: QueryInput) -> QueryAnswer:
        return self._query_resolved(parse_query(query))

    @contextmanager
    def _read_operation(self, category: str) -> Iterator[Visitor | None]:
        ledger = self._resource_ledger
        if ledger is None or self._controller_only:
            yield None
        elif self._terminal_capacity is not None:
            with ledger.structural_meter(category, charge=self._terminal_capacity.consume) as meter:
                yield meter.visit
        else:
            with ledger.phase("export"), ledger.structural_meter(category) as meter:
                yield meter.visit

    def _controller_answer(self, query: QuerySpec) -> QueryAnswer:
        header = _frozen_object(freeze_snapshot({"kind": query.kind}))
        return QueryAnswer._construct(
            header,
            "unavailable",
            None,
            "flow_insensitive_may" if self.actual_engine == "native" else "flow_sensitive_may",
            "open",
            "unknown",
            None,
            True,
        )

    def _query_resolved(self, query: QuerySpec) -> QueryAnswer:
        if self._cached_terminal is not None:
            return self._cached_terminal._query_resolved(query)
        if self._controller_only:
            return self._controller_answer(query)
        try:
            with self._read_operation("result_query_select") as inspect:
                if inspect is not None:
                    inspect(1)
                retained = self._retain_query(query, inspect=inspect)
            approximation = (
                "flow_insensitive_may" if self.actual_engine == "native" else "flow_sensitive_may"
            )
            with self._read_operation("query_answer_publication") as visit:
                if retained.path is not None:
                    return QueryAnswer._from_published(
                        query,
                        retained.status,
                        retained.payload,
                        approximation,
                        self.coverage_status,
                        retained.proof_status,
                        owner=self._snapshot,
                        path=retained.path,
                        visit=visit,
                        copy=self._copy_json,
                        terminal=self._terminal_capacity is not None,
                    )
                return QueryAnswer.create(
                    query,
                    retained.status,
                    retained.payload,
                    approximation,
                    self.coverage_status,
                    retained.proof_status,
                    visit=visit,
                    copy=self._copy_json,
                    terminal=self._terminal_capacity is not None,
                )
        except ResourceStop as error:
            if error.reason == "terminal_capacity" or (
                self._resource_ledger is not None and self._resource_ledger.stop_reason is not None
            ):
                return self._fallback(error.reason)._query_resolved(query)
            raise

    def _retain_query(self, query: QuerySpec, *, inspect: Visitor | None = None) -> _RetainedAnswer:
        requested = query.kind == "summary" or resolved_query_key(query) in self._query_keys
        if isinstance(query, ValueQuery):
            view = ViewQuery("values" if query.point == "module_exit" else "returns")
            requested = requested or resolved_query_key(view) in self._query_keys
        if not requested:
            return _RetainedAnswer("not_requested", None)
        data = self._snapshot.document
        payload: FrozenJsonValue
        path: tuple[str, ...] | None = None
        proof_status = "not_requested"
        if isinstance(query, ValueQuery):
            if query.point == "module_exit":
                payload = _frozen_object(
                    _frozen_object(data.get("values", {})).get(query.module, {})
                ).get(query.symbol)
                path = ("values", query.module, query.symbol)
            else:
                key = query.module + "." + query.symbol
                payload = _frozen_object(data.get("returns", {})).get(key)
                path = ("returns", key)
            if payload is None or (
                isinstance(payload, Mapping)
                and payload.get("bottom")
                and self.solver_status != "converged"
            ):
                return _RetainedAnswer("unavailable", None)
        elif isinstance(query, PropertyQuery):
            proof = _frozen_object(data.get("proof", {}))
            queries = _frozen_object(proof.get("queries", {}))
            properties = _frozen_object(proof.get("properties", {}))
            if resolved_query_key(query) in queries:
                payload = queries[resolved_query_key(query)]
                path = ("proof", "queries", resolved_query_key(query))
            elif query.property in properties:
                payload = properties[query.property]
                path = ("proof", "properties", query.property)
            else:
                payload = {
                    "status": "unknown",
                    "reason": "No closed certificate for the requested property",
                }
            proof_status = _frozen_string(_frozen_object(payload).get("status", "unknown"))
        elif query.kind == "relations":
            payload = tuple(self._relations(inspect=inspect))
        elif query.kind == "summary":
            payload = {
                "modules": len(_frozen_array(data.get("inventory", ()))),
                "entry_contract": data.get("entry_contract", {"entry_coverage": "not_exported"}),
                "calls": len(_frozen_array(data.get("calls", ()))),
                "obligations": len(_frozen_array(data.get("obligations", ()))),
                "actual_engine": self.actual_engine,
                "solver_status": self.solver_status,
                "coverage_status": self.coverage_status,
                "proof_status": self.proof_status,
            }
        else:
            payload = data.get(query.kind)
            path = (query.kind,) if payload is not None else None
            if query.kind == "proof":
                proof_status = self.proof_status
            if payload is None:
                return _RetainedAnswer("unavailable", None, proof_status=proof_status)
        return _RetainedAnswer("answered", payload, path, proof_status)

    def _relations(self, *, inspect: Visitor | None = None) -> list[FrozenJsonValue]:
        data = self._snapshot.document
        rows: list[FrozenJsonValue] = []
        for module, value in _frozen_object(data.get("values", {})).items():
            if inspect is not None:
                inspect(1)
            for symbol, raw_value in _frozen_object(value).items():
                if inspect is not None:
                    inspect(1)
                for relation in _frozen_array(_frozen_object(raw_value).get("relations", ())):
                    if inspect is not None:
                        inspect(1 + len(_frozen_object(relation)))
                    row: dict[str, FrozenJsonValue] = dict(_frozen_object(relation))
                    row.update(
                        module=module,
                        symbol=symbol,
                        qualname=module + "." + symbol,
                        observation_point="module_exit",
                    )
                    rows.append(row)
        for function, value in _frozen_object(data.get("returns", {})).items():
            if inspect is not None:
                inspect(1)
            for relation in _frozen_array(_frozen_object(value).get("relations", ())):
                if inspect is not None:
                    inspect(1 + len(_frozen_object(relation)))
                row = dict(_frozen_object(relation))
                row.update(
                    module=retained_module_for_qualname(function, data, inspect=inspect),
                    qualname=function,
                    observation_point="function_exit",
                )
                rows.append(row)
        return rows

    def raw_snapshot(self) -> JsonObject:
        if self._cached_terminal is not None:
            return self._cached_terminal.raw_snapshot()
        try:
            return require_object(self._copy_json(self._snapshot.document))
        except ResourceStop as error:
            if error.reason == "terminal_capacity" or (
                self._resource_ledger is not None and self._resource_ledger.stop_reason is not None
            ):
                return self._fallback(error.reason).raw_snapshot()
            raise

    def _projected_wire(
        self,
        query: QuerySpec,
        retained: _RetainedAnswer,
        projected: ProjectedPayload,
    ) -> JsonObject:
        proof = isinstance(query, PropertyQuery) or query.kind == "proof"
        status = projected.status if projected.status is not None else retained.status
        header: dict[str, object] = {
            "query": query_to_wire(query),
            "status": status,
            "value": None,
            "approximation": "flow_insensitive_may"
            if self.actual_engine == "native"
            else "flow_sensitive_may",
            "coverage_status": self.coverage_status,
            "proof_status": "unknown"
            if proof and projected.status is not None
            else retained.proof_status,
        }
        if projected.reason is not None:
            header["reason"] = projected.reason
        wire = require_object(self._copy_json(header))
        wire["value"] = projected.value
        return wire

    def to_dict(self, output: OutputSpec | Mapping[str, object] | None = None) -> JsonObject:
        spec = self.output if output is None else parse_output(output)
        if self._cached_terminal is not None:
            return self._cached_terminal.to_dict(spec)
        try:
            return self._project_dict(spec)
        except ResourceStop as error:
            if error.reason == "terminal_capacity" or (
                self._resource_ledger is not None and self._resource_ledger.stop_reason is not None
            ):
                return self._fallback(error.reason).to_dict(spec)
            raise

    def _project_dict(self, spec: OutputSpec) -> JsonObject:
        data = self._snapshot.document
        if self._controller_only:
            result = require_object(thaw_snapshot(data))
            controller_answers: JsonObject = {}
            for view in spec.views[:16]:
                controller_answers[view] = self._controller_answer(ViewQuery(view)).to_dict()
            for query in self._queries[:16]:
                if isinstance(query, (ValueQuery, PropertyQuery)):
                    controller_answers[query.kind] = self._controller_answer(query).to_dict()
            result["answers"] = controller_answers
            result["projection"] = {
                "truncated": True,
                "complete": False,
                "output_items": 0,
                "total_items": None,
                "total_items_status": "not_exported",
                "omitted_fields": ["retained_facts", "query_scope"],
            }
            return result
        invariant_keys = (
            "schema_version",
            "run_id",
            "source_digest",
            "model_digest",
            "policy_digest",
            "target",
            "entry_contract",
            "effective_config",
            "config_provenance",
            "inventory",
            "actual_engine",
            "phase_trace",
            "solver_status",
            "coverage_status",
            "proof_status",
            "profile",
            "diagnostics",
            "obligations",
            "evidence",
            "resource_ledger",
            "resource_ledger_scope",
            "terminal_publication",
        )
        invariants = {key: data[key] for key in invariant_keys if key in data}
        result = require_object(self._copy_json(invariants))
        total = count = 0
        truncated = False
        answers: JsonObject = {}
        pending: list[tuple[QuerySpec, str]] = [(ViewQuery(view), view) for view in spec.views]
        pending.extend(
            (query, resolved_query_key(query))
            for query in self._queries
            if isinstance(query, (ValueQuery, PropertyQuery))
        )
        for query, key in pending:
            if key in answers:
                continue
            with self._read_operation("result_query_select") as inspect:
                if inspect is not None:
                    inspect(1)
                retained = self._retain_query(query, inspect=inspect)
            with (
                self._read_operation("result_projection_select") as inspect,
                self._read_operation("result_projection_copy") as visit,
            ):
                projected = (
                    project_retained_value_query(
                        query, retained.payload, spec, visit=visit, inspect=inspect
                    )
                    if isinstance(query, ValueQuery)
                    else project_retained_payload(
                        "proof" if isinstance(query, PropertyQuery) else query.kind,
                        retained.payload,
                        spec,
                        data,
                        visit=visit,
                        inspect=inspect,
                    )
                )
            answers[key] = self._projected_wire(query, retained, projected)
            total += projected.total
            count += projected.count
            truncated |= projected.truncated
        projection: JsonObject = {
            "views": list(spec.views),
            "modules": list(spec.modules),
            "functions": list(spec.functions),
            "regions": list(spec.regions),
            "contexts": list(spec.contexts),
            "fields": list(spec.fields),
            "truncated": truncated,
            "total_items": total,
            "output_items": count,
        }
        result.update(answers=answers, projection=projection)
        return result

    @contextmanager
    def _serialization_operation(self, value: JsonValue, category: str) -> Iterator[None]:
        ledger = self._resource_ledger
        if ledger is None or self._controller_only:
            yield
        elif self._terminal_capacity is not None:
            ledger.charge_json(value, category, charge=self._terminal_capacity.consume)
            yield
        else:
            with ledger.phase("export"):
                ledger.charge_json(value, category)
                yield
                ledger.checkpoint(force=True)

    @staticmethod
    def _serialize_data(data: JsonObject, format: str) -> str:
        if format == "json":
            return (
                json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
                + "\n"
            )
        lines = [
            "PythonStAn: " + require_string(data["actual_engine"]),
            "solver="
            + require_string(data["solver_status"])
            + "; coverage="
            + require_string(data["coverage_status"])
            + "; proof="
            + require_string(data["proof_status"]),
        ]
        for view, item in require_object(data["answers"]).items():
            answer = require_object(item)
            lines.append(
                view
                + " ["
                + require_string(answer["status"])
                + "]: "
                + json.dumps(answer["value"], ensure_ascii=False, sort_keys=True)
            )
        if require_object(data["projection"])["truncated"]:
            lines.append("Output truncated; see projection metadata.")
        return "\n".join(lines) + "\n"

    def serialize(
        self, format: str | None = None, output: OutputSpec | Mapping[str, object] | None = None
    ) -> str:
        spec = self.output if output is None else parse_output(output)
        selected_format = spec.format if format is None else format
        if selected_format not in {"json", "text"}:
            raise ValueError("format must be json or text")
        if self._cached_terminal is not None:
            return self._cached_terminal.serialize(selected_format, spec)
        try:
            data = self.to_dict(spec)
            terminal = self._terminal_after_read()
            if terminal is not None:
                return terminal.serialize(selected_format, spec)
            with self._serialization_operation(data, "result_serialization"):
                return self._serialize_data(data, selected_format)
        except ResourceStop as error:
            if error.reason == "terminal_capacity" or (
                self._resource_ledger is not None and self._resource_ledger.stop_reason is not None
            ):
                return self._fallback(error.reason).serialize(selected_format, spec)
            raise

    def write(
        self,
        destination: str | PathLike[str] | TextDestination,
        format: str | None = None,
        output: OutputSpec | Mapping[str, object] | None = None,
    ) -> None:
        text = self.serialize(format=format, output=output)
        if self._cached_terminal is not None:
            self._cached_terminal.write(destination, format=format, output=output)
            return
        dispatched = False
        try:
            with self._serialization_operation(text, "result_write"):
                dispatched = True
                if isinstance(destination, (str, PathLike)):
                    Path(destination).write_text(text, encoding="utf-8")
                else:
                    destination.write(text)
        except ResourceStop as error:
            # An arbitrary stream cannot be rolled back. A late deadline or
            # destination failure must not append a second serialized document.
            if dispatched:
                raise
            if error.reason == "terminal_capacity" or (
                self._resource_ledger is not None and self._resource_ledger.stop_reason is not None
            ):
                self._fallback(error.reason).write(destination, format=format, output=output)
                return
            raise

    @override
    def __repr__(self) -> str:
        return f"AnalysisResult(engine={self.actual_engine!r}, solver={self.solver_status!r}, coverage={self.coverage_status!r})"
