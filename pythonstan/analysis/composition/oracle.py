"""A run-local incremental may-inclusion oracle, never an exclusion oracle.

The same finite delta/replay kernel is used by bounded Native regions. Values
carry engine-neutral object IDs. Partial or empty candidates cannot invalidate
an independently discovered abstract behavior.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Hashable, Iterable, Mapping
from typing import Protocol, TypedDict, TypeGuard, overload

from pythonstan.analysis.contracts.identity import (
    CellId,
    ObjectId,
    ProgramPointId,
    UnitId,
    stable_digest,
)
from pythonstan.analysis.contracts.json_data import JsonObject
from pythonstan.analysis.contracts.oracle import CandidateAnswer as CandidateAnswer
from pythonstan.analysis.contracts.oracle import OracleHostPort
from pythonstan.analysis.domains.values import Value, ValueOperations


class InclusionTransfer(Protocol):
    """A scalar constraint consumes only Values and returns its exception kinds."""

    def __call__(self, *values: Value) -> tuple[Value, Iterable[str]]: ...


type InclusionNode = Hashable
type InclusionRule = tuple[tuple[InclusionNode, ...], InclusionNode, InclusionTransfer]
type InclusionListener = Callable[[InclusionNode, int], None]
type CallFact = tuple[str, str, ProgramPointId]


class PreparedInclusionStore(TypedDict):
    kernel: IncrementalInclusionStore
    work: int
    work_precharged: bool


class PreparedOracle(PreparedInclusionStore):
    subscriptions: set[tuple[CellId, UnitId]]
    calls: set[CallFact]


def _tuple_items(value: Hashable) -> TypeGuard[tuple[Hashable, ...]]:
    return isinstance(value, tuple) and all(isinstance(item, Hashable) for item in value)


def _set_items(value: Hashable) -> TypeGuard[frozenset[Hashable]]:
    return isinstance(value, frozenset) and all(isinstance(item, Hashable) for item in value)


def _address(
    identifier: ObjectId, ops: ValueOperations, mapping: Mapping[ObjectId, ObjectId] | None = None
) -> ObjectId:
    """Only an explicitly tagged field owner is an object identity."""
    seen: set[ObjectId] = set()
    aliases = {} if mapping is None else mapping
    while identifier in aliases and aliases[identifier] != identifier:
        if identifier in seen:
            raise ValueError("cyclic oracle address substitution")
        seen.add(identifier)
        identifier = aliases[identifier]
    return ops.arena.resolve(identifier)


@overload
def _payload(
    value: Value, ops: ValueOperations, mapping: Mapping[ObjectId, ObjectId] | None = None
) -> Value: ...


@overload
def _payload(
    value: Hashable, ops: ValueOperations, mapping: Mapping[ObjectId, ObjectId] | None = None
) -> Hashable: ...


def _payload(
    value: Hashable, ops: ValueOperations, mapping: Mapping[ObjectId, ObjectId] | None = None
) -> Hashable:
    if isinstance(value, Value):
        return ops.normalize(ops.remap(value, mapping) if mapping else value)
    if _tuple_items(value):
        return tuple(_payload(item, ops, mapping) for item in value)
    if _set_items(value):
        return frozenset(_payload(item, ops, mapping) for item in value)
    return value


def _node(
    node: InclusionNode, ops: ValueOperations, mapping: Mapping[ObjectId, ObjectId] | None = None
) -> InclusionNode:
    if _tuple_items(node) and len(node) == 3 and node[0] == "field":
        owner = node[1]
        if not isinstance(owner, str):
            raise TypeError("A field constraint owner must be an ObjectId")
        return ("field", _address(ObjectId(owner), ops, mapping), _payload(node[2], ops, mapping))
    return _payload(node, ops, mapping)


class IncrementalInclusionStore:
    def __init__(self, ops: ValueOperations | None = None) -> None:
        self.ops: ValueOperations = ops or ValueOperations()
        self.facts: dict[InclusionNode, Value] = {}
        self.edges: defaultdict[InclusionNode, set[InclusionNode]] = defaultdict(set)
        self.loads: defaultdict[InclusionNode, set[tuple[Hashable, InclusionNode]]] = defaultdict(
            set
        )
        self.stores: defaultdict[InclusionNode, set[tuple[Hashable, InclusionNode]]] = defaultdict(
            set
        )
        self.operations: defaultdict[InclusionNode, list[InclusionRule]] = defaultdict(list)
        self.queue: deque[InclusionNode] = deque()
        self.queued: set[InclusionNode] = set()
        self.listeners: defaultdict[InclusionNode, list[InclusionListener]] = defaultdict(list)
        self.version: int = 0
        self.work: int = 0
        self.publications: int = 0
        self.deliveries: int = 0
        self.replays: int = 0
        self.origins: defaultdict[InclusionNode, set[str]] = defaultdict(set)
        self.errors: set[str] = set()

    def add_fact(self, node: InclusionNode, value: Value, origin: str = "input") -> bool:
        node = _node(node, self.ops)
        value = _payload(value, self.ops)
        old = self.facts.get(node, Value.bottom())
        new = self.ops.join(old, value)
        if self.ops.leq(new, old) and new.refs <= old.refs and new.relations <= old.relations:
            return False
        self.facts[node] = new
        self.origins[node].add(origin)
        self.version += 1
        self.publications += 1
        if node not in self.queued:
            self.queued.add(node)
            self.queue.append(node)
        return True

    def add_edge(self, source: InclusionNode, target: InclusionNode) -> bool:
        source = _node(source, self.ops)
        target = _node(target, self.ops)
        if target in self.edges[source]:
            return False
        self.edges[source].add(target)
        self.replays += 1
        self.add_fact(target, self.facts.get(source, Value.bottom()), "late_edge_replay")
        return True

    def add_load(self, base: InclusionNode, key: Hashable, target: InclusionNode) -> None:
        base = _node(base, self.ops)
        key = _payload(key, self.ops)
        target = _node(target, self.ops)
        rule = (key, target)
        if rule in self.loads[base]:
            return
        self.loads[base].add(rule)
        for ref in self.facts.get(base, Value.bottom()).refs:
            self.add_edge(("field", ref, key), target)

    def add_store(self, base: InclusionNode, key: Hashable, source: InclusionNode) -> None:
        base = _node(base, self.ops)
        key = _payload(key, self.ops)
        source = _node(source, self.ops)
        rule = (key, source)
        if rule in self.stores[base]:
            return
        self.stores[base].add(rule)
        for ref in self.facts.get(base, Value.bottom()).refs:
            self.add_edge(source, ("field", ref, key))

    def add_operation(
        self, inputs: Iterable[InclusionNode], target: InclusionNode, function: InclusionTransfer
    ) -> None:
        inputs = tuple(_node(source, self.ops) for source in inputs)
        target = _node(target, self.ops)
        rule = (tuple(inputs), target, function)
        for source in inputs:
            self.operations[source].append(rule)
        self._operation(rule)

    def _operation(self, rule: InclusionRule) -> None:
        inputs, target, function = rule
        values = [self.facts.get(x, Value.bottom()) for x in inputs]
        if any(x.is_bottom() for x in values):
            return
        value, errors = function(*values)
        self.errors.update(errors)
        self.add_fact(target, value, "scalar_transfer")

    def subscribe(self, node: InclusionNode, callback: InclusionListener) -> None:
        node = _node(node, self.ops)
        if callback not in self.listeners[node]:
            self.listeners[node].append(callback)

    def solve(self, max_work: int | None = None) -> bool:
        selected = 0
        while self.queue and (max_work is None or selected < max_work):
            node = self.queue.popleft()
            self.queued.discard(node)
            value = self.facts[node]
            selected += 1
            self.work += 1
            for target in tuple(self.edges[node]):
                self.add_fact(target, value, "copy_constraint")
            for key, target in tuple(self.loads[node]):
                for ref in value.refs:
                    self.add_edge(("field", ref, key), target)
            for key, source in tuple(self.stores[node]):
                for ref in value.refs:
                    self.add_edge(source, ("field", ref, key))
            for rule in tuple(self.operations[node]):
                self._operation(rule)
            for callback in tuple(self.listeners[node]):
                callback(node, self.version)
        return not self.queue

    def value(self, node: InclusionNode) -> Value:
        self.deliveries += 1
        return self.facts.get(_node(node, self.ops), Value.bottom())

    def prepare_reabstract(
        self,
        mapping: Mapping[ObjectId, ObjectId],
        ops: ValueOperations | None = None,
        charge: Callable[[int], None] | None = None,
    ) -> PreparedInclusionStore:
        """Build a detached covering kernel; never invoke live callbacks here."""
        ops = ops or self.ops
        work = 0

        def spend(amount: int) -> None:
            nonlocal work
            if charge is not None:
                charge(amount)
            work += amount

        spend(1)
        result = IncrementalInclusionStore(ops)
        for node, value in self.facts.items():
            spend(1 + len(value.refs) + len(self.origins.get(node, ())))
            changed = _node(node, ops, mapping)
            converted_value = _payload(value, ops, mapping)
            result.facts[changed] = ops.join(
                result.facts.get(changed, Value.bottom()), converted_value
            )
            result.origins[changed].update(self.origins.get(node, ()))
        for source, targets in self.edges.items():
            spend(1 + len(targets))
            result.edges[_node(source, ops, mapping)].update(
                _node(target, ops, mapping) for target in targets
            )
        for source, rules in self.loads.items():
            spend(1 + len(rules))
            result.loads[_node(source, ops, mapping)].update(
                (_payload(key, ops, mapping), _node(target, ops, mapping)) for key, target in rules
            )
        for source, rules in self.stores.items():
            spend(1 + len(rules))
            result.stores[_node(source, ops, mapping)].update(
                (_payload(key, ops, mapping), _node(target, ops, mapping)) for key, target in rules
            )
        for source, operation_rules in self.operations.items():
            spend(1)
            operation_targets = result.operations[_node(source, ops, mapping)]
            for inputs, output, function in operation_rules:
                spend(1 + len(inputs))
                converted_rule: InclusionRule = (
                    tuple(_node(item, ops, mapping) for item in inputs),
                    _node(output, ops, mapping),
                    function,
                )
                if converted_rule not in operation_targets:
                    operation_targets.append(converted_rule)
        for source, callbacks in self.listeners.items():
            spend(1 + len(callbacks))
            listener_targets = result.listeners[_node(source, ops, mapping)]
            for callback in callbacks:
                if callback not in listener_targets:
                    listener_targets.append(callback)
        # Replaying every producer is necessary even when its own value did not
        # change: an alias collision may create a new load/store destination.
        spend(len(self.queue) + len(result.facts) + len(self.errors))
        result.queue = deque(
            dict.fromkeys([*(_node(node, ops, mapping) for node in self.queue), *result.facts])
        )
        result.queued = set(result.queue)
        result.version = self.version + 1
        result.work = self.work
        result.publications = self.publications
        result.deliveries = self.deliveries
        result.replays = self.replays
        result.errors = set(self.errors)
        return {"kernel": result, "work": work, "work_precharged": charge is not None}

    def statistics(self) -> JsonObject:
        return {
            "work": self.work,
            "facts": len(self.facts),
            "edges": sum(map(len, self.edges.values())),
            "publications": self.publications,
            "deliveries": self.deliveries,
            "late_edge_replays": self.replays,
            "pending": len(self.queue),
            "version": self.version,
        }


class IncrementalCandidateOracle:
    def __init__(
        self,
        *,
        source_digest: str,
        profile: str,
        policy_digest: str = "",
        model_digest: str = "pythonstan-builtins-v1",
        ops: ValueOperations | None = None,
        max_work: int = 100000,
    ) -> None:
        self.identity: str = stable_digest(
            {
                "source": source_digest,
                "profile": profile,
                "policy": policy_digest,
                "model": model_digest,
            }
        )
        self.source_digest: str = source_digest
        self.profile: str = profile
        self.policy_digest: str = policy_digest
        self.model_digest: str = model_digest
        self.kernel: IncrementalInclusionStore = IncrementalInclusionStore(ops)
        self.max_work: int = max_work
        self.driver: OracleHostPort[ValueOperations] | None = None
        self.actual_consumptions: int = 0
        self.nonempty_consumptions: int = 0
        self.calls: set[CallFact] = set()
        self._subscriptions: set[tuple[CellId, UnitId]] = set()

    def prepare_reabstract(
        self,
        mapping: Mapping[ObjectId, ObjectId],
        ops: ValueOperations | None = None,
        charge: Callable[[int], None] | None = None,
    ) -> PreparedOracle:
        prepared = self.kernel.prepare_reabstract(mapping, ops, charge=charge)
        own_work = 1 + len(self._subscriptions) + len(self.calls)
        if charge is not None:
            charge(own_work)
        return {
            **prepared,
            "subscriptions": set(self._subscriptions),
            "calls": set(self.calls),
            "work": prepared["work"] + own_work,
        }

    def commit_reabstract(self, prepared: PreparedOracle) -> None:
        """The host has charged and published its address epoch before this."""
        self.kernel = prepared["kernel"]
        self._subscriptions = prepared["subscriptions"]
        self.calls = prepared["calls"]

    def bind(
        self,
        driver: OracleHostPort[ValueOperations],
        *,
        source_digest: str | None = None,
        profile: str | None = None,
        policy_digest: str | None = None,
    ) -> None:
        actual_source = driver.source_digest
        if source_digest is not None and source_digest != actual_source:
            raise ValueError("oracle source identity override mismatch")
        actual_profile = driver.semantic_profile
        if not isinstance(actual_profile, str):
            raise TypeError("Oracle driver profile must be a string")
        if profile is not None and profile != actual_profile:
            raise ValueError("oracle profile identity override mismatch")
        actual_policy = driver.policy_digest
        if policy_digest is not None and policy_digest != actual_policy:
            raise ValueError("oracle policy identity override mismatch")
        if actual_source != self.source_digest:
            raise ValueError("oracle source identity mismatch")
        if actual_profile != self.profile:
            raise ValueError("oracle profile identity mismatch")
        if self.policy_digest and actual_policy != self.policy_digest:
            raise ValueError("oracle policy identity mismatch")
        if self.model_digest != driver.model_digest:
            raise ValueError("oracle model identity mismatch")
        if not self.policy_digest:
            self.policy_digest = actual_policy
            self.identity = stable_digest(
                {
                    "source": self.source_digest,
                    "profile": self.profile,
                    "policy": self.policy_digest,
                    "model": self.model_digest,
                }
            )
        if profile is not None and profile != self.profile:
            raise ValueError("oracle profile mismatch")
        if policy_digest is not None and self.policy_digest and policy_digest != self.policy_digest:
            raise ValueError("oracle policy identity mismatch")
        if self.driver is not None and self.driver is not driver:
            raise ValueError("oracle run identity mismatch")
        self.driver = driver
        self.kernel.ops = driver.ops

    def _solve(self) -> None:
        remaining = max(0, self.max_work - self.kernel.work)
        if self.driver is not None:
            remaining = min(remaining, max(0, self.driver.max_work - self.driver.work))
        before = self.kernel.work
        try:
            closed = self.kernel.solve(remaining)
        finally:
            if self.driver is not None:
                self.driver.charge(self.kernel.work - before, "oracle_work")
        if not closed and self.driver is not None and self.driver.work >= self.driver.max_work:
            from pythonstan.analysis.engines.abstract.driver import BudgetStop

            raise BudgetStop("oracle_global_work")

    def publish_cell(self, cell: CellId, value: Value, origin: str = "ai") -> bool:
        changed = self.kernel.add_fact(("cell", cell), value, origin)
        self._solve()
        return changed

    def publish_field(
        self, objid: ObjectId, key: Hashable, value: Value, origin: str = "ai"
    ) -> bool:
        changed = self.kernel.add_fact(("field", objid, key), value, origin)
        self._solve()
        return changed

    def register_copy(self, source: CellId, target: CellId) -> None:
        self.kernel.add_edge(("cell", source), ("cell", target))
        self._solve()

    def register_load(self, base: CellId, key: Hashable, target: CellId) -> None:
        self.kernel.add_load(("cell", base), key, ("cell", target))
        self._solve()

    def register_store(self, base: CellId, key: Hashable, source: CellId) -> None:
        self.kernel.add_store(("cell", base), key, ("cell", source))
        self._solve()

    def candidates(self, cell: CellId, consumer: UnitId | None = None) -> CandidateAnswer[Value]:
        driver = self.driver
        if consumer is not None and driver is not None:
            key = (cell, consumer)
            if key not in self._subscriptions:
                self._subscriptions.add(key)

                def notify(node: InclusionNode, version: int) -> None:
                    if consumer in driver.unit_ids:
                        driver.enqueue(consumer, "oracle_fact_growth")

                self.kernel.subscribe(("cell", cell), notify)
        self._solve()
        value = self.kernel.value(("cell", cell))
        self.actual_consumptions += 1
        if not value.is_bottom():
            self.nonempty_consumptions += 1
        return CandidateAnswer(
            value,
            "partial",
            self.kernel.version,
            tuple(sorted(self.kernel.origins[("cell", cell)])),
            self.identity,
        )

    def observe_call(self, caller: str, callee: str, site: ProgramPointId) -> None:
        self.calls.add((caller, callee, site))

    def statistics(self) -> JsonObject:
        return {
            **self.kernel.statistics(),
            "candidate_reads": self.actual_consumptions,
            "nonempty_candidate_reads": self.nonempty_consumptions,
            "call_facts": len(self.calls),
            "coverage": "partial",
            "permits_exclusion": False,
        }
