"""Final-state write footprints consumed by interprocedural state application.

These records name locations in a completion's poststate. They are not an
ordered replay log: a may-delete merged with a may-write still materializes the
joined poststate, including its value and missing alternative.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import assert_never

from pythonstan.analysis.contracts.identity import CellId, ObjectId
from pythonstan.analysis.domains.reabstract import ReferenceKey, resolve_mapping

from .keys import FieldKey


class MetadataAspect(Enum):
    ITERATOR_STATE = "iterator_state"
    GENERATOR_STATE = "generator_state"
    LOCALS_REFRESH = "locals_refresh"
    CLASS_SHAPE = "class_shape"
    FUNCTION_STATE = "function_state"
    COMPONENT_ATTACHMENT = "component_attachment"


@dataclass(frozen=True, slots=True)
class CellWrite:
    cell: CellId
    weak: bool = False


@dataclass(frozen=True, slots=True)
class HeapWrite:
    owner: ObjectId
    key: FieldKey


@dataclass(frozen=True, slots=True)
class HeapDelete:
    owner: ObjectId
    key: FieldKey


@dataclass(frozen=True, slots=True)
class HeapDefaultWrite:
    owner: ObjectId


@dataclass(frozen=True, slots=True)
class MetadataUpdate:
    owner: ObjectId
    aspect: MetadataAspect


@dataclass(frozen=True, slots=True)
class Havoc:
    objects: frozenset[ObjectId] = frozenset()
    cells: frozenset[CellId] = frozenset()
    namespaces: frozenset[str] = frozenset()
    imports: bool = False


type WriteEffect = CellWrite | HeapWrite | HeapDelete | HeapDefaultWrite | MetadataUpdate | Havoc


def remap_field_key(key: FieldKey, aliases: Mapping[ObjectId, ObjectId]) -> FieldKey:
    """Only explicit reference keys carry identity; target strings stay literal."""
    if isinstance(key, ReferenceKey):
        return ReferenceKey(resolve_mapping(key.identifier, aliases))
    if isinstance(key, tuple):
        return tuple(remap_field_key(item, aliases) for item in key)
    return key


def remap_effect(
    effect: WriteEffect,
    object_aliases: Mapping[ObjectId, ObjectId],
    cell_aliases: Mapping[CellId, CellId],
) -> WriteEffect:
    match effect:
        case CellWrite(cell, weak):
            return CellWrite(resolve_mapping(cell, cell_aliases), weak)
        case HeapWrite(owner, key):
            return HeapWrite(
                resolve_mapping(owner, object_aliases), remap_field_key(key, object_aliases)
            )
        case HeapDelete(owner, key):
            return HeapDelete(
                resolve_mapping(owner, object_aliases), remap_field_key(key, object_aliases)
            )
        case HeapDefaultWrite(owner):
            return HeapDefaultWrite(resolve_mapping(owner, object_aliases))
        case MetadataUpdate(owner, aspect):
            return MetadataUpdate(resolve_mapping(owner, object_aliases), aspect)
        case Havoc(objects, cells, namespaces, imports):
            return Havoc(
                frozenset(resolve_mapping(owner, object_aliases) for owner in objects),
                frozenset(resolve_mapping(cell, cell_aliases) for cell in cells),
                namespaces,
                imports,
            )
        case _:
            assert_never(effect)


__all__ = [
    "CellWrite",
    "Havoc",
    "HeapDefaultWrite",
    "HeapDelete",
    "HeapWrite",
    "MetadataAspect",
    "MetadataUpdate",
    "WriteEffect",
    "remap_effect",
    "remap_field_key",
]
