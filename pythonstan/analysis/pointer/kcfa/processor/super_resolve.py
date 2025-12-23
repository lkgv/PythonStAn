from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any
from .processor import Processor
from ..points_to_set import PointsToSet
from ..constraints import SuperResolveConstraint

if TYPE_CHECKING:
    from ..pointer_flow_graph import NormalNode
    from ..solver import PointerSolver
    from ..context import Ctx, Scope
    from ..constraints import Constraint


class SuperResolveProcessor(Processor):    
    def handle_constraint(self, solver: 'PointerSolver', target: 'Ctx[Any]', scope: 'Scope', constraint: 'Constraint', pts: 'PointsToSet') -> bool:
        if isinstance(constraint, SuperResolveConstraint):
            return self._apply_super_resolve(scope, target, constraint, pts)
        return False

    def handle_new_constraint(self, solver: 'PointerSolver', scope: 'Scope', constraint: 'Constraint') -> bool:
        if isinstance(constraint, SuperResolveConstraint):
            target = solver.state.get_variable(scope, scope.context, constraint.target)
            solver.state.constraints.add(scope, target, constraint)
            # If target already has objects, apply the constraint immediately
            target_pts = solver.state.get_points_to(target)
            if len(target_pts) > 0:
                self._apply_super_resolve(scope, target, constraint, target_pts)
            return True
        
        return False

    def _apply_super_resolve(self, solver: 'PointerSolver', scope: 'Scope', variable: 'Ctx', c: 'SuperResolveConstraint', pts: 'PointsToSet'):
        """Apply super resolve constraint: populate SuperObject with class/instance.
        
        This constraint resolves super() arguments and creates properly initialized
        SuperObject instances:
        
        1. For explicit super(Class, obj): get class and instance from variables
        2. For implicit super(): look up __class__ cell var and first param
        3. Create SuperObject with current_class and instance_obj set
        4. Add to target variable's points-to set via worklist
        
        The resolved SuperObject then works with state.get_field() for MRO-based
        field resolution via InheritanceConstraint.
        """
        from ..object import SuperObject, ObjectFactory, ClassObject, InstanceObject
        
        context = scope.context
        current_class = None
        instance_obj = None
        
        if not c.implicit:
            # Explicit super(Class, instance) - resolve from provided variables
            if c.class_var:
                class_var = solver.state.get_variable(scope, context, c.class_var)
                class_pts = solver.state.get_points_to(class_var)
                for obj in class_pts:
                    if isinstance(obj, ClassObject):
                        current_class = obj
                        break
            
            if c.instance_var:
                instance_var = solver.state.get_variable(scope, context, c.instance_var)
                instance_pts = solver.state.get_points_to(instance_var)
                for obj in instance_pts:
                    # Accept any object as instance (InstanceObject or others)
                    instance_obj = obj
                    break
        else:
            # Implicit super() - look up from enclosing function scope
            # This requires __class__ cell variable and first parameter (self)
            # For now, handle conservatively - SuperObject will work without explicit resolution
            pass
        
        # For each generic SuperObject allocation in pts, create resolved version
        target_var = solver.state.get_variable(scope, context, c.target)
        for super_alloc in pts:
            # Create SuperObject with resolved class and instance
            resolved_super = ObjectFactory.create_super(
                context=super_alloc.context,
                stmt=super_alloc.alloc_site.stmt,
                current_class=current_class,
                instance_obj=instance_obj
            )
            
            # Add resolved super object to target's points-to set via worklist
            solver.handle_new_points_to(target_var, scope, PointsToSet.singleton(resolved_super))
 