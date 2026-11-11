"""Monotone class/metaclass relations and a bounded dynamic class model.

This module restores value/call propagation. It deliberately leaves class
creation ordering, metaclass conflict resolution and custom hooks as residual
obligations rather than certifying Python's complete construction protocol.
"""
import ast

from .processor import Processor
from ..constraints import LoadConstraint, CallConstraint
from ..context import Scope
from ..heap_model import Field, FieldKind, attr, elem, unknown
from ..object import (
    AllocKind, AllocSite, BuiltinClassObject, BuiltinFunctionObject,
    BuiltinMethodObject, ClassObject, DictObject, FunctionObject, MethodObject,
)
from ..points_to_set import PointsToSet
from ..pointer_flow_graph import GuardNode, NormalNode, PointerFlowEdge, PointerFlowKind
from ..variable import VariableKind
from pythonstan.ir.ir_statements import IRClass


METACLASS_FIELD = Field(FieldKind.CLASS_METADATA, "metaclass")


def record_class_residual(solver, scope, origin, reason):
    solver.state._residual_effects[(scope, "class_protocol", origin)] = {
        "origin": str(origin), "reason": reason, "context": str(scope.context),
        "return_target": None,
        "effects": ["metaclass_selection", "class_creation_hooks", "may_raise"],
    }


def is_type_builtin(obj):
    return (isinstance(obj, BuiltinClassObject) and obj.builtin_name == "type") or (
        isinstance(obj, BuiltinFunctionObject) and obj.function_name == "type"
    )


def apply_class_metaclass_read(solver, scope, class_obj, attr_field, target_var, call_site):
    """Join metaclass method candidates bound to the actual class receiver.

    Both relation and method loads are persistent PFG dependencies. We retain
    ordinary class lookup candidates as well: exact precedence is not proved.
    """
    if attr_field.kind != FieldKind.ATTRIBUTE:
        return
    installed = getattr(solver, "_class_metaclass_reads", None)
    if installed is None:
        installed = solver._class_metaclass_reads = set()
    identity = (scope, class_obj, attr_field, target_var, call_site)
    if identity in installed:
        return
    installed.add(identity)
    state = solver.state
    suffix = f"{call_site.short_id()}@{id(class_obj)}@{attr_field.name}"
    meta_var = solver.variable_factory.make_variable(f"$metaclass@{suffix}")
    meta_ctx = state.get_variable(scope, scope.context, meta_var)
    meta_field = state.get_field(scope, scope.context, class_obj, METACLASS_FIELD)
    state._add_var_points_flow(meta_field, meta_ctx)
    method_var = solver.variable_factory.make_variable(f"$metaclass_attr@{suffix}")
    solver.add_constraint(scope, scope.context, LoadConstraint(meta_var, attr_field, method_var))
    method_ctx = state.get_variable(scope, scope.context, method_var)
    target_ctx = state.get_variable(scope, scope.context, target_var)
    def bind(_edge, pts):
        result = []
        for obj in pts:
            if isinstance(obj, MethodObject):
                result.append(obj.deliver_into(class_obj))
            elif isinstance(obj, FunctionObject):
                # A function installed later on a metaclass is a descriptor too.
                result.append(MethodObject(obj.context, obj.alloc_site, obj.container_scope,
                                           obj.ir, class_obj, class_obj))
            else:
                result.append(obj)
                record_class_residual(solver, scope, call_site,
                                      "Metaclass descriptor/attribute precedence is not discharged")
        return PointsToSet.from_objects(result)
    binder = GuardNode(bind)
    state._add_points_flow_edge(PointerFlowEdge(NormalNode(method_ctx), binder, PointerFlowKind.NORMAL))
    state._add_points_flow_edge(PointerFlowEdge(binder, NormalNode(target_ctx), PointerFlowKind.NORMAL))


