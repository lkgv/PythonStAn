"""Immutable engine snapshots and checked JSON wire.

No solver object, mutable arena, callback, or user object enters this boundary.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence, ValuesView
from dataclasses import dataclass
from types import MappingProxyType
from typing import override

from .json_data import FrozenJsonObject, FrozenJsonValue, JsonObject
from .outcomes import GuaranteeLedger
from .wire import freeze_snapshot as freeze_snapshot
from .wire import thaw_snapshot as thaw_snapshot
from .wire import value_snapshot as value_snapshot

_EMPTY: FrozenJsonObject = MappingProxyType({})
_FIELDS = frozenset(
    {
        "engine",
        "solver_status",
        "coverage_status",
        "profile",
        "schema_version",
        "phase_trace",
        "values",
        "module_exits",
        "returns",
        "outcomes",
        "calls",
        "effects",
        "obligations",
        "diagnostics",
        "stats",
        "provenance",
    }
)


def _text(value: FrozenJsonValue) -> str:
    if not isinstance(value, str):
        raise TypeError("Snapshot header must be a string")
    return value


def _object(value: FrozenJsonValue) -> FrozenJsonObject:
    if not isinstance(value, Mapping):
        raise TypeError("Snapshot object field must be an object")
    return value


def _array(value: FrozenJsonValue) -> tuple[FrozenJsonValue, ...]:
    if not isinstance(value, tuple):
        raise TypeError("Snapshot sequence field must be an array")
    return value


@dataclass(frozen=True, eq=False)
class ValueFacts(Mapping[str, FrozenJsonValue]):
    """Readonly legacy facts attribute and the genuine Mapping.values method.

    The public transport field is named 'values'. This callable view preserves
    attribute indexing while values() returns the full record's values view.
    """

    _facts: FrozenJsonObject
    _record: FrozenJsonObject

    @override
    def __getitem__(self, key: str) -> FrozenJsonValue:
        return self._facts[key]

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self._facts)

    @override
    def __len__(self) -> int:
        return len(self._facts)

    def __call__(self) -> ValuesView[FrozenJsonValue]:
        return self._record.values()


@dataclass(frozen=True, init=False)
class EngineSnapshot(Mapping[str, FrozenJsonValue]):
    engine: str
    _guarantees: GuaranteeLedger
    schema_version: int
    phase_trace: tuple[FrozenJsonValue, ...]
    values: ValueFacts
    module_exits: FrozenJsonObject
    returns: FrozenJsonObject
    outcomes: FrozenJsonObject
    calls: tuple[FrozenJsonValue, ...]
    effects: FrozenJsonObject
    obligations: tuple[FrozenJsonValue, ...]
    diagnostics: tuple[FrozenJsonValue, ...]
    stats: FrozenJsonObject
    provenance: FrozenJsonObject
    _data: FrozenJsonObject

    def __init__(
        self,
        engine: str,
        solver_status: str,
        coverage_status: str,
        profile: str = "python-core-v1",
        schema_version: int = 1,
        phase_trace: Sequence[object] = (),
        values: Mapping[str, object] = _EMPTY,
        module_exits: Mapping[str, object] = _EMPTY,
        returns: Mapping[str, object] = _EMPTY,
        outcomes: Mapping[str, object] = _EMPTY,
        calls: Sequence[object] = (),
        effects: Mapping[str, object] = _EMPTY,
        obligations: Sequence[object] = (),
        diagnostics: Sequence[object] = (),
        stats: Mapping[str, object] = _EMPTY,
        provenance: Mapping[str, object] = _EMPTY,
    ) -> None:
        raw: dict[str, object] = {
            "engine": engine,
            "solver_status": solver_status,
            "coverage_status": coverage_status,
            "profile": profile,
            "schema_version": schema_version,
            "phase_trace": phase_trace,
            "values": values,
            "module_exits": module_exits,
            "returns": returns,
            "outcomes": outcomes,
            "calls": calls,
            "effects": effects,
            "obligations": obligations,
            "diagnostics": diagnostics,
            "stats": stats,
            "provenance": provenance,
        }
        self._install(_object(freeze_snapshot(raw)))

    def _install(self, data: FrozenJsonObject) -> None:
        engine = _text(data["engine"])
        ledger = GuaranteeLedger(
            _text(data["solver_status"]), _text(data["coverage_status"]), _text(data["profile"])
        )
        version = data["schema_version"]
        if type(version) is not int:
            raise TypeError("Snapshot schema version must be an integer")
        phases = _array(data["phase_trace"])
        values = _object(data["values"])
        module_exits = _object(data["module_exits"])
        returns = _object(data["returns"])
        outcomes = _object(data["outcomes"])
        calls = _array(data["calls"])
        effects = _object(data["effects"])
        obligations = _array(data["obligations"])
        diagnostics = _array(data["diagnostics"])
        stats = _object(data["stats"])
        provenance = _object(data["provenance"])
        record: dict[str, FrozenJsonValue] = {
            "engine": engine,
            "solver_status": ledger.solver_status,
            "coverage_status": ledger.coverage_status,
            "profile": ledger.profile,
            "schema_version": version,
            "phase_trace": phases,
            "values": values,
            "module_exits": module_exits,
            "returns": returns,
            "outcomes": outcomes,
            "calls": calls,
            "effects": effects,
            "obligations": obligations,
            "diagnostics": diagnostics,
            "stats": stats,
            "provenance": provenance,
        }
        frozen = MappingProxyType(record)
        object.__setattr__(self, "engine", engine)
        object.__setattr__(self, "_guarantees", ledger)
        object.__setattr__(self, "schema_version", version)
        object.__setattr__(self, "phase_trace", phases)
        object.__setattr__(self, "values", ValueFacts(values, frozen))
        object.__setattr__(self, "module_exits", module_exits)
        object.__setattr__(self, "returns", returns)
        object.__setattr__(self, "outcomes", outcomes)
        object.__setattr__(self, "calls", calls)
        object.__setattr__(self, "effects", effects)
        object.__setattr__(self, "obligations", obligations)
        object.__setattr__(self, "diagnostics", diagnostics)
        object.__setattr__(self, "stats", stats)
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "_data", frozen)

    @property
    def solver_status(self) -> str:
        return self._guarantees.solver_status

    @property
    def coverage_status(self) -> str:
        return self._guarantees.coverage_status

    @property
    def profile(self) -> str:
        return self._guarantees.profile

    @property
    def guarantees(self) -> GuaranteeLedger:
        return self._guarantees

    @override
    def __getitem__(self, key: str) -> FrozenJsonValue:
        return self._data[key]

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    @override
    def __len__(self) -> int:
        return len(self._data)

    def to_dict(self) -> JsonObject:
        thawed = thaw_snapshot(self._data)
        if not isinstance(thawed, dict):
            raise TypeError("Snapshot must thaw to an object")
        return thawed

    @classmethod
    def from_wire(cls, data: object) -> EngineSnapshot:
        frozen = _object(freeze_snapshot(data))
        unknown = frozen.keys() - _FIELDS
        missing = _FIELDS - frozen.keys()
        if unknown:
            raise ValueError(f"Unknown snapshot fields: {sorted(unknown)}")
        if missing:
            raise ValueError(f"Missing snapshot fields: {sorted(missing)}")
        snapshot = object.__new__(cls)
        snapshot._install(frozen)
        return snapshot
