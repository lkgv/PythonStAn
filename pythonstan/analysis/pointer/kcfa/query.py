"""Native query implementation; imported by the legacy solver facade."""
import warnings
from typing import Any,Dict,List,Set,Tuple,Optional,Union,Iterable
from pythonstan.ir.ir_statements import (IRScope,IRModule,IRFunc,IRClass,IRCall,
    IRLoadAttr,IRStoreAttr,IRLoadSubscr,IRAssign,IRCopy)
from .solver_interface import ISolverQuery
from .state import PointerAnalysisState
from .context import Ctx,Scope,CallSite,AbstractContext
from .object import *
from .variable import Variable,VariableKind
from .heap_model import Field,FieldKind,attr,key,elem,value
from .points_to_set import PointsToSet
from .bootstrap import bootstrap_statistics

class SolverQuery(ISolverQuery):
    def __init__(
        self,
        state: 'PointerAnalysisState',
        stats: Dict[str, int],
        unknown_tracker: 'UnknownTracker',
        class_hierarchy: Optional['ClassHierarchyManager'] = None,
        solver: Optional['PointerSolver'] = None,
    ):
        self._state = state
        self._stats = stats
        self._unknown_tracker = unknown_tracker
        self._class_hierarchy = class_hierarchy
        self._solver = solver
        self._entry_module = state.entry_module

    def _scope_label(self, scope: IRScope) -> str:
        if isinstance(scope, IRModule):
            if scope is self._entry_module:
                return "__main__"
        return scope.get_qualname()

    def _resolve_var_kind(self, var_name: str, ir_scope: IRScope) -> VariableKind:
        # Match the translator's synthetic-name and module-binding rules.  A
        # module's symbol table need not list its ordinary bindings as explicit
        # ``global`` declarations, but translation still stores them as GLOBAL.
        if var_name.startswith("$") and not var_name.startswith("$const"):
            return VariableKind.TEMPORARY
        if isinstance(ir_scope, IRModule) or var_name.startswith("$const"):
            return VariableKind.GLOBAL
        if var_name in ir_scope.get_global_vars():
            return VariableKind.GLOBAL
        if var_name in ir_scope.get_nonlocal_vars():
            return VariableKind.NONLOCAL
        if var_name in ir_scope.get_cell_vars():
            return VariableKind.CELL
        return VariableKind.LOCAL

    def _resolve_scopes(
        self,
        ir_scope: IRScope,
        context: Optional['AbstractContext'],
    ) -> List['Scope']:
        scopes = self._state.get_scopes_for_ir(ir_scope)
        if context is None:
            return scopes
        return [scope for scope in scopes if scope.context == context]

    def _require_scope(self, ir_scope: IRScope, context: 'AbstractContext') -> 'Scope':
        scopes = self._resolve_scopes(ir_scope, context)
        if not scopes:
            raise ValueError(f"No scope found for {ir_scope.get_qualname()} in context {context}")
        return scopes[0]

    def _resolve_var_to_ctx(
        self,
        var_name: str,
        ir_scope: IRScope,
        context: Optional['AbstractContext'],
    ) -> Union['Ctx[Variable]', List['Ctx[Variable]']]:
        var_kind = self._resolve_var_kind(var_name, ir_scope)
        var = Variable(name=var_name, kind=var_kind)
        if context is None:
            scopes = self._state.get_scopes_for_ir(ir_scope)
            return [binding for scope in scopes
                    if (binding := self._state.find_variable(scope, scope.context, var)) is not None]
        scope = self._require_scope(ir_scope, context)
        return self._state.find_variable(scope, context, var)

    def _field_points_to(self, obj: 'AbstractObject', field: Field) -> 'PointsToSet':
        if obj.kind == AllocKind.UNKNOWN:
            return PointsToSet.singleton(obj)
        scope = self._state.get_internal_scope(obj) or self._state.obj_scope.get(obj)
        if scope is None:
            return PointsToSet.empty()
        # Observing a result must never register a new heap demand, PFG edge,
        # replay subscription or unknown-effect event. A missing slot means no
        # materialized fact; field_observation exposes that distinction from a
        # requested empty slot for consumers that need completeness metadata.
        access = self._state.has_field(scope, scope.context, obj, field)
        result = (self._state.get_points_to(Ctx(obj.context, None, access))
                  if access is not None else PointsToSet.empty())
        effects = self._state.unknown_effects
        if effects is not None and effects.source is not None:
            result = result.union(self._state.get_points_to(effects.source))
        return result

    def field_observation(self, obj: 'AbstractObject', field: Field) -> Dict[str, Any]:
        """Passive materialization status; an empty set is not an absence proof."""
        scope = self._state.get_internal_scope(obj) or self._state.obj_scope.get(obj)
        access = (self._state.has_field(scope, scope.context, obj, field)
                  if scope is not None else None)
        points = self._field_points_to(obj, field)
        unknown = any(value.kind == AllocKind.UNKNOWN for value in points)
        status = ("unknown" if unknown else "materialized" if access is not None
                  else "not_requested")
        return {"status": status, "points_to": points}

    def _callsite_ctx(
        self,
        call_stmt: IRCall,
        scope: IRScope,
        context: 'AbstractContext',
    ) -> 'Ctx[CallSite]':
        internal_scope = self._require_scope(scope, context)
        call_site = CallSite(statement=call_stmt, scope_name=self._scope_label(scope))
        return Ctx(context, internal_scope, call_site)

    def _edges_for_call(
        self,
        call_stmt: IRCall,
        scope: IRScope,
        context: Optional['AbstractContext'],
    ) -> Set['CallEdge']:
        expected_scope = self._scope_label(scope)
        edges = set()
        for edge in self._state.call_graph.get_edges():
            callsite_ctx = edge.get_callsite()
            if callsite_ctx.content.statement != call_stmt:
                continue
            if callsite_ctx.content.scope_name != expected_scope:
                continue
            if callsite_ctx.scope.stmt != scope:
                continue
            if context is None or callsite_ctx.context == context:
                edges.add(edge)
        return edges

    def _iter_ctx_vars_by_name(self, var: 'Variable') -> Iterable['Ctx[Variable]']:
        for ctx_var in self._state._env:
            if not isinstance(ctx_var, Ctx):
                continue
            content = ctx_var.content
            if isinstance(content, Variable) and content.name == var.name and content.kind == var.kind:
                yield ctx_var

    def _get_function_objects(
        self,
        func: IRFunc,
        context: Optional['AbstractContext'],
    ) -> List['AbstractObject']:
        objs = []
        for obj in self._state._heap.objects.values():
            if getattr(obj.alloc_site, "stmt", None) != func:
                continue
            if not isinstance(obj, (FunctionObject, MethodObject)):
                continue
            if context is None or obj.context == context:
                objs.append(obj)
        return objs

    def points_to_at(
        self,
        var_name: str,
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> 'PointsToSet':
        if context is None:
            result = PointsToSet.empty()
            for ctx_var in self._resolve_var_to_ctx(var_name, scope, None):
                result = result.union(self._state.get_points_to(ctx_var))
        else:
            ctx_var = self._resolve_var_to_ctx(var_name, scope, context)
            result = self._state.get_points_to(ctx_var)
        # An arbitrary boundary can introduce/rebind source globals even if an
        # ad-hoc query name never had a producer slot. Read the semantic havoc
        # default directly; exporting an answer must not create solver work.
        effects = self._state.unknown_effects
        if (effects is not None and effects.source is not None
                and self._resolve_var_kind(var_name, scope) == VariableKind.GLOBAL
                and not var_name.startswith(("$", "<"))):
            result = result.union(self._state.get_points_to(effects.source))
        return result

    def points_to_of_target(
        self,
        stmt: Union[IRAssign, IRCopy, IRLoadAttr, IRLoadSubscr, IRCall],
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> 'PointsToSet':
        if isinstance(stmt, IRCall):
            target = stmt.get_target()
            if target is None:
                return PointsToSet.empty()
            return self.points_to_at(target, scope, context)
        lval = stmt.get_lval()
        return self.points_to_at(lval.id, scope, context)

    def load_attr_result(
        self,
        stmt: IRLoadAttr,
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> 'PointsToSet':
        return self.points_to_of_target(stmt, scope, context)

    def store_attr_value(
        self,
        stmt: IRStoreAttr,
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> 'PointsToSet':
        return self.points_to_at(stmt.get_rval().id, scope, context)

    def field_of_objects(
        self,
        base_pts: 'PointsToSet',
        attr_name: str,
    ) -> 'PointsToSet':
        result = PointsToSet.empty()
        field = attr(attr_name)
        for obj in base_pts:
            result = result.union(self._field_points_to(obj, field))
        return result

    def subscript_of_objects(
        self,
        base_pts: 'PointsToSet',
        index: Optional[Union[int, str]] = None,
    ) -> 'PointsToSet':
        result = PointsToSet.empty()
        for obj in base_pts:
            if index is None:
                field = elem()
            else:
                field = key(index)
            result = result.union(self._field_points_to(obj, field))
        return result

    def call_graph(self) -> 'AbstractCallGraph':
        return self._state.call_graph

    def callees_of(
        self,
        call_stmt: IRCall,
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> Set['AbstractObject']:
        callees = set()
        for edge in self._edges_for_call(call_stmt, scope, context):
            callee_scope = edge.get_callee()
            if callee_scope is not None:
                callees.add(callee_scope.obj)
        return callees

    def callees_as_ir(
        self,
        call_stmt: IRCall,
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> Set[Union[IRFunc, IRClass]]:
        result: Set[Union[IRFunc, IRClass]] = set()
        for obj in self.callees_of(call_stmt, scope, context):
            if isinstance(obj, (FunctionObject, MethodObject)):
                result.add(obj.ir)
            elif isinstance(obj, ClassObject):
                result.add(obj.ir)
        return result

    def callers_of(
        self,
        callee: Union[IRFunc, IRClass],
    ) -> Set[Tuple[IRCall, IRScope]]:
        callers: Set[Tuple[IRCall, IRScope]] = set()
        for edge in self._state.call_graph.get_edges():
            callee_scope = edge.get_callee()
            if callee_scope.stmt == callee:
                callsite_ctx = edge.get_callsite()
                callers.add((callsite_ctx.content.statement, callsite_ctx.scope.stmt))
        return callers

    def receiver_objects(
        self,
        call_stmt: IRCall,
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> 'PointsToSet':
        ir = self._state.scope_manager.get_ir(scope, "ir")
        if not ir:
            return PointsToSet.empty()
        callee_name = call_stmt.get_func_name()
        receiver_name = None
        try:
            idx = ir.index(call_stmt)
            candidates = reversed(ir[:idx])
        except ValueError:
            candidates = reversed(ir)
        for stmt in candidates:
            if isinstance(stmt, IRLoadAttr) and stmt.get_lval().id == callee_name:
                receiver_name = stmt.get_obj().id
                break
        if receiver_name is None:
            return PointsToSet.empty()
        return self.points_to_at(receiver_name, scope, context)

    def may_alias(
        self,
        var1: str,
        var2: str,
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> bool:
        pts1 = self.points_to_at(var1, scope, context)
        pts2 = self.points_to_at(var2, scope, context)
        if any(obj.kind == AllocKind.UNKNOWN for obj in (*pts1, *pts2)):
            return True
        return not pts1.intersection(pts2).is_empty()

    def may_alias_field(
        self,
        var1: str,
        attr1: str,
        var2: str,
        attr2: Optional[str],
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> bool:
        pts1 = self.field_of_objects(self.points_to_at(var1, scope, context), attr1)
        if attr2 is None:
            pts2 = self.points_to_at(var2, scope, context)
        else:
            pts2 = self.field_of_objects(self.points_to_at(var2, scope, context), attr2)
        if any(obj.kind == AllocKind.UNKNOWN for obj in (*pts1, *pts2)):
            return True
        return not pts1.intersection(pts2).is_empty()

    def get_class_mro(
        self,
        cls_obj: 'AbstractObject',
    ) -> List['AbstractObject']:
        from .mro_projection import project_class_mro
        return project_class_mro(self._state, cls_obj, self._class_hierarchy, read_only=True)

    def resolve_method(
        self,
        receiver_pts: 'PointsToSet',
        method_name: str,
    ) -> 'PointsToSet':
        result = PointsToSet.empty()
        field = attr(method_name)
        for obj in receiver_pts:
            result = result.union(self._field_points_to(obj, field))
        return result

    def get_instance_class(
        self,
        instance_obj: 'AbstractObject',
    ) -> Optional['AbstractObject']:
        if isinstance(instance_obj, InstanceObject):
            return instance_obj.class_obj
        if isinstance(instance_obj, BuiltinInstanceObject):
            return ObjectFactory.create_builtin_class(instance_obj.builtin_type, instance_obj.context)
        return None

    def get_bound_receiver(
        self,
        method_obj: 'AbstractObject',
    ) -> Optional['AbstractObject']:
        if isinstance(method_obj, MethodObject):
            return method_obj.instance_obj
        if isinstance(method_obj, BuiltinMethodObject):
            return method_obj.receiver
        return None

    def get_captured_vars(
        self,
        func: IRFunc,
        context: Optional['AbstractContext'] = None,
    ) -> Dict[str, 'PointsToSet']:
        result: Dict[str, PointsToSet] = {}
        for func_obj in self._get_function_objects(func, context):
            cell_vars = self._state.get_cell_vars(func_obj)
            nonlocal_vars = self._state.get_nonlocal_vars(func_obj)
            for var_name, ctx_var in {**cell_vars, **nonlocal_vars}.items():
                pts = self._state.get_points_to(ctx_var)
                if var_name in result:
                    result[var_name] = result[var_name].union(pts)
                else:
                    result[var_name] = pts
        return result

    def may_have_attr(
        self,
        obj: 'AbstractObject',
        attr_name: str,
    ) -> bool:
        scope = self._state.get_internal_scope(obj) or self._state.obj_scope.get(obj)
        if scope is None:
            return True
        field_access = self._state.has_field(scope, scope.context, obj, attr(attr_name))
        if field_access is None:
            return True
        return not self._state.get_points_to(field_access).is_empty()

    def list_elements(
        self,
        list_obj: 'AbstractObject',
    ) -> 'PointsToSet':
        return self._field_points_to(list_obj, elem())

    def dict_keys(
        self,
        dict_obj: 'AbstractObject',
    ) -> 'PointsToSet':
        keys = []
        for (obj, field), _ in self._state._field_accesses.items():
            if obj != dict_obj:
                continue
            if field.kind != FieldKind.KEY or field.name is None:
                continue
            for heap_obj in self._state._heap.objects.values():
                if isinstance(heap_obj, ConstantObject) and str(heap_obj.value) == field.name:
                    keys.append(heap_obj)
        return PointsToSet.from_objects(keys)

    def dict_values(
        self,
        dict_obj: 'AbstractObject',
    ) -> 'PointsToSet':
        return self._field_points_to(dict_obj, value()).union(self._field_points_to(dict_obj, elem()))

    def dict_get(
        self,
        dict_obj: 'AbstractObject',
        key_name: str,
    ) -> 'PointsToSet':
        return self._field_points_to(dict_obj, key(key_name))

    def tuple_element(
        self,
        tuple_obj: 'AbstractObject',
        index: int,
    ) -> 'PointsToSet':
        return self._field_points_to(tuple_obj, key(index))

    def set_elements(
        self,
        set_obj: 'AbstractObject',
    ) -> 'PointsToSet':
        return self._field_points_to(set_obj, elem())

    def get_object_type(
        self,
        obj: 'AbstractObject',
    ) -> Optional['AbstractObject']:
        if isinstance(obj, InstanceObject):
            return obj.class_obj
        if isinstance(obj, BuiltinInstanceObject):
            return ObjectFactory.create_builtin_class(obj.builtin_type, obj.context)
        if isinstance(obj, (FunctionObject, MethodObject, BuiltinFunctionObject, BuiltinMethodObject)):
            return ObjectFactory.create_builtin_class("function", obj.context)
        if isinstance(obj, ClassObject):
            return ObjectFactory.create_builtin_class("type", obj.context)
        if isinstance(obj, BuiltinClassObject):
            return ObjectFactory.create_builtin_class("type", obj.context)
        return None

    def is_instance_of(
        self,
        obj: 'AbstractObject',
        cls: 'AbstractObject',
    ) -> bool:
        obj_type = self.get_object_type(obj)
        if obj_type is None:
            return False
        if obj_type == cls:
            return True
        if isinstance(obj_type, BuiltinClassObject) and isinstance(cls, BuiltinClassObject):
            return obj_type.builtin_name == cls.builtin_name
        if isinstance(obj_type, ClassObject) and isinstance(cls, ClassObject):
            mro = self.get_class_mro(obj_type)
            # This is a may-query. Unresolved order/base coverage cannot prove
            # that an ancestor is absent.
            return cls in mro or not mro.order_resolved
        return False

    def objects_of_type(
        self,
        cls: 'AbstractObject',
    ) -> 'PointsToSet':
        objs = [obj for obj in self.get_all_objects() if self.is_instance_of(obj, cls)]
        return PointsToSet.from_objects(objs)

    def get_reachable_scopes(self) -> Set[IRScope]:
        scopes = {scope.stmt for scope in self._state.call_graph.get_nodes()}
        scopes.update(scope.stmt for scope in self._state._internal_scope.values())
        return scopes

    def get_reachable_functions(self) -> Set[IRFunc]:
        return {scope for scope in self.get_reachable_scopes() if isinstance(scope, IRFunc)}

    def get_reachable_classes(self) -> Set[IRClass]:
        return {scope for scope in self.get_reachable_scopes() if isinstance(scope, IRClass)}

    def is_reachable(
        self,
        scope: IRScope,
    ) -> bool:
        return scope in self.get_reachable_scopes()

    def get_statistics(self) -> Dict[str, Any]:
        state_stats = self._state.get_statistics()
        unknown_stats = self._unknown_tracker.get_summary()
        current_stats = self._solver._stats if self._solver is not None else self._stats
        bootstrap_stats = bootstrap_statistics(self._solver) if self._solver is not None else {}
        allocations = (self._state.allocation_policy.statistics()
                       if self._state.allocation_policy is not None else {})
        ledger=(self._solver.work_ledger.snapshot() if self._solver is not None else {"used":0,"limit":None,"remaining":None,"by_kind":{},"denials":{}})
        nested={"global_work_used":ledger["used"],"global_work_limit":ledger["limit"],
                "global_work_remaining":ledger["remaining"],"work_by_kind":ledger["by_kind"],
                "work_budget_denials":ledger["denials"],
                "ai_intrinsic_work":ledger["by_kind"].get("abstract_transfer",0),
                "intrinsic_bridge_work":ledger["by_kind"].get("intrinsic_bridge",0),
                "ai_intrinsic_seconds":self._solver.intrinsic_seconds if self._solver is not None else 0.0,
                "ai_intrinsic_seconds_scope":"subset_of_inclusive_native_phase",
                "native_selected_work":ledger["by_kind"].get("native_selected",0),
                "input_publications":ledger["by_kind"].get("input_publication",0)}
        return {**state_stats, **current_stats, **unknown_stats, **bootstrap_stats, **nested,
                "allocation_policy_decisions": allocations,
                "heap_context_policy": (self._solver.config.heap_context_policy if self._solver is not None else None),
                "heap_context_depth": (self._solver.config.heap_context_depth if self._solver is not None else None),
                "synthetic_method_roots": [dict(row) for row in self._state.synthetic_method_roots],
                "residual_effect_count": len(getattr(self._state, "_residual_effects", {}))}

    def get_all_objects(self) -> Iterable['AbstractObject']:
        return self._state._heap.objects.values()

    def get_unknown_summary(self) -> Dict[str, int]:
        return self._unknown_tracker.get_summary()

    def get_unknown_details(self) -> List[Dict[str, Any]]:
        return self._unknown_tracker.get_detailed_report()

    def get_unknown_effect_events(self):
        """Read-only semantic may-events; arbitrary callback coverage stays open."""
        effects = self._state.unknown_effects
        return list(effects.snapshot()) if effects is not None else []

    def get_residual_effects(self) -> List[Dict[str, Any]]:
        """Undischarged obligations, separate from actual unknown-effect events."""
        return [dict(effect) for effect in getattr(self._state, "_residual_effects", {}).values()]

    def get_semantic_status(self) -> Dict[str, Any]:
        """Report proof boundaries without interpreting a quiet ledger as a proof.

        The solver reaches a fixed point of the constraints it received. Neither
        that fact nor the optional unknown diagnostics establishes that those
        constraints cover all source-language operations. Residual recording is
        mandatory where implemented, but its exhaustiveness is not established.
        """
        return {
            "constraint_fixpoint_reached": bool((self._solver._stats if self._solver is not None else self._stats).get("converged", False)),
            "residual_obligation_count": len(getattr(self._state, "_residual_effects", {})),
            "unknown_diagnostics_enabled": self._unknown_tracker.track_unknowns,
            "unknown_diagnostic_count": len(self._unknown_tracker.records),
            "residual_enumeration_exhaustive": False,
            "source_semantics_coverage": "unproved",
            "whole_program_soundness_proved": False,
            "certificate_eligible": False,
        }

    def points_to(self, var: 'Variable') -> 'PointsToSet':
        warnings.warn(
            "points_to(Variable) is deprecated. Use points_to_at(var_name, scope, context).",
            DeprecationWarning,
            stacklevel=2,
        )
        result = PointsToSet.empty()
        for ctx_var in self._iter_ctx_vars_by_name(var):
            result = result.union(self._state.get_points_to(ctx_var))
        return result

    def get_field(self, obj: 'AbstractObject', field: 'Field') -> 'PointsToSet':
        warnings.warn(
            "get_field(AbstractObject, Field) is deprecated. Use field_of_objects() or container-specific methods.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._field_points_to(obj, field)