def install_metaclass_call(solver, scope, context, call, class_obj):
    """Subscribe to metaclass __call__ without creating an empty fake call.

    Metaclass relations and attributes can arrive after the class invocation.
    Reuse their persistent read/binding path, and install the invocation only
    when a user function actually reaches it. Keep ordinary construction as a
    may-path: exact metaclass/descriptor precedence has not been established.
    """
    subscriptions = getattr(solver, "_metaclass_call_subscriptions", None)
    if subscriptions is None:
        subscriptions = solver._metaclass_call_subscriptions = set()
    identity = (scope, call, class_obj)
    if identity in subscriptions:
        return
    subscriptions.add(identity)
    suffix = f"{call.call_site.short_id()}@{id(class_obj)}@{len(subscriptions)}"
    observed = solver.variable_factory.make_variable(f"$metaclass_call_observed@{suffix}", VariableKind.TEMPORARY)
    active = solver.variable_factory.make_variable(f"$metaclass_call_active@{suffix}", VariableKind.TEMPORARY)
    observed_ctx = solver.state.get_variable(scope, context, observed)
    active_ctx = solver.state.get_variable(scope, context, active)
    apply_class_metaclass_read(solver, scope, class_obj, attr("__call__"), observed, call.call_site)
    invocation = CallConstraint(active, call.args, call.kwargs, call.target, call.call_site)
    registered = False

    def activate(_edge, pts):
        nonlocal registered
        functions = []
        for obj in pts:
            if isinstance(obj, FunctionObject):
                functions.append(obj)
            elif isinstance(obj, BuiltinMethodObject) and obj.method_name == "__call__" and is_type_builtin(obj.receiver):
                # The default type constructor path below already models this.
                continue
            else:
                solver._mark_unknown_call(scope, call,
                                          "Unmodeled metaclass __call__ descriptor or callable value")
        if functions:
            solver.state._residual_effects[(scope, "metaclass_call", call, class_obj)] = {
                "origin": str(call.call_site),
                "reason": "Metaclass __call__ and ordinary construction remain may-paths; precedence is unproved",
                "context": str(context), "return_target": call.target.name if call.target else None,
                "effects": ["metaclass_call_dispatch", "construction_precedence", "may_mutate_heap", "may_invoke_callbacks", "may_raise"],
            }
            if not registered:
                registered = True
                solver.add_constraint(scope, context, invocation)
        return PointsToSet.from_objects(functions)

    gate = GuardNode(activate)
    solver.state._add_points_flow_edge(PointerFlowEdge(NormalNode(observed_ctx), gate, PointerFlowKind.NORMAL))
    solver.state._add_points_flow_edge(PointerFlowEdge(gate, NormalNode(active_ctx), PointerFlowKind.NORMAL))


