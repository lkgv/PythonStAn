"""Persistent ordinary-instance special-method call subscriptions.

This helper produces ordinary function may-calls and joins descriptor-bound
callable results from the type slot. It bypasses instance shadows, preserves
late class writes and keeps the original IR callsite, including compiler-
generated temporary names. Dispatch precedence and exceptions remain open.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..constraints import CallConstraint, OperatorProtocolConstraint, ResidualEffectConstraint
from ..context import CallSite, Scope
from ..heap_model import attr
from ..object import BuiltinInstanceObject, ConstantObject, FunctionObject, InstanceObject
from ..pointer_flow_graph import GuardNode, NormalNode, PointerFlowEdge, PointerFlowKind
from ..points_to_set import PointsToSet
from ..protocol_context import KeywordPartition
from ..variable import Variable, VariableKind

if TYPE_CHECKING:
    from ..solver import PointerSolver

type InstanceMethodKey = tuple[
    Scope,
    InstanceObject,
    str,
    tuple[Variable, ...],
    Variable | None,
    CallSite,
    KeywordPartition | None,
]


def install_instance_method_call(
    solver: PointerSolver,
    scope: Scope,
    receiver: InstanceObject,
    method: str,
    arguments: tuple[Variable, ...],
    target: Variable | None,
    call_site: CallSite,
    semantic_partition: KeywordPartition | None = None,
) -> None:
    installed = solver._type_protocol_calls if hasattr(solver, "_type_protocol_calls") else None
    if installed is None:
        installed = solver._type_protocol_calls = set()
    identity = (scope, receiver, method, arguments, target, call_site, semantic_partition)
    if identity in installed:
        return
    installed.add(identity)
    state = solver.state
    suffix = f"{call_site.short_id()}@{id(receiver)}@{method}"
    bound = solver.variable_factory.make_variable(
        f"$protocol_receiver@{suffix}", VariableKind.TEMPORARY
    )
    state_receiver = state.get_variable(scope, scope.context, bound)
    solver.handle_new_points_to(state_receiver, scope, PointsToSet.singleton(receiver))
    source = state.get_field(scope, scope.context, receiver.class_obj, attr(method))
    active = solver.variable_factory.make_variable(
        f"$protocol_method@{suffix}", VariableKind.TEMPORARY
    )
    destination = state.get_variable(scope, scope.context, active)
    invocation = CallConstraint(
        active,
        (bound, *arguments),
        frozenset(),
        target,
        call_site,
        semantic_partition=semantic_partition,
    )
    registered = False
    descriptor_registered = False

    def activate(_edge: PointerFlowEdge, points: PointsToSet) -> PointsToSet:
        nonlocal registered, descriptor_registered
        methods = [obj for obj in points if isinstance(obj, FunctionObject)]
        if methods and not registered:
            registered = True
            solver.add_constraint(scope, scope.context, invocation)
        if not descriptor_registered and any(
            isinstance(obj, (InstanceObject, BuiltinInstanceObject)) for obj in points
        ):
            descriptor_registered = True
            # Special lookup bypasses instance shadows but still invokes the
            # descriptor protocol on a value found in the receiver's type.
            # Reuse the persistent class-slot join, including late __get__ and
            # returned-callable facts. Ordinary functions are handled above.
            from .attribute_semantics import _DescriptorAccessConstraint

            result = solver.variable_factory.make_variable(
                f"$protocol_descriptor_result@{suffix}", VariableKind.TEMPORARY
            )
            rule = _DescriptorAccessConstraint(
                receiver,
                receiver.class_obj,
                "__get__",
                call_site,
                "native_special_" + suffix,
                target=result,
            )
            solver._register_dynamic_constraint(scope, source, rule)
            # __get__ already decided binding. Its result may be a plain
            # function, an already-bound method or a callable object; adding
            # `bound` here would supply the original key a second time.
            solver.add_constraint(
                scope,
                scope.context,
                CallConstraint(
                    result,
                    arguments,
                    frozenset(),
                    target,
                    call_site,
                    semantic_partition=semantic_partition,
                ),
            )
        return PointsToSet.from_objects(methods)

    gate = GuardNode(activate)
    state._add_points_flow_edge(PointerFlowEdge(NormalNode(source), gate, PointerFlowKind.NORMAL))
    state._add_points_flow_edge(
        PointerFlowEdge(gate, NormalNode(destination), PointerFlowKind.NORMAL)
    )


def apply_operator(
    solver: PointerSolver,
    scope: Scope,
    constraint: OperatorProtocolConstraint,
    objects: PointsToSet,
) -> None:
    for receiver in objects:
        # Exact builtin scalar payloads cannot call a user-defined type hook.
        # Their value/exception transfer belongs to the scalar AI component.
        if isinstance(receiver, ConstantObject) and type(receiver.value) in (
            bool,
            int,
            float,
            complex,
            str,
            bytes,
            type(None),
        ):
            continue
        solver.add_constraint(
            scope,
            scope.context,
            ResidualEffectConstraint(
                location=str(constraint.call_site),
                reason="Operator methods are may-candidates; precedence, NotImplemented, in-place, descriptor and exception semantics remain open",
                effects=(
                    "operator_protocol",
                    "inplace_protocol",
                    "may_invoke_callbacks",
                    "may_mutate_heap",
                    "may_raise",
                ),
            ),
        )
        if isinstance(receiver, InstanceObject):
            install_instance_method_call(
                solver,
                scope,
                receiver,
                constraint.method,
                constraint.arguments,
                constraint.target,
                constraint.call_site,
            )
