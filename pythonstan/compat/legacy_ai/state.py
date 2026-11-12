"""Abstract state for AI intraprocedural analysis.

This module defines AIState which tracks:
- env: Variable -> AbsVal (local environment)
- heap: AIAddr -> AbsObj (heap objects)
- overlay: Intra-analysis field writes (for read-after-write)
- predicates: TAC temporary -> predicate info (for value partition refinement)
- exn: Exception state (optional)
"""

from builtins import dict as Dict
from builtins import list as List
from builtins import tuple as Tuple
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from pythonstan.analysis.domains.base import AddrSet
from pythonstan.compat.legacy_ai.domains.mapslots import field_to_keysig
from pythonstan.compat.legacy_ai.heap import AbsObj, AIAddr, Presence, Slot
from pythonstan.compat.legacy_ai.value_domain import (
    AbsVal,
    DomainRegistry,
)

# =============================================================================
# PredicateInfo - Info about TAC temporaries holding predicate results
# =============================================================================


@dataclass(frozen=True)
class PredicateInfo:
    """Information about a predicate result stored in a TAC temporary.

    This is used to track `k in d` expressions that have been assigned to
    temporaries so that ConditionRefiner can refine dict membership.

    Attributes:
        kind: Predicate kind ("dict_contains", "dict_not_contains")
        dict_var: Variable name holding the dict
        key_var: Variable name holding the key (or None for literal keys)
        key_literal: Literal key value (str/int) if key_var is None
        negated: Whether the predicate is negated
    """

    kind: str
    dict_var: str
    key_var: str | None = None
    key_literal: "NativeLiteralKey" = None
    negated: bool = False


if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.context import NativeContext, Scope
    from pythonstan.analysis.pointer.kcfa.heap_model import Field, NativeLiteralKey
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.compat.legacy_ai.api import AnalysisBudget, ArgBinding, PtaQuery


def _has_object_alternatives(value: AbsVal) -> bool:
    from pythonstan.analysis.pointer.kcfa.object import ConstantObject

    return value.addr_top or any(not isinstance(obj, ConstantObject) for obj in value.addrs)


__all__ = ["AIState", "AbsVal", "PredicateInfo"]


# =============================================================================
# AIState - Abstract state for intraprocedural analysis
# =============================================================================


