"""Cross-owner mutable alias partitions of admitted semantic graphs.

Value leaves and immutable syntax are compared elsewhere. This module checks
the topology that determines whether a later mutation is shared, using the same
finite record projection as graph copy and order.
"""

from __future__ import annotations

from pythonstan.analysis.contracts.identity import ObjectId
from pythonstan.analysis.domains.values import Value

from .boundary import BoundaryObject, BoundaryState
from .metadata import Metadata
from .store import HeapObject, Store
from .transform import _dictionary, _list, _record, _semantic_fields, _tuple


class _AliasComparison:
    def __init__(
        self,
        left: Store | None = None,
        right: Store | None = None,
        *,
        ignore_right_havoc: bool = False,
        coalesce_roots: bool = False,
    ) -> None:
        self.left = left
        self.right = right
        self.ignore_right_havoc = ignore_right_havoc
        self.coalesce_roots = coalesce_roots
        self.forward: dict[int, tuple[int, set[ObjectId]]] = {}
        self.backward: dict[int, tuple[int, set[ObjectId]]] = {}
        self.seen: set[tuple[int, int, ObjectId | None]] = set()
        self.conflicts: set[ObjectId] = set()
        self.agree = True

    def _anchor(self, left: object, right: object, owner: ObjectId | None) -> bool:
        owners = set() if owner is None else {owner}
        a, b = id(left), id(right)
        prior_a, prior_b = self.forward.get(a), self.backward.get(b)
        if prior_a is not None:
            prior_a[1].update(owners)
        if prior_b is not None:
            prior_b[1].update(owners)
        if (prior_a is not None and prior_a[0] != b) or (prior_b is not None and prior_b[0] != a):
            self.agree = False
            self.conflicts.update(owners)
            if prior_a is not None:
                self.conflicts.update(prior_a[1])
            if prior_b is not None:
                self.conflicts.update(prior_b[1])
            return False
        self.forward.setdefault(a, (b, owners))
        self.backward.setdefault(b, (a, set(owners)))
        return True

    def compare(self, left: object, right: object, owner: ObjectId | None = None) -> None:
        # Shape and Value order are independent obligations. Missing fields and
        # incompatible record types are handled by those existing predicates.
        if type(left) is not type(right) or isinstance(left, Value):
            return
        if (
            self.coalesce_roots
            and owner is not None
            and self.left is not self.right
            and (left is self.right or right is self.left)
        ):
            # Joining anchors both input roots to one result. An edge to the
            # opposite input root was detached from its own root in that
            # alternative; retaining the chosen representative as exact would
            # introduce a new must-alias relation.
            self.conflicts.add(owner)
        mutable = (
            isinstance(left, (Store, HeapObject, Metadata, BoundaryObject, BoundaryState))
            or _dictionary(left)
            or _list(left)
        )
        if mutable and not self._anchor(left, right, owner):
            return
        pair = id(left), id(right), owner
        if pair in self.seen:
            return
        self.seen.add(pair)
        if isinstance(left, Metadata) and isinstance(right, Metadata):
            left_fields, right_fields = (
                left.transformation_entries(),
                right.transformation_entries(),
            )
            for name in left_fields.keys() & right_fields.keys():
                if type(left_fields[name]) is type(right_fields[name]):
                    self.compare(left_fields[name].value, right_fields[name].value, owner)
        elif _record(left) and _record(right):
            for (_, left_field), (_, right_field) in zip(
                _semantic_fields(left), _semantic_fields(right), strict=True
            ):
                self.compare(left_field, right_field, owner)
        elif _dictionary(left) and _dictionary(right):
            top_heap = (
                self.left is not None
                and self.right is not None
                and left is self.left.heap
                and right is self.right.heap
            )
            for key in left.keys() & right.keys():
                current = ObjectId(key) if top_heap and isinstance(key, str) else owner
                if top_heap and self.ignore_right_havoc:
                    value = right[key]
                    if isinstance(value, HeapObject) and value.havoc:
                        continue
                self.compare(left[key], right[key], current)
        elif (_tuple(left) and _tuple(right)) or (_list(left) and _list(right)):
            for left_item, right_item in zip(left, right, strict=False):
                self.compare(left_item, right_item, owner)


def alias_partitions_agree(left: object, right: object) -> bool:
    """Require one global bijection for mutable nodes across both graphs."""
    comparison = _AliasComparison()
    comparison.compare(left, right)
    return comparison.agree


def store_alias_conflicts(
    left: Store,
    right: Store,
    *,
    ignore_right_havoc: bool = False,
    coalesce_roots: bool = False,
) -> frozenset[ObjectId]:
    """Return a havoc mask whose unmasked mutable topology stays compatible.

    The mask covers disagreements; it need not contain every endpoint of each
    disagreement. Root coalescing is a join obligation, not an order rule.
    """
    comparison = _AliasComparison(
        left, right, ignore_right_havoc=ignore_right_havoc, coalesce_roots=coalesce_roots
    )
    comparison.compare(left, right)
    return frozenset(comparison.conflicts)


__all__ = ["alias_partitions_agree", "store_alias_conflicts"]
