from typing import TYPE_CHECKING, override

from .processor import Processor

if TYPE_CHECKING:
    from ..constraints import AllocConstraint, CallConstraint, Constraint
    from ..context import Ctx, NativeContext, Scope
    from ..object import AbstractObject
    from ..points_to_set import PointsToSet
    from ..solver import PointerSolver
    from ..variable import FieldAccess, Variable


class ComposeProcessor(Processor):
    """
    Compose multiple processors into one.
    Each processor will be called in order until one of them returns True.
    """

    def __init__(self, processors: list[Processor]) -> None:
        self.processors = processors

    def add_processor(self, processor: Processor) -> None:
        self.processors.append(processor)

    def _call_processors(self, method_name: str, *args, **kwargs) -> bool:
        for processor in self.processors:
            if getattr(processor, method_name)(*args, **kwargs):
                return True
        return False

    @override
    def handle_allocation(
        self,
        solver: "PointerSolver",
        target: "Ctx[Variable | FieldAccess]",
        scope: "Scope",
        context: "NativeContext",
        constraint: "AllocConstraint",
    ) -> bool:
        for processor in self.processors:
            if processor.handle_allocation(solver, target, scope, context, constraint):
                return True
        return False

    @override
    def handle_call(
        self,
        solver: "PointerSolver",
        target: "Ctx[Variable | FieldAccess]",
        scope: "Scope",
        constraint: "CallConstraint",
        callee_obj: "AbstractObject",
    ) -> bool:
        for processor in self.processors:
            if processor.handle_call(solver, target, scope, constraint, callee_obj):
                return True
        return False

    @override
    def handle_constraint(
        self,
        solver: "PointerSolver",
        target: "Ctx[Variable | FieldAccess]",
        scope: "Scope",
        constraint: "Constraint",
        pts: "PointsToSet",
    ) -> bool:
        for processor in self.processors:
            if processor.handle_constraint(solver, target, scope, constraint, pts):
                return True
        return False

    @override
    def handle_new_constraint(
        self, solver: "PointerSolver", scope: "Scope", constraint: "Constraint"
    ) -> bool:
        for processor in self.processors:
            if processor.handle_new_constraint(solver, scope, constraint):
                return True
        return False

    @override
    def handle_pts(
        self,
        solver: "PointerSolver",
        target: "Ctx[Variable | FieldAccess]",
        scope: "Scope | None",
        pts: "PointsToSet",
    ) -> bool:
        for processor in self.processors:
            if processor.handle_pts(solver, target, scope, pts):
                return True
        return False

    @override
    def handle_new_points_to(
        self,
        solver: "PointerSolver",
        target: "Ctx[Variable | FieldAccess]",
        scope: "Scope",
        pts: "PointsToSet",
    ) -> bool:
        for processor in self.processors:
            if processor.handle_new_points_to(solver, target, scope, pts):
                return True
        return False
