from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..constraints import AllocConstraint, CallConstraint, Constraint
    from ..context import Ctx, NativeContext, Scope
    from ..object import AbstractObject
    from ..points_to_set import PointsToSet
    from ..solver import PointerSolver
    from ..variable import FieldAccess, Variable


class Processor:
    """Concrete default hooks; a truthy override intercepts the native operation.

    The default returns the existing None/no-interception result for every hook.
    """

    def handle_allocation(
        self,
        solver: "PointerSolver",
        target: "Ctx[Variable | FieldAccess]",
        scope: "Scope",
        context: "NativeContext",
        constraint: "AllocConstraint",
    ) -> bool | None:
        return None

    def handle_call(
        self,
        solver: "PointerSolver",
        target: "Ctx[Variable | FieldAccess]",
        scope: "Scope",
        constraint: "CallConstraint",
        callee_obj: "AbstractObject",
    ) -> bool | None:
        return None

    def handle_constraint(
        self,
        solver: "PointerSolver",
        target: "Ctx[Variable | FieldAccess]",
        scope: "Scope",
        constraint: "Constraint",
        pts: "PointsToSet",
    ) -> bool | None:
        return None

    def handle_new_constraint(
        self, solver: "PointerSolver", scope: "Scope", constraint: "Constraint"
    ) -> bool | None:
        return None

    def handle_pts(
        self,
        solver: "PointerSolver",
        target: "Ctx[Variable | FieldAccess]",
        scope: "Scope | None",
        pts: "PointsToSet",
    ) -> bool | None:
        return None

    def handle_new_points_to(
        self,
        solver: "PointerSolver",
        target: "Ctx[Variable | FieldAccess]",
        scope: "Scope",
        pts: "PointsToSet",
    ) -> bool | None:
        return None