class ClassProtocolProcessor(Processor):
    def __init__(self):
        self._dynamic_classes = {}
        self._namespace_edges = set()

    def handle_call(self, solver, target, scope, call, callee_obj):
        if is_type_builtin(callee_obj) and len(call.args) == 3:
            return self._construct(solver, scope, call, callee_obj, 0)
        if (isinstance(callee_obj, BuiltinMethodObject)
                and callee_obj.method_name == "__new__" and is_type_builtin(callee_obj.receiver)
                and len(call.args) >= 4):
            return self._construct(solver, scope, call, callee_obj, 1)
        return False

    def _construct(self, solver, scope, call, builtin_obj, offset):
        state, context = solver.state, scope.context
        invocation = solver._register_builtin_dependencies(scope, context, call, builtin_obj)
        record_class_residual(solver, scope, call.call_site,
                              "Dynamic class may model retains namespace flows and ordinary exact-string-key "
                              "__set_name__ calls; namespace timing, non-string/unknown names, custom hook descriptors, "
                              "__init_subclass__, metaclass selection and exceptional exits remain open")
        identity = (scope, call.call_site)
        entry = self._dynamic_classes.get(identity)
        if entry is None:
            label = f"$dynamic_class_{call.call_site.short_id()}"
            base_name = f"$dynamic_bases_{len(self._dynamic_classes)}"
            tree = ast.ClassDef(name=label, bases=[ast.Name(id=base_name, ctx=ast.Load())],
                                keywords=[], body=[ast.Pass()], decorator_list=[])
            ast.fix_missing_locations(tree)
            ir = IRClass(f"{scope.stmt.get_qualname()}.{label}", tree)
            site = AllocSite(ir, AllocKind.CLASS)
            cls = ClassObject(solver.state.select_allocation_context(context, site), site, scope, ir)
            internal = Scope.new(cls, scope.module, context, ir, scope)
            state.set_internal_scope(cls, internal)
            state.obj_scope[cls] = scope
            state._heap.set_obj(scope, context, site, cls)
            base_var = solver.variable_factory.make_variable(base_name, VariableKind.GLOBAL)
            ir._kcfa_base_variables = (base_var,)
            ir._kcfa_dynamic_bases = True
            base_ctx = state.get_variable(scope, context, base_var)
            entry = self._dynamic_classes[identity] = (cls, base_ctx)
        cls, base_ctx = entry
        # Discarding the class result does not discard its construction effects.
        # The existing callsite cache still creates only one abstract owner.
        if call.target is not None:
            destination = state.get_variable(scope, context, call.target)
            solver.handle_new_points_to(destination, scope, PointsToSet.singleton(cls))
        name_ctx = state.get_variable(scope, context, call.args[offset])
        state._add_var_points_flow(name_ctx, state.get_field(scope, context, cls, attr("__name__")))
        meta_field = state.get_field(scope, context, cls, METACLASS_FIELD)
        if offset:
            meta_ctx = state.get_variable(scope, context, call.args[0])
            state._add_var_points_flow(meta_ctx, meta_field)
        else:
            solver.handle_new_points_to(meta_field, scope, PointsToSet.singleton(builtin_obj))
        bases_ctx = state.get_variable(scope, context, call.args[offset + 1])
        for bases in state.get_points_to(bases_ctx):
            solver._builtin_object_dependencies.setdefault(bases, set()).add(invocation)
            if isinstance(bases, ClassObject):
                # Invalid concrete bases must raise; retaining candidates here
                # is may-information only and does not discharge the residual.
                solver.handle_new_points_to(base_ctx, scope, PointsToSet.singleton(bases))
            else:
                state._add_var_points_flow(state.get_field(scope, context, bases, elem()), base_ctx)
        namespace_ctx = state.get_variable(scope, context, call.args[offset + 2])
        for namespace in state.get_points_to(namespace_ctx):
            solver._builtin_object_dependencies.setdefault(namespace, set()).add(invocation)
            if not isinstance(namespace, DictObject):
                record_class_residual(solver, scope, call.call_site,
                                      "Unknown/non-dict class namespace and custom mapping effects")
                solver.handle_new_points_to(state.get_field(scope, context, cls, unknown()),
                                            scope, PointsToSet.singleton(namespace))
                continue
            for obj, field in list(state._field_accesses):
                if obj != namespace:
                    continue
                if field.kind == FieldKind.KEY and field.key_type is None:
                    cls_field = attr(field.name)
                elif field.kind in {FieldKind.WILDCARD_KEY, FieldKind.UNKNOWN} or (
                    not solver.config.index_sensitive and field.kind == FieldKind.ELEMENT
                ):
                    cls_field = unknown()
                else:
                    continue
                self._install_namespace_field(solver, scope, namespace, field, cls, cls_field,
                                              call.call_site.statement)
        return True

    def _install_namespace_field(self, solver, scope, namespace, source_field, cls, target_field, origin):
        identity = (namespace, source_field, cls, target_field)
        if identity in self._namespace_edges:
            return
        self._namespace_edges.add(identity)
        state = solver.state
        source = state.get_field(scope, scope.context, namespace, source_field)
        if (source_field.kind == FieldKind.KEY and source_field.key_type is None
                and type(source_field.name) is str):
            from .construction_hooks import install_set_name_source
            # Observe original own namespace values before function-to-method
            # conversion. Logical class fields may also contain inherited
            # descriptors and must not supply construction-hook inputs.
            install_set_name_source(solver, scope, cls, source, source_field.name, origin)
        target = state.get_field(scope, scope.context, cls, target_field)
        def as_class_member(_edge, pts):
            return PointsToSet.from_objects([
                MethodObject(obj.context, obj.alloc_site, obj.container_scope, obj.ir, cls, None)
                if isinstance(obj, FunctionObject) else obj for obj in pts
            ])
        binder = GuardNode(as_class_member)
        state._add_points_flow_edge(PointerFlowEdge(NormalNode(source), binder, PointerFlowKind.NORMAL))
        state._add_points_flow_edge(PointerFlowEdge(binder, NormalNode(target), PointerFlowKind.NORMAL))
