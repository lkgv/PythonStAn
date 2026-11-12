"""Run-local, nominally typed identity substitutions and publication epochs.

Preparing a substitution is pure. Publication replaces the complete alias map
and increments one epoch. Object and cell namespaces have separate typed ports;
legacy mixed-map projection belongs to the host state boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from pythonstan.analysis.contracts.identity import CellId, IdentityEpoch, ObjectId


@dataclass(frozen=True)
class ReferenceKey:
    """Opaque host reference key, distinct from every target tuple/string."""

    identifier: ObjectId


@dataclass(frozen=True)
class IdentitySubstitution:
    """Detached substitutions whose static namespaces cannot be interchanged."""

    object_aliases: Mapping[ObjectId, ObjectId] = field(default_factory=dict)
    cell_aliases: Mapping[CellId, CellId] = field(default_factory=dict)

    def __post_init__(self) -> None:
        objects: Mapping[ObjectId, ObjectId] = MappingProxyType(dict(self.object_aliases))
        cells: Mapping[CellId, CellId] = MappingProxyType(dict(self.cell_aliases))
        object.__setattr__(self, "object_aliases", objects)
        object.__setattr__(self, "cell_aliases", cells)


def resolve_mapping[Identity: str](
    identifier: Identity, mapping: Mapping[Identity, Identity]
) -> Identity:
    seen: set[Identity] = set()
    current = identifier
    while current in mapping and mapping[current] != current:
        if current in seen:
            raise ValueError("Cyclic address substitution")
        seen.add(current)
        current = mapping[current]
    return current


class IdentityArena:
    aliases: dict[ObjectId, ObjectId]
    cell_aliases: dict[CellId, CellId]
    epoch: IdentityEpoch
    summaries: set[ObjectId]

    def __init__(self) -> None:
        self.aliases = {}
        self.cell_aliases = {}
        self.epoch = IdentityEpoch(0)
        self.summaries = set()

    def resolve(self, identifier: ObjectId) -> ObjectId:
        return resolve_mapping(identifier, self.aliases)

    def resolve_cell(self, identifier: CellId) -> CellId:
        return resolve_mapping(identifier, self.cell_aliases)

    def prepare(self, mapping: Mapping[ObjectId, ObjectId]) -> dict[ObjectId, ObjectId]:
        if any(type(key) is not str or type(value) is not str for key, value in mapping.items()):
            raise ValueError("Address substitutions require opaque string identities")
        combined = dict(self.aliases)
        for old, new in mapping.items():
            old = self.resolve(old)
            new = self.resolve(new)
            if old != new:
                combined[old] = new
        resolved: dict[ObjectId, ObjectId] = {}
        for old in combined:
            path: list[ObjectId] = []
            seen: set[ObjectId] = set()
            current = old
            while current in combined and combined[current] != current and current not in resolved:
                if current in seen:
                    raise ValueError("Cyclic address substitution")
                seen.add(current)
                path.append(current)
                current = combined[current]
            target = resolved.get(current, current)
            for alias in path:
                resolved[alias] = target
        return {old: new for old, new in resolved.items() if old != new}

    def publish(
        self,
        prepared: dict[ObjectId, ObjectId],
        *,
        summaries: set[ObjectId] | None = None,
        cell_aliases: Mapping[CellId, CellId] | None = None,
    ) -> IdentityEpoch:
        if prepared == self.aliases and (cell_aliases is None or cell_aliases == self.cell_aliases):
            return self.epoch
        if summaries is None:
            summaries = self.summaries | set(prepared.values())
        next_cells = self.cell_aliases if cell_aliases is None else dict(cell_aliases)
        next_epoch = IdentityEpoch(self.epoch + 1)
        self.aliases = prepared
        self.cell_aliases = next_cells
        self.summaries = summaries
        self.epoch = next_epoch
        return next_epoch

    def is_summary(self, identifier: ObjectId) -> bool:
        return self.resolve(identifier) in self.summaries
