"""Immutable engine snapshots and JSON wire helpers.

No solver object, mutable arena table, callback, or user object may escape into
these records. Unknown Python objects are rejected instead of repr-serialized.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, TYPE_CHECKING

from .wire import freeze_snapshot, thaw_snapshot, value_snapshot

if TYPE_CHECKING:
    from .identity import EntryScenario, SourceModule


@dataclass(frozen=True)
class EngineSnapshot(Mapping[str, object]):
    engine: str
    solver_status: str
    coverage_status: str
    profile: str = "python-core-v1"
    schema_version: int = 1
    phase_trace: tuple[object, ...] = ()
    values: Mapping[str, object] = field(default_factory=dict)
    module_exits: Mapping[str, object] = field(default_factory=dict)
    returns: Mapping[str, object] = field(default_factory=dict)
    outcomes: Mapping[str, object] = field(default_factory=dict)
    calls: tuple[object, ...] = ()
    effects: Mapping[str, object] = field(default_factory=dict)
    obligations: tuple[object, ...] = ()
    diagnostics: tuple[object, ...] = ()
    stats: Mapping[str, object] = field(default_factory=dict)
    provenance: Mapping[str, object] = field(default_factory=dict)
    _data: Mapping[str, object] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        names = (
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
        )
        data = freeze_snapshot({name: getattr(self, name) for name in names})
        object.__setattr__(self, "_data", data)
        for name in names:
            object.__setattr__(self, name, data[name])

    def __getitem__(self, key: str) -> object:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def to_dict(self) -> dict[str, object]:
        return thaw_snapshot(self._data)


class Engine(Protocol):
    def analyze(
        self,
        modules: Sequence[SourceModule],
        scenarios: Sequence[EntryScenario],
        config: Mapping[str, object],
        queries: Sequence[object] = (),
    ) -> EngineSnapshot:
        """Solve source/entry equations then freeze a self-contained result."""
        ...
