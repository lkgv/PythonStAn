"""Persistent may-calls for ordinary class construction hooks.

Class statement own namespace values subscribe to ``__set_name__``; inherited
attributes are not enumerated. Explicit metaclass functions receive a name,
bases tuple and source-derived namespace. Ordinary allocation remains a may-path.
This is not a temporal class-construction model: prepare, ordering, descriptor
hooks, conflicts, exceptions and returned-class replacement remain residual.
"""
from ..allocation import canonical_constant
from ..constraints import CallConstraint
from ..context import CallSite
from ..heap_model import attr, elem, key
from ..object import (
    AllocKind, AllocSite, ClassObject, ConstantObject, DictObject, FunctionObject,
    InstanceObject, MethodObject, TupleObject,
)
from ..points_to_set import PointsToSet
from ..pointer_flow_graph import GuardNode, NormalNode, PointerFlowEdge, PointerFlowKind
from ..variable import VariableKind
from .class_protocol import METACLASS_FIELD, record_class_residual


def _seed(solver, scope, context, label, obj):
    variable = solver.variable_factory.make_variable(label, VariableKind.TEMPORARY)
    ctx = solver.state.get_variable(scope, context, variable)
    solver.handle_new_points_to(ctx, scope, PointsToSet.singleton(obj))
    return variable


def _watch(solver, source, callback, target=None):
    gate = GuardNode(callback)
    solver.state._add_points_flow_edge(PointerFlowEdge(NormalNode(source), gate, PointerFlowKind.NORMAL))
    if target is not None:
        solver.state._add_points_flow_edge(PointerFlowEdge(gate, NormalNode(target), PointerFlowKind.NORMAL))


def _ordinary_hook(solver, scope, source, args, call_site, target, identity, *, allow_static=False):
    """Install a call only after an ordinary source function reaches its slot."""
    active = solver.variable_factory.make_variable("$construction_hook@" + identity, VariableKind.TEMPORARY)
    destination = solver.state.get_variable(scope, scope.context, active)
    invocation = CallConstraint(active, tuple(args), frozenset(), target, call_site)
    registered = False

    def activate(_edge, points):
        nonlocal registered
        methods = []
        for value in points:
            if isinstance(value, FunctionObject) and not (
                    value.ir.is_class_method or (value.ir.is_static_method and not allow_static)):
                # Explicit first argument supplies the receiver; a method
                # descriptor must not additionally bind it at call dispatch.
                methods.append(MethodObject(value.context, value.alloc_site, value.container_scope,
                                            value.ir, value.class_obj, None)
                               if isinstance(value, MethodObject) else value)
            else:
                record_class_residual(solver, scope, call_site,
                                      "Nonordinary class-construction hook descriptor remains unresolved")
        if methods and not registered:
            registered = True
            solver.add_constraint(scope, scope.context, invocation)
        return PointsToSet.from_objects(methods)

    _watch(solver, source, activate, destination)


def install_set_name_source(solver, scope, owner, source, name, origin):
    """Watch one *own namespace* value, not a logical inherited class lookup."""
    subscriptions = getattr(solver, "_construction_set_name_sources", None)
    if subscriptions is None:
        subscriptions = solver._construction_set_name_sources = set()
    identity = (scope, owner, source, name)
    if identity in subscriptions:
        return
    subscriptions.add(identity)
    call_site = CallSite(origin, scope_name=scope.stmt.get_qualname())
    suffix = f"{id(owner)}@{name}"
    owner_var = _seed(solver, scope, scope.context, "$construction_owner@" + suffix, owner)
    name_obj = canonical_constant(solver.state, scope.context,
                              AllocSite(("construction_name", owner, name), AllocKind.CONSTANT), name)
    name_var = _seed(solver, scope, scope.context, "$construction_name@" + suffix, name_obj)
    observed = set()

    def descriptors(_edge, points):
        for descriptor in points:
            if not isinstance(descriptor, InstanceObject) or descriptor in observed:
                continue
            observed.add(descriptor)
            label = suffix + "@" + str(id(descriptor))
            receiver_var = _seed(solver, scope, scope.context, "$construction_descriptor@" + label, descriptor)
            method = solver.state.get_field(scope, scope.context, descriptor.class_obj, attr("__set_name__"))
            _ordinary_hook(solver, scope, method, (receiver_var, owner_var, name_var),
                           call_site, None, "set_name@" + label)
        return PointsToSet.empty()

    _watch(solver, source, descriptors)


