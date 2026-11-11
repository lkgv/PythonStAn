from typing import TYPE_CHECKING, Any
from .processor import Processor
from ..points_to_set import PointsToSet
from ..constraints import SuperResolveConstraint, AllocConstraint
from ..object import SuperObject, AllocKind
from pythonstan.ir.ir_statements import IRCall

if TYPE_CHECKING:
    from ..pointer_flow_graph import NormalNode
    from ..solver import PointerSolver
    from ..context import AbstractContext, Ctx, Scope
    from ..constraints import Constraint


class SuperResolveProcessor(Processor):    
    def handle_allocation(self, solver: 'PointerSolver', target: 'Ctx[Any]', scope: 'Scope', context: 'AbstractContext', c: AllocConstraint) -> bool:
        if c.alloc_site.kind != AllocKind.OBJECT:
            return False
        if not self._is_super_alloc(solver, target, c):
            return False
        super_obj = SuperObject(context=solver.state.select_allocation_context(context, c.alloc_site), alloc_site=c.alloc_site, current_class=None, instance_obj=None)
        solver.state._heap.set_obj(scope, context, c.alloc_site, super_obj)
        solver.state.obj_scope[super_obj] = scope
        solver.handle_new_points_to(target, scope, PointsToSet.singleton(super_obj))
        return True

    def handle_constraint(self, solver: 'PointerSolver', target: 'Ctx[Any]', scope: 'Scope', constraint: 'Constraint', pts: 'PointsToSet') -> bool:
        if isinstance(constraint, SuperResolveConstraint):
            return self._apply_super_resolve(solver, scope, target, constraint, pts)
        return False

    def handle_new_constraint(self, solver: 'PointerSolver', scope: 'Scope', constraint: 'Constraint') -> bool:
        if isinstance(constraint, SuperResolveConstraint):
            # super's allocation and its arguments can arrive in any order.
            # Subscribing only to the result loses arguments discovered later.
            for variable in self._argument_variables(scope, constraint):
                trigger = solver.state.get_variable(scope, scope.context, variable)
                solver._register_dynamic_constraint(scope, trigger, constraint)
            return True
        
        return False

    @staticmethod
    def _argument_variables(scope, constraint):
        from ..variable import Variable, VariableKind
        from pythonstan.ir.ir_statements import IRFunc
        variables = constraint.variables()
        if constraint.implicit:
            variables.add(Variable("__class__", VariableKind.CELL))
            if isinstance(scope.stmt, IRFunc) and scope.stmt.args:
                positional = (*scope.stmt.args.posonlyargs, *scope.stmt.args.args)
                if positional:
                    variables.add(Variable(positional[0].arg, VariableKind.LOCAL))
        return variables

    def _apply_super_resolve(self, solver: 'PointerSolver', scope: 'Scope', variable: 'Ctx', c: 'SuperResolveConstraint', pts: 'PointsToSet'):
        """Join every possible argument pair, including delayed arguments.

        This is a may relation, not proof of Python's receiver validity, exact
        C3 suffix, or descriptor precedence. Those obligations remain explicit.
        """
        from ..object import ObjectFactory, ClassObject, MethodObject
        from ..variable import Variable, VariableKind
        from pythonstan.ir.ir_statements import IRFunc

        context = scope.context
        classes = set()
        receivers = set()
        solver.state._residual_effects[(scope, "super_resolution", c)] = {
            "origin": str(c), "context": str(context), "return_target": c.target.name,
            "reason": "Super may-paths retain receiver validation, C3 suffix and descriptor obligations",
            "effects": ["super_receiver_check", "super_mro_suffix", "descriptor_precedence", "may_raise"],
        }

        if not c.implicit:
            if c.class_var:
                class_var = solver.state.get_variable(scope, context, c.class_var)
                classes.update(obj for obj in solver.state.get_points_to(class_var)
                               if isinstance(obj, ClassObject))
            if c.instance_var:
                instance_var = solver.state.get_variable(scope, context, c.instance_var)
                receivers.update(solver.state.get_points_to(instance_var))
            else:
                receivers.add(None)  # Explicit one-argument unbound super.
        else:
            class_ctx = solver.state.get_variable(scope, context, Variable("__class__", VariableKind.CELL))
            classes.update(obj for obj in solver.state.get_points_to(class_ctx)
                           if isinstance(obj, ClassObject))
            if isinstance(scope.obj, MethodObject):
                # Method rebinding changes the receiver, not lexical __class__.
                lexical_class = getattr(scope.obj.container_scope, "obj", None)
                if isinstance(lexical_class, ClassObject):
                    classes.add(lexical_class)
                elif not classes:
                    classes.add(scope.obj.class_obj)
                if scope.obj.instance_obj is not None:
                    receivers.add(scope.obj.instance_obj)
                elif scope.obj.ir.is_class_method:
                    receivers.add(scope.obj.class_obj)
            if isinstance(scope.stmt, IRFunc) and scope.stmt.args:
                positional = (*scope.stmt.args.posonlyargs, *scope.stmt.args.args)
                if positional:
                    self_var = Variable(positional[0].arg, VariableKind.LOCAL)
                    self_ctx = solver.state.get_variable(scope, context, self_var)
                    receivers.update(solver.state.get_points_to(self_ctx))

        # Argument notifications contain argument deltas, not SuperObjects.
        # Always read the result's persistent allocation facts and keep all
        # class/receiver combinations; never choose an arbitrary first object.
        target_var = solver.state.get_variable(scope, context, c.target)
        for super_alloc in solver.state.get_points_to(target_var):
            if not isinstance(super_alloc, SuperObject) or super_alloc.current_class is not None:
                continue
            resolved = PointsToSet.from_objects(
                ObjectFactory.create_super(super_alloc.context, super_alloc.alloc_site.stmt,
                                           current_class=current_class, instance_obj=receiver)
                for current_class in classes for receiver in receivers
            )
            solver.handle_new_points_to(target_var, scope, resolved)
        return True

    def _is_super_alloc(self, solver: 'PointerSolver', target: 'Ctx[Any]', c: AllocConstraint) -> bool:
        if isinstance(c.alloc_site.stmt, IRCall) and c.alloc_site.stmt.func_name == "super":
            return True
        for _, constraint in solver.state.constraints.iter_scoped_by_variable(target):
            if isinstance(constraint, SuperResolveConstraint):
                return True
        return False

