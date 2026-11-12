"""Analysis state for pointer analysis.

This module defines the state maintained during pointer analysis including
the environment (variable points-to sets) and heap (object field points-to sets).
"""

from collections import defaultdict, deque
from collections.abc import Hashable
from enum import Enum
from typing import TYPE_CHECKING, Literal, Optional, overload, override

from pythonstan.analysis.contracts.json_data import JsonObject, JsonValue
from pythonstan.graph.call_graph.call_edge import CallEdge
from pythonstan.graph.call_graph.call_graph import AbstractCallGraph
from pythonstan.ir.ir_statements import IRModule, IRScope
from pythonstan.utils.common import Box

from .constraints import Constraint, ConstraintManager, InheritanceConstraint
from .context import CallSite, Ctx, NativeContext, Scope
from .debug_boundary import feature, record_points_to_update
from .heap_model import Field, FieldKind, HeapModel, unknown
from .object import (
    AbstractObject,
    AllocSite,
    AsyncGeneratorObject,
    BuiltinClassObject,
    ClassObject,
    CoroutineObject,
    FunctionObject,
    GeneratorObject,
    InstanceObject,
    ModuleObject,
    SuperObject,
    is_summary_object,
    summarize_object,
)
from .pointer_flow_graph import (
    InheritanceSelectorNode,
    NormalNode,
    PointerFlowEdge,
    PointerFlowGraph,
    PointerFlowKind,
    PointerFlowNode,
)
from .points_to_set import PointsToSet
from .variable import FieldAccess, Variable, VariableFactory, VariableKind

if TYPE_CHECKING:
    from pythonstan.world.scope_manager import ScopeManager

    from .allocation import AllocationPolicyRegistry
    from .native_mapping import NativeMappingProtocols
    from .processor.heap_mapping import FieldCreatedObserver
    from .unknown_effects import UnknownEffects
    from .worklist import ConsumerFairWorklist

__all__ = ["PointerAnalysisState"]


class _MissingWorkItem(Enum):
    TOKEN = "missing_work_item"


_WORKLIST_MISSING = _MissingWorkItem.TOKEN

type NativePendingItem = tuple[Scope | None, PointerFlowNode, Box[PointsToSet]]
type NativePointsKey = Ctx[Variable | FieldAccess] | PointerFlowNode | Variable | FieldAccess


def _lexical_capture_owner(obj: AbstractObject) -> AbstractObject:
    if isinstance(obj, (GeneratorObject, CoroutineObject, AsyncGeneratorObject)):
        return obj.func_obj
    return obj


# TODO to be done
class PointerCallGraph(AbstractCallGraph[Ctx[CallSite], Scope]):
    def __init__(self) -> None:
        super().__init__()
        self.visited_edges: set[tuple[Ctx[CallSite], Scope]] = set()
        self.plain_edges: set[tuple[CallSite, IRScope]] = set()
        self.non_empty: set[IRScope] = set()

    @override
    def add_edge(self, edge: CallEdge[Ctx[CallSite], Scope]) -> None:
        super().add_edge(edge)
        self.visited_edges.add((edge.callsite, edge.callee))
        self.plain_edges.add((edge.callsite.content, edge.callee.stmt))
        self.non_empty.add(edge.callee.stmt)

    @override
    def has_edge(self, edge: CallEdge[Ctx[CallSite], Scope]) -> bool:
        return (edge.callsite, edge.callee) in self.visited_edges

    def num_plain_edges(self) -> int:
        return len(self.plain_edges)


class Worklist:
    """Deterministic worklist using list-based storage.

    Uses a list for deterministic ordering and a dict for fast lookup.
    Items are processed in FIFO order for better determinism.
    """

    items_list: deque[NativePendingItem]
    items_dict: dict[PointerFlowNode, NativePendingItem]

    def __init__(self) -> None:
        self.items_list = deque()
        self.items_dict = {}
        self._next_index = 0

    def add(self, content: tuple[Scope | None, PointerFlowNode, PointsToSet]) -> None:
        scope, node, pts = content
        assert isinstance(node, PointerFlowNode), f"node must be a PFNode, but got {type(node)}"
        if isinstance(node, NormalNode):
            assert isinstance(node.var, Ctx), f"node.var must be a Ctx, but got {type(node.var)}"

        if type(self.items_dict) is dict:
            # Keep the canonical tuple/Box and its FIFO age on a merge. Native
            # keys have stable, pure equality; no discarded Box is needed.
            item = self.items_dict.get(node, _WORKLIST_MISSING)
            if item is not _WORKLIST_MISSING:
                item[2].val = item[2].val.union(pts)
            else:
                item = (scope, node, Box(pts))
                self.items_list.append(item)
                self.items_dict[node] = item
            return

        # Custom stores retain the former allocation and callback sequence.
        worklist_item: NativePendingItem = (scope, node, Box(pts))

        # Check if node already in worklist
        if node in self.items_dict:
            item = self.items_dict[node]
            # Merge points-to sets
            item[2].val = item[2].val.union(pts)
        else:
            # Add new item
            self.items_list.append(worklist_item)
            self.items_dict[node] = worklist_item

    def pop(self) -> tuple[Scope | None, PointerFlowNode, PointsToSet]:
        """Pop from the front (FIFO) for deterministic processing."""
        if not self.items_list:
            raise IndexError("pop from empty worklist")

        scope, node, pts = self.items_list.popleft()
        del self.items_dict[node]

        return scope, node, pts.val

    def empty(self) -> bool:
        return len(self.items_list) == 0

    def __len__(self) -> int:
        return len(self.items_list)