def install_class_statement_hooks(solver, scope, context, allocation, owner,
                                  internal_scope, own_variables):
    state = solver.state
    subscriptions = getattr(solver, "_construction_statements", None)
    if subscriptions is None:
        subscriptions = solver._construction_statements = set()
    if owner in subscriptions:
        return
    subscriptions.add(owner)
    origin = owner.ir
    for variable in own_variables:
        source = state.get_variable(internal_scope, internal_scope.context, variable)
        install_set_name_source(solver, scope, owner, source, variable.name, origin)

    # Only actual user metaclass objects trigger the additional work. Default
    # builtin type creates no fake unresolved __new__ or __init__ invocation.
    relation = state.get_field(scope, context, owner, METACLASS_FIELD)
    observed = set()

    def metaclasses(_edge, points):
        for meta in points:
            if not isinstance(meta, ClassObject) or meta in observed:
                continue
            observed.add(meta)
            _install_metaclass(solver, scope, context, allocation, owner, internal_scope,
                               own_variables, meta)
        return PointsToSet.empty()

    _watch(solver, relation, metaclasses)


def _install_metaclass(solver, scope, context, allocation, owner, internal_scope,
                       own_variables, meta):
    state = solver.state
    suffix = f"{id(owner)}@{id(meta)}"
    name = canonical_constant(solver.state, context, AllocSite(("construction_class_name", owner), AllocKind.CONSTANT), owner.ir.name)
    bases_site = AllocSite(("construction_bases", owner), AllocKind.TUPLE)
    namespace_site = AllocSite(("construction_namespace", owner), AllocKind.DICT)
    bases = TupleObject(solver.state.select_allocation_context(context, bases_site), bases_site)
    namespace = DictObject(solver.state.select_allocation_context(context, namespace_site), namespace_site)
    for obj in (bases, namespace):
        state._heap.set_obj(scope, context, obj.alloc_site, obj)
        state.obj_scope[obj] = scope
    meta_var = _seed(solver, scope, context, "$construction_meta@" + suffix, meta)
    owner_var = _seed(solver, scope, context, "$construction_class@" + suffix, owner)
    # A source __new__ can return the dynamic class allocated by type.__new__.
    # Its actual abstract identity must receive __init__ effects as well; keep
    # the original lexical class as an explicitly conservative construction path.
    state._add_var_points_flow(state.get_variable(scope, context, allocation.target),
                               state.get_variable(scope, context, owner_var))
    name_var = _seed(solver, scope, context, "$construction_class_name@" + suffix, name)
    bases_var = _seed(solver, scope, context, "$construction_bases@" + suffix, bases)
    namespace_var = _seed(solver, scope, context, "$construction_namespace@" + suffix, namespace)
    for index, variable in enumerate(getattr(owner.ir, "_kcfa_base_variables", ())):
        source = state.get_variable(scope, context, variable)
        state._add_var_points_flow(source, state.get_field(scope, context, bases, key(index)))
        state._add_var_points_flow(source, state.get_field(scope, context, bases, elem()))
    for variable in own_variables:
        source = state.get_variable(internal_scope, internal_scope.context, variable)
        state._add_var_points_flow(source, state.get_field(scope, context, namespace, key(variable.name)))
        state._add_var_points_flow(source, state.get_field(scope, context, namespace, elem()))
    call_site = CallSite(owner.ir, scope_name=scope.stmt.get_qualname())
    _ordinary_hook(solver, scope, state.get_field(scope, context, meta, attr("__new__")),
                   (meta_var, name_var, bases_var, namespace_var), call_site,
                   allocation.target, "meta_new@" + suffix, allow_static=True)
    _ordinary_hook(solver, scope, state.get_field(scope, context, meta, attr("__init__")),
                   (owner_var, name_var, bases_var, namespace_var), call_site,
                   None, "meta_init@" + suffix)
    record_class_residual(solver, scope, owner.ir,
                          "Source metaclass hooks retain ordinary allocation and source return may-paths; "
                          "prepare, exact replacement, ordering, descriptors, conflicts and exceptions remain open")