@dataclass
class AIState:
    """Abstract state for AI intraprocedural analysis.

    Attributes:
        env: Variable name -> AbsVal mapping
        heap: AIAddr -> AbsObj mapping (AI-local heap view)
        overlay: Intra-analysis field writes (overlay on PTA heap)
        predicates: TAC temporary -> PredicateInfo (for value partition refinement)
        exn: Exception state (optional: None = normal, else may raise)
        pta_query: Reference to PTA for querying points-to
        budget: Analysis budget for widening caps
    """

    env: dict[str, AbsVal] = field(default_factory=dict)
    heap: dict[AIAddr, AbsObj] = field(default_factory=dict)
    overlay: dict[tuple["AbstractObject", "Field"], AbsVal] = field(default_factory=dict)
    overlay_havoc: set["AbstractObject"] = field(default_factory=set)
    predicates: dict[str, PredicateInfo] = field(default_factory=dict)
    exn: AbsVal | None = None
    pta_query: "PtaQuery | None" = None
    budget: "AnalysisBudget | None" = None
    registry: DomainRegistry | None = None
    # Shared cell/global slots are state, not mutable interpreter services.
    shared_bindings: dict[tuple[str, str], AbsVal] = field(default_factory=dict)
    # Persistent canonicalization makes future accesses through old aliases work.
    address_remaps: dict["AbstractObject", "AbstractObject"] = field(default_factory=dict)
    remap_generation: int = 0

    def __post_init__(self):
        if self.registry is None:
            from pythonstan.compat.legacy_ai.domains.base import create_default_registry

            self.registry = create_default_registry(self.budget)
        self.env = {name: value.with_registry(self.registry) for name, value in self.env.items()}
        self.overlay = {
            key: value.with_registry(self.registry) for key, value in self.overlay.items()
        }
        self.shared_bindings = {
            key: value.with_registry(self.registry) for key, value in self.shared_bindings.items()
        }
        self.heap = {key: self._bind_object(value) for key, value in self.heap.items()}
        if self.exn is not None:
            self.exn = self.exn.with_registry(self.registry)

    def _bind_object(self, obj: AbsObj) -> AbsObj:
        obj = obj.copy()
        obj.slots = obj.slots.copy()
        for slot in list(obj.slots.partitions.values()) + [obj.slots.default]:
            slot.val = slot.val.with_registry(self.registry)
            if slot.key_rep is not None:
                slot.key_rep = slot.key_rep.with_registry(self.registry)
        if obj.container_slots is not None:
            obj.container_slots = obj.container_slots.copy()
            obj.container_slots.elem_bucket = obj.container_slots.elem_bucket.with_registry(
                self.registry
            )
            obj.container_slots.tracked_indices = {
                key: value.with_registry(self.registry)
                for key, value in obj.container_slots.tracked_indices.items()
            }
        return obj

    def copy(self) -> "AIState":
        """Create a deep-ish copy (copies env/heap dicts)."""
        return AIState(
            env=dict(self.env),
            heap={k: v.copy() for k, v in self.heap.items()},
            overlay=dict(self.overlay),
            overlay_havoc=set(self.overlay_havoc),
            predicates=dict(self.predicates),
            exn=self.exn,
            pta_query=self.pta_query,
            budget=self.budget,
            registry=self.registry,
            shared_bindings=dict(self.shared_bindings),
            address_remaps=dict(self.address_remaps),
            remap_generation=self.remap_generation,
        )

    @classmethod
    def bottom(
        cls, pta_query: Optional["PtaQuery"] = None, budget: Optional["AnalysisBudget"] = None
    ) -> "AIState":
        """Create bottom state (unreachable)."""
        return cls(pta_query=pta_query, budget=budget)

    @classmethod
    def from_arg_bindings(
        cls,
        arg_bindings: List["ArgBinding"],
        pta_query: Optional["PtaQuery"] = None,
        budget: Optional["AnalysisBudget"] = None,
    ) -> "AIState":
        """Create initial state from argument bindings.

        Args:
            arg_bindings: Parameter -> PointsToSet bindings from caller
            pta_query: PTA query interface
            budget: Analysis budget

        Returns:
            Initial AIState with parameters bound
        """
        env: Dict[str, AbsVal] = {}
        from pythonstan.compat.legacy_ai.domains.base import create_default_registry

        registry = create_default_registry(budget)
        for binding in arg_bindings:
            # Prefer full AbsVal if provided (includes domain components)
            if binding.value is not None:
                reduced = registry.reduce_value(
                    binding.param_name,
                    frozenset() if binding.value.addr_top else binding.value.addrs,
                    dict(binding.value.components),
                    budget=budget,
                    scalar_kinds=binding.value.scalar_kinds,
                    has_object_alternatives=_has_object_alternatives(binding.value),
                )
                env[binding.param_name] = binding.value.with_components(reduced)
            else:
                val = AbsVal.from_addrs(binding.points_to)
                reduced = registry.reduce_value(
                    binding.param_name,
                    val.addrs,
                    dict(val.components),
                    budget=budget,
                    scalar_kinds=val.scalar_kinds,
                    has_object_alternatives=_has_object_alternatives(val),
                )
                env[binding.param_name] = val.with_components(reduced)

        return cls(env=env, pta_query=pta_query, budget=budget, registry=registry)

    def _reduce_value(self, var: str, val: AbsVal) -> AbsVal:
        """Run reduction across domains for a variable value."""
        if self.registry is None:
            return val
        val = self.reabstract_value(val).with_registry(self.registry)
        components = self.registry.reduce_value(
            var,
            frozenset() if val.addr_top else val.addrs,
            dict(val.components),
            budget=self.budget,
            scalar_kinds=val.scalar_kinds,
            has_object_alternatives=_has_object_alternatives(val),
        )
        return val.with_components(components)

    # -------------------------------------------------------------------------
    # Environment operations
    # -------------------------------------------------------------------------

    def get_var(self, name: str) -> AbsVal:
        """Get abstract value for variable."""
        return self.env.get(name, AbsVal.bottom(self.registry))

    def set_var(self, name: str, val: AbsVal) -> "AIState":
        """Set variable value (returns new state)."""
        new_state = self.copy()
        new_state._forget_predicates_for(name)
        new_state.env[name] = new_state._reduce_value(name, val)
        return new_state

    def weak_set_var(self, name: str, val: AbsVal) -> "AIState":
        """Weak update variable (join with existing)."""
        new_state = self.copy()
        new_state._forget_predicates_for(name)
        if name in new_state.env:
            joined = new_state.env[name].join(val)
            new_state.env[name] = new_state._reduce_value(name, joined)
        else:
            new_state.env[name] = new_state._reduce_value(name, val)
        return new_state

    def _forget_predicates_for(self, name: str) -> None:
        """Invalidate facts whose result or operands have been reassigned."""
        self.predicates = {
            result: pred
            for result, pred in self.predicates.items()
            if result != name and pred.dict_var != name and pred.key_var != name
        }

    def _forget_heap_predicates_for(self, addr: AIAddr) -> None:
        """Membership facts cannot refine a dictionary changed through aliases."""

        def may_alias(pred: PredicateInfo) -> bool:
            receivers = self.get_var(pred.dict_var).addrs
            return not receivers or any(
                getattr(obj, "alloc_site", None) == addr.alloc_site for obj in receivers
            )

        self.predicates = {
            result: pred for result, pred in self.predicates.items() if not may_alias(pred)
        }

    # -------------------------------------------------------------------------
    # Overlay heap operations (intra-analysis view)
    # -------------------------------------------------------------------------

    def add_overlay_write(
        self,
        obj: "AbstractObject",
        field_key: "Field",
        value: AbsVal,
    ) -> "AIState":
        """Weakly update overlay for a field write."""
        obj = self.canonical_object(obj)
        if value.is_bottom():
            return self
        value = self.reabstract_value(value)
        new_state = self.copy()
        new_state._forget_heap_predicates_for(AIAddr.from_kcfa_obj(obj))
        key = (obj, field_key)
        existing = new_state.overlay.get(key, AbsVal.bottom(self.registry))
        new_state.overlay[key] = existing.join(value)
        return new_state

    def add_overlay_havoc(self, obj: "AbstractObject") -> "AIState":
        """Mark an object as having unknown writes within this analysis."""
        obj = self.canonical_object(obj)
        new_state = self.copy()
        new_state._forget_heap_predicates_for(AIAddr.from_kcfa_obj(obj))
        new_state.overlay_havoc.add(obj)
        return new_state

    def read_field_value(
        self,
        obj: "AbstractObject",
        field_key: "Field",
        caller_scope: Optional["Scope"],
        call_context: "NativeContext | None",
        *,
        unknown_is_residual: bool = False,
    ) -> Tuple[AbsVal, bool]:
        """Read field value with overlay + AI heap + PTA fallback."""
        from pythonstan.analysis.pointer.kcfa.heap_model import FieldKind, unknown

        obj = self.canonical_object(obj)
        overlay_val = self.overlay.get((obj, field_key), AbsVal.bottom(self.registry))
        if field_key.kind == FieldKind.UNKNOWN and not unknown_is_residual:
            # A dynamic attribute read may select any recorded named write.
            for (owner, written_field), value in self.overlay.items():
                if owner == obj and written_field.kind == FieldKind.ATTRIBUTE:
                    overlay_val = overlay_val.join(value)
        elif field_key.kind == FieldKind.ATTRIBUTE:
            # A write through an unknown name may alias this named field.
            overlay_val = overlay_val.join(
                self.overlay.get((obj, unknown()), AbsVal.bottom(self.registry))
            )
        havoc = obj in self.overlay_havoc

        heap_val = AbsVal.bottom(self.registry)
        ai_addr = AIAddr.from_kcfa_obj(obj)
        ai_obj = self.heap.get(ai_addr)
        if ai_obj is not None:
            # Convert Field to KeySig and read from unified SlotMap
            keysig = field_to_keysig(field_key)
            if keysig is not None:
                slot = ai_obj.slots.effective_slot(keysig)
                # Tracked keys are excluded from residual default. Unknown
                # writes update tracked partitions at the write boundary.
                heap_val = slot.val if slot.pres.may_be_present() else AbsVal.bottom(self.registry)
            else:
                # Residual buckets accompany a named read; wildcard reads may
                # select any named field as well. Do not conflate the two.
                heap_val = (
                    ai_obj.slots.default.val
                    if unknown_is_residual
                    else ai_obj.slots.get_all_values()
                )

        pta_val = AbsVal.bottom(self.registry)
        if self.pta_query is not None:
            try:
                pta_pts = self.pta_query.field_points_to(obj, field_key, caller_scope, call_context)
                pta_val = AbsVal.from_addrs(frozenset(pta_pts))
                if field_key.kind == FieldKind.ATTRIBUTE:
                    residual_pts = self.pta_query.field_points_to(
                        obj, unknown(), caller_scope, call_context
                    )
                    pta_val = pta_val.join(AbsVal.from_addrs(frozenset(residual_pts)))
            except Exception:
                # Query failure is unknown information, not evidence of absence.
                havoc = True

        merged = overlay_val.join(heap_val).join(pta_val)
        return merged, havoc or merged.addr_top

    def read_field_points_to(
        self,
        obj: "AbstractObject",
        field_key: "Field",
        caller_scope: Optional["Scope"],
        call_context: "NativeContext | None",
    ) -> Tuple[AddrSet, bool]:
        """Read field points-to with overlay, returning (pts, is_havoc)."""
        val, havoc = self.read_field_value(obj, field_key, caller_scope, call_context)
        return val.addrs, havoc

    def write_field_value(
        self,
        obj: "AbstractObject",
        field_key: "Field",
        value: AbsVal,
        update_heap: bool = False,
    ) -> "AIState":
        """Weakly update overlay (and optional AI heap) for a field write."""
        obj = self.canonical_object(obj)
        new_state = self.add_overlay_write(obj, field_key, value)
        if update_heap:
            ai_addr = AIAddr.from_kcfa_obj(obj)
            ai_obj = new_state.heap.get(ai_addr)
            if ai_obj is not None:
                # Convert Field to KeySig and write to unified SlotMap
                keysig = field_to_keysig(field_key)
                if keysig is not None:
                    new_slots = ai_obj.slots.weak_update(keysig, value, Presence.PRESENT)
                    new_state = new_state.set_obj(ai_addr, ai_obj.with_slots(new_slots))
                else:
                    # Unknown field - update default bucket
                    new_slots = ai_obj.slots.weak_update_default(value, Presence.PRESENT)
                    new_state = new_state.set_obj(ai_addr, ai_obj.with_slots(new_slots))
        return new_state

    def canonical_object(self, obj: "AbstractObject") -> "AbstractObject":
        seen: set[AbstractObject] = set()
        while obj in self.address_remaps and obj not in seen:
            seen.add(obj)
            obj = self.address_remaps[obj]
        return obj

    def canonical_address(self, addr: AIAddr) -> AIAddr:
        for old, new in self.address_remaps.items():
            if AIAddr.from_kcfa_obj(old) == addr:
                return AIAddr.from_kcfa_obj(self.canonical_object(new))
        return addr

    def reabstract_value(self, value: AbsVal) -> AbsVal:
        if not self.address_remaps:
            return value
        return self._remap_payload(value, self.address_remaps)

    @staticmethod
    def _remap_payload(value, mapping, record_induced=False):
        """Map references inside values, keys and token payloads, never source IR."""
        from dataclasses import fields, is_dataclass, replace

        from pythonstan.analysis.pointer.kcfa.object import AbstractObject

        def remap(item):
            if isinstance(item, AbsVal):
                return AbsVal(
                    frozenset(remap(x) for x in item.addrs),
                    {name: remap(x) for name, x in item.components.items()},
                    addr_top=item.addr_top,
                    registry=item.registry,
                    scalar_kinds=item.scalar_kinds,
                    boolean_values=item.boolean_values,
                )
            if isinstance(item, AbstractObject):
                current = mapping.get(item, item)
                changes = {}
                for f in fields(current):
                    if f.name in ("context", "alloc_site", "container_scope", "ir", "receiver_var"):
                        continue
                    old = getattr(current, f.name)
                    new = remap(old)
                    if new != old:
                        changes[f.name] = new
                transformed = replace(current, **changes) if changes else current
                if record_induced and transformed != item:
                    mapping[item] = transformed
                return transformed
            if isinstance(item, tuple):
                return tuple(remap(x) for x in item)
            if isinstance(item, frozenset):
                return frozenset(remap(x) for x in item)
            if isinstance(item, set):
                return {remap(x) for x in item}
            if isinstance(item, list):
                return [remap(x) for x in item]
            if isinstance(item, dict):
                return {remap(k): remap(v) for k, v in item.items()}
            if is_dataclass(item) and item.__class__.__module__.startswith(
                (
                    "pythonstan.analysis.pointer.ai.domains",
                    "pythonstan.compat.legacy_ai.domains",
                    "pythonstan.analysis.domains",
                )
            ):
                return replace(item, **{f.name: remap(getattr(item, f.name)) for f in fields(item)})
            return item

        return remap(value)

    def reabstract(self, mapping) -> "AIState":
        """Apply one covering identity map to every state component atomically.

        Mapping is retained for future old-alias reads/writes. Hosts consume the
        versioned address_remaps ledger to migrate dependency subscriptions.
        """
        from pythonstan.compat.legacy_ai.value_domain import AbsVal

        combined = {**self.address_remaps, **mapping}
        # Compose prior maps so no callback or future consumer sees stale hops.
        for old, new in list(combined.items()):
            seen = {old}
            while new in combined and new not in seen:
                seen.add(new)
                new = combined[new]
            combined[old] = new

        def remap(value):
            return self._remap_payload(value, combined, record_induced=True)

        def remap_addr(addr):
            for old, new in combined.items():
                if AIAddr.from_kcfa_obj(old) == addr:
                    return AIAddr.from_kcfa_obj(new)
            return addr

        result = self.copy()
        result.address_remaps = combined
        result.remap_generation += 1
        result.env = {name: remap(value) for name, value in self.env.items()}
        result.shared_bindings = {key: remap(value) for key, value in self.shared_bindings.items()}
        result.overlay = {}
        for (owner, key), value in self.overlay.items():
            new_key = (remap(owner), remap(key))
            result.overlay[new_key] = result.overlay.get(
                new_key, AbsVal.bottom(self.registry)
            ).join(remap(value))
        result.overlay_havoc = {remap(owner) for owner in self.overlay_havoc}
        result.heap = {}
        for addr, obj in self.heap.items():
            new_addr = remap_addr(addr)
            new_obj = obj.copy()
            new_obj.addr = new_addr
            new_obj.cls_addrs = remap(obj.cls_addrs)
            slots = obj.slots.copy()
            slots.partitions = {}

            def mapped_slot(slot):
                return Slot(
                    remap(slot.val),
                    slot.pres,
                    remap(slot.key_rep) if slot.key_rep is not None else None,
                )

            for key, slot in obj.slots.partitions.items():
                key = remap(key)
                transformed = mapped_slot(slot)
                slots.partitions[key] = (
                    slots.partitions[key].join(transformed)
                    if key in slots.partitions
                    else transformed
                )
            slots.default = mapped_slot(obj.slots.default)
            new_obj.slots = slots
            if obj.container_slots is not None:
                container = obj.container_slots.copy()
                container.elem_bucket = remap(container.elem_bucket)
                container.tracked_indices = remap(container.tracked_indices)
                new_obj.container_slots = container
            result.heap[new_addr] = (
                result.heap[new_addr].join(new_obj) if new_addr in result.heap else new_obj
            )
        result.exn = remap(self.exn) if self.exn is not None else None
        # Branch predicates involving a changed heap identity may no longer be
        # justified; discarding constraints is a covering abstraction.
        result.predicates = {} if mapping else dict(self.predicates)
        return result

    def _widen_address_mapping(
        self, other: "AIState", max_addrs: int
    ) -> "dict[AbstractObject, AbstractObject]":
        from pythonstan.analysis.pointer.kcfa.object import AbstractObject, summarize_object

        mapping: dict[AbstractObject, AbstractObject] = {}
        joined = self.join(other)
        values = (
            list(joined.env.values())
            + list(joined.overlay.values())
            + list(joined.shared_bindings.values())
        )
        if joined.exn is not None:
            values.append(joined.exn)
        for heap_object in joined.heap.values():
            for slot in list(heap_object.slots.partitions.values()) + [heap_object.slots.default]:
                values.append(slot.val)
                if slot.key_rep is not None:
                    values.append(slot.key_rep)
            if heap_object.container_slots is not None:
                values.append(heap_object.container_slots.elem_bucket)
                values.extend(heap_object.container_slots.tracked_indices.values())
        for value in values:
            if len(value.addrs) > max_addrs:
                for obj in value.addrs:
                    if isinstance(obj, AbstractObject):
                        summary = summarize_object(obj)
                        if summary != obj:
                            mapping[obj] = summary
        return mapping

    # -------------------------------------------------------------------------
    # Heap operations (AI-local view)
    # -------------------------------------------------------------------------

    def get_obj(self, addr: AIAddr) -> Optional[AbsObj]:
        """Get heap object at address."""
        return self.heap.get(self.canonical_address(addr))

    def set_obj(self, addr: AIAddr, obj: AbsObj) -> "AIState":
        """Set heap object (returns new state)."""
        new_state = self.copy()
        new_state._forget_heap_predicates_for(addr)
        addr = self.canonical_address(addr)
        obj = obj.copy()
        obj.addr = addr
        new_state.heap[addr] = new_state._bind_object(obj)
        return new_state

    def update_obj(self, addr: AIAddr, obj: AbsObj) -> "AIState":
        """Weakly join a heap object after run-local binding/canonicalization."""
        addr = self.canonical_address(addr)
        obj = obj.copy()
        obj.addr = addr
        obj = self._bind_object(obj)
        previous = self.heap.get(addr)
        return self.set_obj(addr, previous.join(obj) if previous is not None else obj)

    # -------------------------------------------------------------------------
    # Lattice operations
    # -------------------------------------------------------------------------

    def is_bottom(self) -> bool:
        """Check if state is bottom (unreachable)."""
        return (
            len(self.env) == 0
            and len(self.heap) == 0
            and len(self.overlay) == 0
            and not self.shared_bindings
        )

    def join(self, other: "AIState") -> "AIState":
        """Join two states (⊔)."""
        # Join environments
        new_env: Dict[str, AbsVal] = {}
        all_vars = set(self.env.keys()) | set(other.env.keys())
        for v in all_vars:
            v1 = self.env.get(v, AbsVal.bottom(self.registry))
            v2 = other.env.get(v, AbsVal.bottom(self.registry))
            joined = v1.join(v2)
            new_env[v] = self._reduce_value(v, joined)

        # Join heaps
        new_heap: Dict[AIAddr, AbsObj] = {}
        all_addrs = set(self.heap.keys()) | set(other.heap.keys())
        for a in all_addrs:
            if a in self.heap and a in other.heap:
                new_heap[a] = self.heap[a].join(other.heap[a])
            elif a in self.heap:
                new_heap[a] = self.heap[a]
            else:
                new_heap[a] = other.heap[a]

        # Join overlays
        new_overlay: Dict[Tuple[AbstractObject, Field], AbsVal] = {}
        all_overlay_keys = set(self.overlay.keys()) | set(other.overlay.keys())
        for k in all_overlay_keys:
            v1 = self.overlay.get(k, AbsVal.bottom(self.registry))
            v2 = other.overlay.get(k, AbsVal.bottom(self.registry))
            new_overlay[k] = v1.join(v2)

        new_overlay_havoc = self.overlay_havoc | other.overlay_havoc

        # Join predicates (keep common predicates)
        new_predicates: Dict[str, PredicateInfo] = {}
        for name in self.predicates:
            if name in other.predicates and self.predicates[name] == other.predicates[name]:
                new_predicates[name] = self.predicates[name]

        # Join exceptions
        new_exn = None
        if self.exn is not None and other.exn is not None:
            new_exn = self.exn.join(other.exn)
        elif self.exn is not None:
            new_exn = self.exn
        elif other.exn is not None:
            new_exn = other.exn

        return AIState(
            env=new_env,
            heap=new_heap,
            overlay=new_overlay,
            overlay_havoc=new_overlay_havoc,
            predicates=new_predicates,
            exn=new_exn,
            pta_query=self.pta_query,
            budget=self.budget,
            registry=self.registry,
            address_remaps={**self.address_remaps, **other.address_remaps},
            remap_generation=max(self.remap_generation, other.remap_generation),
            shared_bindings={
                key: self.shared_bindings.get(key, AbsVal.bottom(self.registry)).join(
                    other.shared_bindings.get(key, AbsVal.bottom(self.registry))
                )
                for key in self.shared_bindings.keys() | other.shared_bindings.keys()
            },
        )

    def leq(self, other: "AIState") -> bool:
        """Check if self ⊑ other."""
        # Compare effective states after the target's covering remap, including
        # local heap and overlay; env-only address inclusion is insufficient.
        if other.address_remaps and any(
            old != new
            for old, new in other.address_remaps.items()
            if self.address_remaps.get(old) != new
        ):
            return self.reabstract(other.address_remaps).leq(other)
        for key, value in self.shared_bindings.items():
            if not value.leq(other.shared_bindings.get(key, AbsVal.bottom(self.registry))):
                return False
        # Check all vars in self
        for v, val in self.env.items():
            other_val = other.env.get(v, AbsVal.bottom(self.registry))
            if not val.leq(other_val):
                return False

        # Check heap objects
        for a, obj in self.heap.items():
            if a not in other.heap:
                return False
            # Use AbsObj.leq which includes slots (unified SlotMap) and container_slots
            if not obj.leq(other.heap[a]):
                return False

        # Check overlays
        for k, v in self.overlay.items():
            other_v = other.overlay.get(k, AbsVal.bottom(self.registry))
            if not v.leq(other_v):
                return False
        if not self.overlay_havoc <= other.overlay_havoc:
            return False

        # Predicate facts are constraints: more facts means a smaller state.
        # Every fact required by ``other`` must therefore hold in ``self``.
        for k, pred in other.predicates.items():
            if self.predicates.get(k) != pred:
                return False

        # Exception growth must trigger successor processing just like env/heap
        # growth. None means no recorded exceptional component.
        if self.exn is not None:
            if other.exn is None or not self.exn.leq(other.exn):
                return False

        return True

    def widen(self, other: "AIState") -> "AIState":
        """Widen state (∇).

        Apply widening to address sets, string sets, and field stores.
        From ai_spec.md §10.1: address-set widening caps growth via summarization.
        """
        max_addrs = self.budget.max_addr_set_size if self.budget else 50
        mapping = self._widen_address_mapping(other, max_addrs)
        if mapping:
            return self.reabstract(mapping).widen(other.reabstract(mapping))

        # Widen environments
        new_env: Dict[str, AbsVal] = {}
        all_vars = set(self.env.keys()) | set(other.env.keys())
        for v in all_vars:
            v1 = self.env.get(v, AbsVal.bottom(self.registry))
            v2 = other.env.get(v, AbsVal.bottom(self.registry))
            widened = v1.widen(
                v2,
                max_addrs=max_addrs,
                max_strs=self.budget.max_const_strings if self.budget else None,
                max_ho_tokens=self.budget.max_ho_tokens if self.budget else None,
            )
            new_env[v] = self._reduce_value(v, widened)

        # Widen heaps
        new_heap: Dict[AIAddr, AbsObj] = {}
        all_addrs = set(self.heap.keys()) | set(other.heap.keys())
        for a in all_addrs:
            if a in self.heap and a in other.heap:
                new_heap[a] = self.heap[a].widen(other.heap[a])
            elif a in self.heap:
                new_heap[a] = self.heap[a]
            else:
                new_heap[a] = other.heap[a]

        # Widen overlays (use join semantics)
        new_overlay: Dict[Tuple[AbstractObject, Field], AbsVal] = {}
        all_overlay_keys = set(self.overlay.keys()) | set(other.overlay.keys())
        for k in all_overlay_keys:
            v1 = self.overlay.get(k, AbsVal.bottom(self.registry))
            v2 = other.overlay.get(k, AbsVal.bottom(self.registry))
            new_overlay[k] = v1.join(v2)
        new_overlay_havoc = self.overlay_havoc | other.overlay_havoc

        # Widen predicates (keep common predicates)
        new_predicates: Dict[str, PredicateInfo] = {}
        for name in self.predicates:
            if name in other.predicates and self.predicates[name] == other.predicates[name]:
                new_predicates[name] = self.predicates[name]

        return AIState(
            env=new_env,
            heap=new_heap,
            overlay=new_overlay,
            overlay_havoc=new_overlay_havoc,
            predicates=new_predicates,
            exn=self.exn.join(other.exn) if self.exn and other.exn else self.exn or other.exn,
            pta_query=self.pta_query,
            budget=self.budget,
            registry=self.registry,
            address_remaps={**self.address_remaps, **other.address_remaps},
            remap_generation=max(self.remap_generation, other.remap_generation),
            shared_bindings={
                key: self.shared_bindings.get(key, AbsVal.bottom(self.registry)).widen(
                    other.shared_bindings.get(key, AbsVal.bottom(self.registry)),
                    max_addrs=max_addrs,
                )
                for key in self.shared_bindings.keys() | other.shared_bindings.keys()
            },
        )

    def narrow(self, other: "AIState") -> "AIState":
        """Narrow state (△).

        Apply meet to refine after widening. Narrows both environment and heap.
        From ai_spec.md §10.6: narrowing uses meet with branch constraints.
        """
        # Narrow environments
        new_env: Dict[str, AbsVal] = {}
        for v in self.env:
            if v in other.env:
                narrowed = self.env[v].meet(other.env[v])
                new_env[v] = self._reduce_value(v, narrowed)
            else:
                new_env[v] = self.env[v]

        # Narrow heaps: apply meet to matching objects
        new_heap: Dict[AIAddr, AbsObj] = {}
        for a in self.heap:
            if a in other.heap:
                # Narrow the heap object
                new_heap[a] = self.heap[a].narrow(other.heap[a])
            else:
                new_heap[a] = self.heap[a]

        # Narrow overlays (intersection where possible)
        new_overlay: Dict[Tuple[AbstractObject, Field], AbsVal] = {}
        for k, value in self.overlay.items():
            if k in other.overlay:
                new_overlay[k] = value.meet(other.overlay[k])
            else:
                new_overlay[k] = value
        new_overlay_havoc = self.overlay_havoc & other.overlay_havoc

        # Narrow predicates (union - keep all predicates from both)
        new_predicates: Dict[str, PredicateInfo] = dict(self.predicates)
        new_predicates.update(other.predicates)

        return AIState(
            env=new_env,
            heap=new_heap,
            overlay=new_overlay,
            overlay_havoc=new_overlay_havoc,
            predicates=new_predicates,
            exn=self.exn,
            pta_query=self.pta_query,
            budget=self.budget,
            registry=self.registry,
            address_remaps={**self.address_remaps, **other.address_remaps},
            remap_generation=max(self.remap_generation, other.remap_generation),
            shared_bindings={
                key: value.meet(other.shared_bindings[key])
                if key in other.shared_bindings
                else value
                for key, value in self.shared_bindings.items()
            },
        )

    # -------------------------------------------------------------------------
    # Predicate operations (for value partition refinement)
    # -------------------------------------------------------------------------

    def add_predicate(self, var_name: str, info: PredicateInfo) -> "AIState":
        """Add a predicate for a TAC temporary variable."""
        new_state = self.copy()
        new_state.predicates[var_name] = info
        return new_state

    def get_predicate(self, var_name: str) -> Optional[PredicateInfo]:
        """Get predicate info for a TAC temporary variable."""
        return self.predicates.get(var_name)

    def __str__(self) -> str:
        env_str = ", ".join(f"{k}={v}" for k, v in sorted(self.env.items()))
        return f"AIState(env={{{env_str}}}, heap={len(self.heap)} objs, overlay={len(self.overlay)}, preds={len(self.predicates)})"