class PointerAnalysisState:
    """Unified analysis state container.

    Maintains the environment (variable points-to information), heap (object
    field points-to information), call graph, and constraint manager.
    """

    _native_mapping_protocols: "NativeMappingProtocols"
    _instance_namespace_views: "FieldCreatedObserver"
    _mapping_field_copies: "FieldCreatedObserver"
    _builtin_exception_classes: dict[str, BuiltinClassObject]
    _residual_effects: dict[Hashable, dict[str, object]]
    _consumer_worklist: "ConsumerFairWorklist | None"

    def __init__(self, debug_monitor: object | None = None) -> None:
        """Initialize empty analysis state.

        Args:
            debug_monitor: Optional DebugMonitor instance for tracking
        """
        self.allocation_policy: AllocationPolicyRegistry | None = None
        self.source_metadata: dict[str, dict[str, str]] = {}
        self.input_bindings: dict[str, object] = {}
        self._input_seeded: set[Scope] = set()
        self.synthetic_method_roots: list[JsonObject] = []
        self.unknown_effects: UnknownEffects | None = None
        self._env: dict[NativePointsKey, PointsToSet] = {}
        self._heap = HeapModel()
        self._call_graph: PointerCallGraph = PointerCallGraph()
        self._constraints: ConstraintManager = ConstraintManager()
        self._call_edges: list[CallEdge[Ctx[CallSite], Scope]] = []
        self._pointer_flow_graph: PointerFlowGraph = PointerFlowGraph()
        self._field_accesses: dict[tuple[AbstractObject, Field], FieldAccess] = {}
        # Field slots only grow through set_field. Preserve each object's
        # creation order so snapshot models need not rescan every heap slot.
        self._known_fields_by_object: dict[AbstractObject, dict[Field, None]] = {}
        self._variable_factory: VariableFactory = VariableFactory()
        self._worklist: Worklist | ConsumerFairWorklist = Worklist()
        self._consumer_worklist = None
        # append/pop retains the historical LIFO policy; fair arbitration uses
        # popleft without copying or shifting all pending constraints.
        self._static_constraints: deque[tuple[Scope, NativeContext, Constraint]] = deque()
        self._internal_scope: dict[AbstractObject, Scope] = {}
        self.obj_scope: dict[AbstractObject, Scope] = {}
        self._module_unknown_fields: defaultdict[IRScope, set[Ctx[FieldAccess]]] = defaultdict(set)

        # Debug monitoring
        self._debug_monitor = debug_monitor

        # Note: scope_manager is set lazily when needed
        self._scope_manager: ScopeManager | None = None
        self.entry_module: IRModule | None = None

    def peek_residual_effects(self) -> dict[Hashable, dict[str, object]] | None:
        """Observe optional solver-owned diagnostics without activating storage."""
        try:
            return self._residual_effects
        except AttributeError:
            return None

    @overload
    def select_allocation_context(
        self,
        context: NativeContext,
        site: AllocSite,
        alloc_type: AbstractObject | None = None,
        *,
        identity: str = "fresh",
    ) -> NativeContext: ...

    @overload
    def select_allocation_context(
        self,
        context: None,
        site: AllocSite,
        alloc_type: AbstractObject | None = None,
        *,
        identity: str = "fresh",
    ) -> NativeContext | None: ...

    def select_allocation_context(
        self,
        context: NativeContext | None,
        site: AllocSite,
        alloc_type: AbstractObject | None = None,
        *,
        identity: str = "fresh",
    ) -> NativeContext | None:
        """Run-local allocation boundary, injected by the native solver."""
        if self.allocation_policy is None:
            return context
        return self.allocation_policy.select(context, site, alloc_type, identity=identity)

    def apply_unknown_effects(self, scope: Scope, top_object: AbstractObject, origin: str) -> None:
        if self.unknown_effects is None:
            from .unknown_effects import UnknownEffects

            self.unknown_effects = UnknownEffects(self)
        self.unknown_effects.install(scope, top_object, origin)

    def set_internal_scope(self, obj: AbstractObject, scope: Scope) -> None:
        self._internal_scope[obj] = scope

    def is_module_export(self, module: IRScope, name: str) -> bool:
        from pythonstan.world.module_exports import source_has_export

        if source_has_export(module, name):
            return True
        exports = module.peek_source_extra_exports()
        return name in (() if exports is None else exports)

    def register_module_export(self, module_scope: Scope, name: object) -> None:
        """Record a real modeled module write/import effect, not builtin fallback."""
        if not isinstance(name, str) or not name.isidentifier():
            return
        module = module_scope.module.stmt
        extra = module.ensure_source_extra_exports()
        extra.add(name)
        variable = self._get_variable_direct(
            module_scope.module, module_scope.module.context, name, VariableKind.GLOBAL
        )
        if variable is not None:
            for wildcard in self._module_unknown_fields.get(module, ()):
                self._add_var_points_flow(variable, wildcard)

    def get_internal_scope(self, obj: AbstractObject) -> Scope | None:
        return self._internal_scope.get(obj, None)

    def get_scopes_for_ir(self, ir_scope: IRScope) -> list[Scope]:
        scopes: list[Scope] = []
        for scope in self._internal_scope.values():
            if scope.stmt == ir_scope:
                scopes.append(scope)
        for scope in self.call_graph.get_nodes():
            if scope.stmt == ir_scope and scope not in scopes:
                scopes.append(scope)
        return scopes

    def get_scope_for_ir(
        self,
        ir_scope: IRScope,
        context: "NativeContext | None" = None,
    ) -> Scope | None:
        scopes = self.get_scopes_for_ir(ir_scope)
        if context is None:
            return scopes[0] if scopes else None
        for scope in scopes:
            if scope.context == context:
                return scope
        return None

    def get_all_ctx_vars_for(self, scope: Scope, var: "Variable") -> list["Ctx[Variable]"]:
        scopes = self.get_scopes_for_ir(scope.stmt)
        ctx_vars: list[Ctx[Variable]] = []
        for scoped in scopes:
            ctx_vars.append(self.get_variable(scoped, scoped.context, var))
        return ctx_vars

    def get_points_to(self, var: NativePointsKey | None) -> PointsToSet:
        """Get points-to set for variable.

        Args:
            var: Variable to query

        Returns:
            Points-to set for variable (empty if not found)
        """
        if var is None:
            return PointsToSet.empty()
        return self._env.get(var, PointsToSet.empty())

    def set_points_to(self, var: NativePointsKey, pts: PointsToSet) -> bool:
        """Set points-to set for variable.

        Performs union with existing points-to set.

        Args:
            var: Variable to update
            pts: Points-to set to add

        Returns:
            True if points-to set changed
        """
        old_pts = self._env.get(var, PointsToSet.empty())
        new_pts = old_pts.union(pts)

        if new_pts != old_pts:
            self._env[var] = new_pts

            # Debug monitoring
            if self._debug_monitor and feature(self._debug_monitor, "enabled"):
                diff = new_pts - old_pts
                added_objs = [str(obj) for obj in diff]
                record_points_to_update(
                    self._debug_monitor,
                    variable_str=str(var),
                    old_size=len(old_pts),
                    new_size=len(new_pts),
                    added_objects=added_objs,
                )

            return True
        return False

    def known_fields_for_object(self, obj: "AbstractObject") -> tuple["Field", ...]:
        """Snapshot existing slots, including empty read-created slots.

        This indexes slot creation, not key presence or nonempty values. The
        tuple remains safe when a consumer recursively creates more fields.
        Its order equals filtering the global slot dictionary for ``obj``.
        """
        return tuple(self._known_fields_by_object.get(obj, ()))

    def has_field(
        self,
        scope: "Scope | None",
        context: "NativeContext | None",
        obj: "AbstractObject",
        field: "Field",
    ) -> Optional["FieldAccess"]:
        """Check if the field access exists in the object.

        Args:
            obj: Object to query
            field: Field to query

        Returns:
            Points-to set for field (empty if not found)
        """
        # TODO here is just a trivial mock and did not consider the complex features such as inheritance and MRO.

        return self._field_accesses.get((obj, field), None)

    def get_field(
        self,
        scope: "Scope | None",
        context: "NativeContext | None",
        obj: "AbstractObject",
        field: "Field",
    ) -> "Ctx[FieldAccess]":
        """Get field access for object field.

        Handles container-specific field resolution:
        - TupleObject: Supports position(i) fields for precise tracking
        - DictObject: Supports key(k) fields for precise tracking
        - ListObject/SetObject: Uses elem() field for all elements

        Args:
            obj: Object to query
            field: Field to query

        Returns:
            Contextualized field access for the specified field
        """

        # Summary objects: connect summary field to all concrete counterparts
        if is_summary_object(obj):
            field_access = self._field_accesses.get((obj, field), None)
            if field_access is None:
                field_access = self._variable_factory.make_field_access(obj, field)
                self.set_field(scope, context, obj, field, field_access)
            summary_cfield: Ctx[FieldAccess] = Ctx(obj.context, None, field_access)
            # Bidirectionally connect summary field with each concrete matching object
            for concrete in self._heap.objects.values():
                if summarize_object(concrete) == obj:
                    concrete_field = self.get_field(scope, context, concrete, field)
                    self._add_points_flow_edge(
                        PointerFlowEdge(
                            NormalNode(summary_cfield),
                            NormalNode(concrete_field),
                            PointerFlowKind.NORMAL,
                        )
                    )
                    self._add_points_flow_edge(
                        PointerFlowEdge(
                            NormalNode(concrete_field),
                            NormalNode(summary_cfield),
                            PointerFlowKind.NORMAL,
                        )
                    )
            return summary_cfield

        field_access = self._field_accesses.get((obj, field), None)
        exists = True
        if field_access is None:
            field_access = self._variable_factory.make_field_access(obj, field)
            self.set_field(scope, context, obj, field, field_access)
            exists = False

        cfield: Ctx[FieldAccess] = Ctx(obj.context, None, field_access)

        if not exists:
            if isinstance(obj, ModuleObject):
                internal_scope = self.get_internal_scope(obj)
                if internal_scope is None:
                    return cfield
                if field.name is None:
                    # Unknown attribute selection (e.g. getattr(module, name)
                    # during plugin discovery) may read any lexical export.
                    # Keep live subscriptions, not a snapshot of current pts.
                    self._module_unknown_fields[internal_scope.stmt].add(cfield)
                    for cvar in tuple(self._heap.heap.get((internal_scope.stmt,), {}).values()):
                        variable = cvar.content
                        if variable.kind == VariableKind.GLOBAL and self.is_module_export(
                            internal_scope.stmt, variable.name
                        ):
                            self._add_var_points_flow(cvar, cfield)
                    residuals = self.peek_residual_effects()
                    if residuals is not None:
                        residuals[(obj, "unknown_module_attribute")] = {
                            "origin": internal_scope.stmt.get_qualname(),
                            "context": str(context),
                            "reason": "Known exports flow to unknown module reads; dynamic __getattr__, presence and loaders remain open",
                            "effects": [
                                "module_dynamic_exports",
                                "may_invoke_callbacks",
                                "may_raise",
                            ],
                        }
                    return cfield
                var = self._variable_factory.make_variable(field.name, VariableKind.GLOBAL)
                if not self.is_module_export(internal_scope.stmt, field.name):
                    return cfield
                cvar = self.get_variable(internal_scope, internal_scope.context, var)
                self._add_var_points_flow(cvar, cfield)
                for wildcard in self._module_unknown_fields.get(internal_scope.stmt, ()):
                    self._add_var_points_flow(cvar, wildcard)

            elif isinstance(obj, InstanceObject) and field.kind in {
                FieldKind.ATTRIBUTE,
                FieldKind.UNKNOWN,
            }:
                # print(f"get field {field.name} for instance {obj}")
                own = self.get_write_field(scope, context, obj, field)
                self._add_var_points_flow(own, cfield)
                if field.kind == FieldKind.ATTRIBUTE:
                    unknown_own = self.get_field(scope, context, obj, Field(FieldKind.OWN_UNKNOWN))
                    self._add_var_points_flow(unknown_own, cfield)
                cls_obj = obj.class_obj
                assert isinstance(cls_obj, ClassObject), (
                    f"cls_obj must be a ClassObject, but got {type(cls_obj)}"
                )
                cls_scope = self.get_internal_scope(cls_obj)
                assert cls_scope is not None, "Class field lookup requires its registered scope"
                cls_field = self.get_field(cls_scope, cls_scope.context, cls_obj, field)
                assert cls_field != cfield, (
                    f"cls_field {cls_field} and cfield {cfield} should not be the same"
                )
                if cls_field != cfield:
                    edge = PointerFlowEdge(
                        NormalNode(cls_field), NormalNode(cfield), PointerFlowKind.INSTANCE
                    )
                    self._add_points_flow_edge(edge)

            elif isinstance(obj, ClassObject):
                # Dynamic namespaces may contain names outside the finite key
                # domain. Their wildcard values are candidates for every
                # concrete class attribute, including later instance reads.
                if field.kind == FieldKind.ATTRIBUTE:
                    wildcard = self.get_field(scope, context, obj, unknown())
                    self._add_var_points_flow(wildcard, cfield)
                class_source = obj.alloc_site.require_class_source()
                bases = class_source.get_bases()
                scope = obj.container_scope

                if len(bases) > 0:
                    # TODO here can use the class hierarchy manager to get the base class and the index of the base class.
                    selector = InheritanceSelectorNode()
                    inherit_edge = PointerFlowEdge(
                        selector, NormalNode(cfield), PointerFlowKind.INHERIT
                    )
                    self._add_points_flow_edge(inherit_edge)

                    for idx, base in enumerate(bases):
                        resolved = obj.ir.peek_base_variables()
                        base_var = (
                            resolved[idx]
                            if idx < len(resolved)
                            else self._variable_factory.make_variable(base.id, VariableKind.GLOBAL)
                        )
                        # Contextualize the base variable for constraint indexing
                        base_ctx_var = self.get_variable(scope, scope.context, base_var)
                        inherit_constraint = InheritanceConstraint(
                            base=base_var, field=field, target=selector, index=idx
                        )
                        added = self._constraints.add(scope, base_ctx_var, inherit_constraint)
                        consumer_worklist = self._consumer_worklist
                        if added and consumer_worklist is not None:
                            consumer_worklist.promote(NormalNode(base_ctx_var))
                        # Debug logging
                        import logging

                        logger = logging.getLogger(__name__)

                        # Check if debug_inheritance is enabled (access through solver if possible)
                        # For now, log at debug level
                        logger.debug(
                            f"[INHERIT] Created InheritanceConstraint for {class_source.name}.{field} from base {base.id}"
                        )

                        # CRITICAL FIX: If the base variable already has objects in its points-to set,
                        # we need to immediately resolve the field from those objects.
                        # Otherwise, the InheritanceConstraint won't fire because it only triggers on NEW objects.
                        if base_ctx_var:
                            base_pts = self.get_points_to(base_ctx_var)
                            if len(base_pts) > 0:
                                # Manually apply inheritance for existing base class objects
                                for base_obj in base_pts:
                                    if isinstance(base_obj, ClassObject):
                                        # Get field from base class using its internal scope
                                        base_internal_scope = self.get_internal_scope(base_obj)
                                        if base_internal_scope:
                                            base_field_access = self.get_field(
                                                base_internal_scope,
                                                base_obj.context,
                                                base_obj,
                                                field,
                                            )
                                            # Add PFG edge from base field to selector with proper index
                                            base_edge = PointerFlowEdge(
                                                NormalNode(base_field_access),
                                                selector,
                                                PointerFlowKind.NORMAL,
                                            )
                                            selector.add_edge(
                                                base_edge, idx
                                            )  # Register edge with selector
                                            self._add_points_flow_edge(base_edge)
                                            logger.debug(
                                                f"[INHERIT] Immediately resolving field {field} from existing base {base.id}"
                                            )

            if isinstance(obj, SuperObject):
                from .super_lookup import install_super_lookup

                install_super_lookup(self, scope, context, obj, field, cfield)

        return cfield

    def get_write_field(
        self,
        scope: "Scope | None",
        context: "NativeContext | None",
        obj: "AbstractObject",
        field: "Field",
    ) -> "Ctx[FieldAccess]":
        """Own storage for instance writes, logical storage for other objects."""
        if isinstance(obj, InstanceObject):
            if field.kind == FieldKind.ATTRIBUTE:
                field = Field(FieldKind.OWN_ATTRIBUTE, field.name)
            elif field.kind == FieldKind.UNKNOWN:
                field = Field(FieldKind.OWN_UNKNOWN)
        target = self.get_field(scope, context, obj, field)
        if isinstance(obj, InstanceObject) and field.kind == FieldKind.OWN_ATTRIBUTE:
            # If a write precedes the read, make the corresponding logical slot
            # now so ordinary attribute consumers get the new own facts.
            self.get_field(scope, context, obj, Field(FieldKind.ATTRIBUTE, field.name))
        return target

    def _get_builtin_methods_for_type(self, builtin_type: str) -> set[str]:
        """Get the set of known methods for a builtin type.

        Args:
            builtin_type: Type name (e.g., "list", "dict", "set")

        Returns:
            Set of method names
        """
        methods = {
            "list": {
                "append",
                "extend",
                "insert",
                "remove",
                "pop",
                "clear",
                "index",
                "count",
                "sort",
                "reverse",
                "copy",
                "__getitem__",
                "__setitem__",
                "__iter__",
                "__len__",
                "__contains__",
                "__bool__",
            },
            "dict": {
                "get",
                "pop",
                "popitem",
                "clear",
                "update",
                "setdefault",
                "keys",
                "values",
                "items",
                "copy",
                "__getitem__",
                "__setitem__",
                "__iter__",
                "__len__",
                "__contains__",
                "__bool__",
            },
            "set": {
                "add",
                "remove",
                "discard",
                "pop",
                "clear",
                "copy",
                "union",
                "intersection",
                "difference",
                "symmetric_difference",
                "update",
                "intersection_update",
                "difference_update",
                "__iter__",
                "__len__",
                "__contains__",
                "__bool__",
            },
            "frozenset": {"__iter__", "__len__", "__contains__", "__bool__"},
            "tuple": {
                "count",
                "index",
                "__getitem__",
                "__iter__",
                "__len__",
                "__contains__",
                "__bool__",
            },
            "str": {
                "upper",
                "lower",
                "strip",
                "split",
                "join",
                "replace",
                "startswith",
                "endswith",
                "find",
                "index",
                "format",
                "__getitem__",
                "__iter__",
                "__len__",
                "__contains__",
                "__bool__",
            },
            "bytes": {"__getitem__", "__iter__", "__len__", "__contains__", "__bool__"},
            "bytearray": {"__getitem__", "__iter__", "__len__", "__contains__", "__bool__"},
            "memoryview": {"__getitem__", "__iter__", "__len__", "__contains__", "__bool__"},
            "range": {"__getitem__", "__iter__", "__len__", "__contains__"},
            "dict_keys": {"__iter__", "__len__", "__contains__"},
            "dict_values": {"__iter__", "__len__", "__contains__"},
            "dict_items": {"__iter__", "__len__", "__contains__"},
            "iterator": {"__iter__", "__next__", "__len__"},
            "coroutine_iterator": {"__iter__", "__next__", "send", "throw", "close"},
            "async_generator": {"__aiter__", "__anext__", "asend", "athrow", "aclose"},
            "async_generator_asend": {
                "__await__",
                "__iter__",
                "__next__",
                "send",
                "throw",
                "close",
            },
            "enumerate": {"__iter__", "__next__"},
            "zip": {"__iter__", "__next__"},
            "map": {"__iter__", "__next__"},
            "filter": {"__iter__", "__next__"},
            "reversed": {"__iter__", "__next__"},
            "generator": {"__iter__", "__next__", "send", "throw", "close"},
            "coroutine": {"__await__", "send", "throw", "close"},
            "property": {"__get__", "__set__", "__delete__"},
            "classmethod": {"__get__"},
            "staticmethod": {"__get__"},
            "file": {"__enter__", "__exit__"},
        }
        return methods.get(builtin_type, set())

    def peek_instance_namespace_views(self) -> "FieldCreatedObserver | None":
        """Read an optional namespace subscriber without allocating one."""
        try:
            return self._instance_namespace_views
        except AttributeError:
            return None

    def peek_mapping_field_copies(self) -> "FieldCreatedObserver | None":
        """Read an optional copy subscriber without allocating one."""
        try:
            return self._mapping_field_copies
        except AttributeError:
            return None

    def set_field(
        self,
        scope: "Scope | None",
        context: "NativeContext | None",
        obj: "AbstractObject",
        field: "Field",
        field_access: "FieldAccess",
    ) -> None:
        """Set field access for object field.

        Performs union with existing points-to set.

        Args:
            obj: Object to update
            field: Field to update

        Returns:
            Field access for object field
        """
        assert isinstance(field_access, FieldAccess), (
            f"field_access must be a FieldAccess, but got {type(field_access)}"
        )
        self._field_accesses[(obj, field)] = field_access
        if self.unknown_effects is not None:
            self.unknown_effects.field_created(obj, field_access)
        # Publish the slot before callbacks can recursively enumerate it.
        self._known_fields_by_object.setdefault(obj, {})[field] = None
        manager = self.peek_instance_namespace_views()
        if manager is not None:
            manager.field_created(scope, obj, field)
        copies = self.peek_mapping_field_copies()
        if copies is not None:
            copies.field_created(scope, obj, field)

    @property
    def scope_manager(self) -> "ScopeManager":
        if self._scope_manager is None:
            from pythonstan.world import World

            self._scope_manager = World().scope_manager
        return self._scope_manager

    @property
    def constraints(self) -> "ConstraintManager":
        """Get constraint manager.

        Returns:
            Constraint manager

        Raises:
            RuntimeError: If constraints not initialized
        """
        if self._constraints is None:
            raise RuntimeError("Constraints not initialized")
        return self._constraints

    @property
    def pointer_flow_graph(self) -> "PointerFlowGraph":
        """Get pointer flow graph.

        Returns:
            Pointer flow graph
        """
        return self._pointer_flow_graph

    @property
    def call_graph(self) -> "PointerCallGraph":
        """Get call graph.

        Returns:
            Call graph

        Raises:
            RuntimeError: If call graph not initialized
        """
        if self._call_graph is None:
            raise RuntimeError("Call graph not initialized")
        return self._call_graph

    def get_variable(
        self, scope: "Scope", context: "NativeContext | None", var: "Variable"
    ) -> "Ctx[Variable]":
        return self._resolve_variable(scope, context, var, create=True)

    def find_variable(
        self, scope: Scope, context: NativeContext | None, var: Variable
    ) -> Ctx[Variable] | None:
        """Read an existing binding without creating slots or propagation work."""
        return self._resolve_variable(scope, context, var, create=False)

    @overload
    def _resolve_variable(
        self, scope: Scope, context: NativeContext | None, var: Variable, *, create: Literal[True]
    ) -> Ctx[Variable]: ...

    @overload
    def _resolve_variable(
        self, scope: Scope, context: NativeContext | None, var: Variable, *, create: Literal[False]
    ) -> Ctx[Variable] | None: ...

    def _resolve_variable(
        self, scope: Scope, context: NativeContext | None, var: Variable, *, create: bool
    ) -> Ctx[Variable] | None:
        owner_scope = scope
        owner_context = context
        var_kind = var.kind

        if var_kind == VariableKind.GLOBAL:
            owner_scope = scope.module
            owner_context = scope.module.context
        elif var_kind == VariableKind.NONLOCAL:
            from .object import ClassObject, FunctionObject

            capture_owner = _lexical_capture_owner(scope.obj)
            if isinstance(capture_owner, (FunctionObject, ClassObject)):
                captured = self._heap.get_nonlocal_vars(capture_owner).get(var.name)
                if captured is not None:
                    return captured
            owner_scope = scope.parent
            owner_context = scope.parent.context
        elif var_kind == VariableKind.CELL:
            from .object import ClassObject, FunctionObject

            # For closure-captured variables, first try to resolve from the
            # function object's captured cell vars before falling back.
            func_obj = _lexical_capture_owner(scope.obj)
            if isinstance(func_obj, (FunctionObject, ClassObject)):
                captured = self._heap.get_cell_vars(func_obj).get(var.name)
                if captured is not None:
                    return captured
            owner_scope = scope.parent or scope
            owner_context = owner_scope.context if owner_scope else context
        else:
            owner_scope = scope
            owner_context = context

        if var.name.startswith("$const") and scope.module and scope != scope.module:
            module_cvar = self._get_variable_direct(
                scope.module, scope.module.context, var.name, var_kind
            )
            if module_cvar is not None:
                return module_cvar

        cvar = self._get_variable_direct(owner_scope, owner_context, var.name, var_kind)
        if cvar is None and create:
            cvar = Ctx(
                owner_context, owner_scope, self._variable_factory.make_variable(var.name, var_kind)
            )
            self.set_variable(owner_scope, owner_context, var, cvar)
        return cvar

    def set_variable(
        self,
        scope: "Scope",
        context: "NativeContext | None",
        var: "Variable",
        ctx_var: "Ctx[Variable]",
    ) -> None:
        self._heap.set_variable(scope, context, var, ctx_var)
        if self.unknown_effects is not None:
            self.unknown_effects.variable_created(ctx_var)
        if (
            var.kind == VariableKind.GLOBAL
            and scope is not None
            and self.is_module_export(scope.module.stmt, var.name)
        ):
            for wildcard in self._module_unknown_fields.get(scope.module.stmt, ()):
                self._add_var_points_flow(ctx_var, wildcard)

    def get_cell_var(self, obj: FunctionObject | ClassObject, name: str) -> Ctx["Variable"] | None:
        return self._heap.get_cell_vars(obj).get(name, None)

    def get_nonlocal_var(
        self, obj: FunctionObject | ClassObject, name: str
    ) -> Ctx["Variable"] | None:
        return self._heap.get_nonlocal_vars(obj).get(name, None)

    def get_global_var(
        self, obj: FunctionObject | ClassObject, name: str
    ) -> Ctx["Variable"] | None:
        return self._heap.get_global_vars(obj).get(name, None)

    def get_cell_vars(self, obj: FunctionObject | ClassObject) -> dict[str, Ctx["Variable"]]:
        return self._heap.get_cell_vars(obj)

    def get_nonlocal_vars(self, obj: FunctionObject | ClassObject) -> dict[str, Ctx["Variable"]]:
        return self._heap.get_nonlocal_vars(obj)

    def get_global_vars(self, obj: FunctionObject | ClassObject) -> dict[str, Ctx["Variable"]]:
        return self._heap.get_global_vars(obj)

    def set_cell_vars(
        self, obj: FunctionObject | ClassObject, vars: dict[str, Ctx[Variable]]
    ) -> None:
        self._heap.cell_vars[obj] = vars
        if self.unknown_effects is not None:
            self.unknown_effects.captures_created(vars)

    def set_nonlocal_vars(
        self, obj: FunctionObject | ClassObject, vars: dict[str, Ctx[Variable]]
    ) -> None:
        self._heap.nonlocal_vars[obj] = vars
        if self.unknown_effects is not None:
            self.unknown_effects.captures_created(vars)

    def set_global_vars(
        self, obj: FunctionObject | ClassObject, vars: dict[str, Ctx[Variable]]
    ) -> None:
        self._heap.global_vars[obj] = vars
        if self.unknown_effects is not None:
            self.unknown_effects.captures_created(vars)

    def _get_variable_direct(
        self, scope: "Scope", context: "NativeContext | None", var_name: str, var_kind: VariableKind
    ) -> Optional["Ctx[Variable]"]:
        assert isinstance(var_name, str), f"var_name must be a string, but got {type(var_name)}"

        var = self._variable_factory.make_variable(var_name, var_kind)
        return self._heap.get_variable(scope, context, var)

    def _add_var_points_flow(
        self, src: Ctx[Variable | FieldAccess], tgt: Ctx[Variable | FieldAccess]
    ) -> None:
        assert isinstance(src, Ctx), f"src must be a Ctx, but got {type(src)}"
        assert isinstance(tgt, Ctx), f"tgt must be a Ctx, but got {type(tgt)}"
        if src != tgt:
            self._add_points_flow_edge(
                PointerFlowEdge(NormalNode(src), NormalNode(tgt), PointerFlowKind.NORMAL)
            )

    def _add_points_flow_edge(self, edge: PointerFlowEdge) -> None:
        if self.pointer_flow_graph.add_edge(edge):
            consumer_worklist = self._consumer_worklist
            if consumer_worklist is not None:
                consumer_worklist.promote(edge.source)
            src = edge.source
            tgt = edge.target
            pts = self.pointer_flow_graph.flow_through_edge(
                edge, self.get_points_to(src)
            ) - self.get_points_to(tgt)
            if not pts.is_empty():
                scope = None
                if isinstance(tgt, NormalNode):
                    scope = tgt.var.scope
                self._worklist.add((scope, tgt, pts))

    def get_statistics(self) -> dict[str, int]:
        """Get state statistics.

        Returns:
            Dictionary with statistics:
            - num_variables: Number of variables tracked
            - num_objects: Number of unique objects
            - num_heap_locations: Number of heap locations
        """
        objects = set(self._heap.objects.values())
        variable_locations = set()
        field_locations = set()
        for location, points in self._env.items():
            objects.update(points)
            content = (
                location.var.content
                if isinstance(location, NormalNode)
                else (location.content if isinstance(location, Ctx) else location)
            )
            if isinstance(content, Variable):
                variable_locations.add(location)
            elif isinstance(content, FieldAccess):
                field_locations.add(location)
                objects.add(content.obj)
        # Count populated locations; merely asking for a field must not inflate
        # analysis output statistics. Allocations with no references still count.
        return {
            "num_variables": len(variable_locations),
            "num_objects": len(objects),
            "num_heap_locations": len(field_locations),
            "num_call_edges": self._call_graph.num_plain_edges(),
        }

    def get_detailed_statistics(self) -> JsonObject:
        """Get detailed state statistics for debugging.

        Returns:
            Dictionary with detailed statistics including:
            - Points-to set size distribution
            - Variables with empty/singleton/large sets
            - Object type breakdown
            - Call graph metrics
        """
        from collections import defaultdict

        # Collect points-to set sizes
        pts_sizes = []
        empty_vars: list[JsonValue] = []
        singleton_vars: list[JsonValue] = []
        large_vars: list[JsonValue] = []  # > 10 objects

        for var, pts in self._env.items():
            size = len(pts)
            pts_sizes.append(size)

            if size == 0:
                empty_vars.append(str(var))
            elif size == 1:
                singleton_vars.append(str(var))
            elif size > 10:
                large_vars.append(str(var))

        # Size distribution
        size_dist: defaultdict[str, int] = defaultdict(int)
        for size in pts_sizes:
            if size == 0:
                bucket = "0"
            elif size == 1:
                bucket = "1"
            elif size <= 5:
                bucket = "2-5"
            elif size <= 10:
                bucket = "6-10"
            elif size <= 50:
                bucket = "11-50"
            else:
                bucket = "51+"
            size_dist[bucket] += 1

        # Object type breakdown
        obj_by_kind: defaultdict[str, int] = defaultdict(int)
        all_objects: set[AbstractObject] = set()
        for pts in self._env.values():
            for obj in pts:
                all_objects.add(obj)
                obj_by_kind[obj.kind.value] += 1

        return {
            "num_variables": len(self._env),
            "num_heap_locations": len(self._heap.objects),
            "num_call_edges": self._call_graph.get_number_of_edges(),
            "num_pfg_nodes": len(self._pointer_flow_graph.get_nodes()),
            "num_pfg_edges": len(self._pointer_flow_graph.get_edges()),
            "points_to": {
                "avg_size": sum(pts_sizes) / len(pts_sizes) if pts_sizes else 0.0,
                "max_size": max(pts_sizes) if pts_sizes else 0,
                "min_size": min(pts_sizes) if pts_sizes else 0,
                "empty_count": len(empty_vars),
                "singleton_count": len(singleton_vars),
                "large_count": len(large_vars),
                "size_distribution": dict(size_dist),
                "empty_variables": empty_vars[:20],  # Limit output
                "singleton_variables": singleton_vars[:20],
                "large_variables": large_vars[:20],
            },
            "objects": {"total": len(all_objects), "by_kind": dict(obj_by_kind)},
        }
