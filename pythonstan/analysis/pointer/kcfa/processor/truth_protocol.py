"""Persistent may-callback flow for Python's implicit truth protocol.

Ordinary instance hooks are looked up on the type, with an explicit receiver.
The abstraction retains both __bool__ and __len__ candidates; precedence,
invalid return values and exceptional exits remain explicit obligations.
"""
from ..constraints import ResidualEffectConstraint
from ..object import ConstantObject, InstanceObject
from .type_protocol import install_instance_method_call


def apply_truth_test(solver, scope, constraint, objects):
    for receiver in objects:
        if isinstance(receiver, ConstantObject):
            continue
        solver.add_constraint(scope, scope.context, ResidualEffectConstraint(
            location=str(constraint.call_site),
            reason="Truth hook dispatch precedence, custom descriptors and return/exception validation remain open",
            effects=("truth_protocol", "may_invoke_callbacks", "may_mutate_heap", "may_raise"),
        ))
        if not isinstance(receiver, InstanceObject):
            continue
        for method in ("__bool__", "__len__"):
            install_instance_method_call(solver, scope, receiver, method, (), None, constraint.call_site)
