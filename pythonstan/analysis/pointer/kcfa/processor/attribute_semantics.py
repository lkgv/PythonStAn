import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from .processor import Processor
from ..constraints import (
    AttrReadConstraint,
    AttrWriteConstraint,
    LoadConstraint,
    StoreConstraint,
    CallConstraint,
    AllocConstraint,
    Constraint,
)
from ..context import Ctx
from ..heap_model import Field, FieldKind, attr, unknown
from ..object import (AllocKind, AllocSite, ClassObject, InstanceObject,
                      BuiltinInstanceObject, FunctionObject, MethodObject, SuperObject)
from ..points_to_set import PointsToSet
from ..variable import Variable, VariableKind
from pythonstan.ir.ir_statements import IRAssign

if TYPE_CHECKING:
    from ..context import Scope, AbstractContext
    from ..solver import PointerSolver
    from ..constraints import Constraint
    from ..object import AbstractObject, ClassObject

__all__ = ["AttributeSemanticsProcessor"]


@dataclass(frozen=True)
class _DescriptorAccessConstraint(Constraint):
    """A persistent join between an access and each class-field candidate.

    Hook loads created from this rule remain subscribed when the descriptor's
    class or inherited method facts arrive later. Absence in the current
    points-to set is never used as a proof that a hook does not exist.
    """

    receiver: Any
    owner: ClassObject
    method: str
    call_site: Any
    token: str
    target: Optional[Variable] = None
    source: Optional[Variable] = None

    def variables(self):
        return {var for var in (self.target, self.source) if var is not None}

    def __str__(self):
        return f"DescriptorAccessConstraint: {self.method}@{self.call_site.short_id()}"


@dataclass(frozen=True)
class ClassMethodBindConstraint(Constraint):
    """Join wrapped callable facts with the actual owner of __get__."""

    function: Variable
    arguments: tuple
    target: Variable
    call_site: Any

    def variables(self):
        return {self.function, self.target, *self.arguments}

    def __str__(self):
        return f"ClassMethodBindConstraint: {self.target}@{self.call_site.short_id()}"


class AttributeSemanticsProcessor(Processor):
    _READ_INTERCEPT_SKIP = {"__getattribute__", "__getattr__"}
    _WRITE_INTERCEPT_SKIP = {"__setattr__"}
    
    def __init__(self) -> None:
        self._const_alloc_sites: Dict[str, AllocSite] = {}
    
    def handle_new_constraint(self, solver: 'PointerSolver', scope: 'Scope', constraint: 'Constraint') -> bool:
        state = solver.state
        if isinstance(constraint, ClassMethodBindConstraint):
            for var in (constraint.function, *constraint.arguments):
                trigger = state.get_variable(scope, scope.context, var)
                solver._register_dynamic_constraint(scope, trigger, constraint)
            return True
        if isinstance(constraint, AttrReadConstraint):
            base_ctx = state.get_variable(scope, scope.context, constraint.base)
            solver._register_dynamic_constraint(scope, base_ctx, constraint)
            return True
        if isinstance(constraint, AttrWriteConstraint):
            base_ctx = state.get_variable(scope, scope.context, constraint.base)
            solver._register_dynamic_constraint(scope, base_ctx, constraint)
            return True
        return False
    
    def handle_constraint(
        self,
        solver: 'PointerSolver',
        target: 'Ctx[Any]',
        scope: 'Scope',
        constraint: 'Constraint',
        pts: 'PointsToSet',
    ) -> bool:
        if isinstance(constraint, ClassMethodBindConstraint):
            self._apply_classmethod_binding(solver, scope, constraint)
            return True
        if isinstance(constraint, _DescriptorAccessConstraint):
            self._apply_descriptor_candidates(solver, scope, constraint, pts)
            return True
        if isinstance(constraint, AttrReadConstraint):
            self._apply_attr_read(solver, scope, target, constraint, pts)
            return True
        if isinstance(constraint, AttrWriteConstraint):
            self._apply_attr_write(solver, scope, target, constraint, pts)
            return True
        return False
    
    def _apply_attr_read(
        self,
        solver: 'PointerSolver',
        scope: 'Scope',
        base_ctx: 'Ctx[Any]',
        constraint: AttrReadConstraint,
        pts: 'PointsToSet',
    ) -> None:
        attr_field, attr_name = self._resolve_attr(constraint.attr)
        for base_obj in pts:
            if isinstance(base_obj, InstanceObject):
                self._apply_instance_attr_read(
                    solver=solver,
                    scope=scope,
                    base_obj=base_obj,
                    attr_field=attr_field,
                    attr_name=attr_name,
                    target_var=constraint.target,
                    call_site=constraint.call_site,
                )
            elif isinstance(base_obj, ClassObject):
                self._apply_class_attr_read(solver, scope, base_obj, attr_field,
                                           attr_name, constraint.target,
                                           constraint.call_site)
            elif isinstance(base_obj, SuperObject):
                self._apply_super_attr_read(solver, scope, base_obj, attr_field,
                                           attr_name, constraint.target, constraint.call_site)
            else:
                solver._apply_load(
                    scope,
                    base_ctx,
                    LoadConstraint(
                        base=constraint.base,
                        field=attr_field,
                        target=constraint.target,
                    ),
                    PointsToSet.singleton(base_obj),
                )
    
    def _apply_attr_write(
        self,
        solver: 'PointerSolver',
        scope: 'Scope',
        base_ctx: 'Ctx[Any]',
        constraint: AttrWriteConstraint,
        pts: 'PointsToSet',
    ) -> None:
        attr_field, attr_name = self._resolve_attr(constraint.attr)
        for base_obj in pts:
            if isinstance(base_obj, InstanceObject):
                self._apply_instance_attr_write(
                    solver=solver,
                    scope=scope,
                    base_obj=base_obj,
                    attr_field=attr_field,
                    attr_name=attr_name,
                    source_var=constraint.source,
                    call_site=constraint.call_site,
                )
            else:
                solver._apply_store(
                    scope,
                    base_ctx,
                    StoreConstraint(
                        base=constraint.base,
                        field=attr_field,
                        source=constraint.source,
                    ),
                    PointsToSet.singleton(base_obj),
                )
    
    def _apply_instance_attr_read(
        self, solver, scope, base_obj, attr_field, attr_name, target_var, call_site,
    ):
        state = solver.state
        context = scope.context
        token = f"{self._attr_token(attr_name)}@{id(base_obj)}"
        target = state.get_variable(scope, context, target_var)
        if attr_name == "__dict__":
            from .heap_mapping import instance_namespace_view
            view = instance_namespace_view(state, scope, base_obj)
            solver.handle_new_points_to(target, scope, PointsToSet.singleton(view))
            state._residual_effects[(scope, call_site, "instance_namespace")] = {
                "origin": str(call_site), "context": str(context),
                "reason": "Own-namespace may view leaves slots, custom __dict__, replacement and deletion open",
                "effects": ["namespace_presence", "custom_descriptor", "deletion", "may_raise"],
            }
        instance_field = state.get_field(scope, context, base_obj, attr_field)
        # May-points-to membership is not a must-presence proof. A priority
        # selector here would suppress ordinary values for other concrete
        # alternatives and make the result depend on worklist order.
        state._add_var_points_flow(instance_field, target)
        class_field = self._get_class_field(solver, scope, base_obj.class_obj, attr_field)
        self._register_descriptor_access(
            solver, scope, class_field, base_obj, base_obj.class_obj, "__get__",
            call_site, self._attr_token(attr_name), target=target_var,
        )
        if attr_name not in self._READ_INTERCEPT_SKIP:
            instance_var = self._make_object_var(
                solver, scope, base_obj, "attr_self", call_site, token,
            )
            name_var = self._make_const_name_var(solver, scope, attr_name, call_site)
            for hook, prefix in (("__getattribute__", "getattribute"),
                                 ("__getattr__", "getattr")):
                hook_var = self._make_temp_var(prefix, call_site, token)
                solver.add_constraint(scope, context, LoadConstraint(
                    base=instance_var, field=attr(hook), target=hook_var,
                ))
                solver.add_constraint(scope, context, CallConstraint(
                    callee=hook_var, args=(name_var,), kwargs=frozenset(),
                    target=target_var, call_site=call_site,
                ))

    def _apply_instance_attr_write(
        self,
        solver: 'PointerSolver',
        scope: 'Scope',
        base_obj: InstanceObject,
        attr_field: Field,
        attr_name: str,
        source_var: Variable,
        call_site,
    ) -> None:
        state = solver.state
        context = scope.context
        attr_token = self._attr_token(attr_name)
        
        inst_var = self._make_object_var(
            solver,
            scope,
            base_obj,
            "attr_self",
            call_site,
            f"{attr_token}@{id(base_obj)}",
        )
        name_var = self._make_const_name_var(solver, scope, attr_name, call_site)
        source_ctx = state.get_variable(scope, context, source_var)
        
        class_obj = base_obj.class_obj
        class_field = self._get_class_field(solver, scope, class_obj, attr_field)
        self._register_descriptor_access(
            solver, scope, class_field, base_obj, class_obj, "__set__",
            call_site, attr_token, source=source_var,
        )
        # Weak fallback is deliberately retained: growing may-facts cannot
        # prove that every concrete class alternative has a data descriptor.
        # Proving descriptor priority requires a separate must/absence domain.
        instance_field = state.get_write_field(scope, context, base_obj, attr_field)
        state._add_var_points_flow(source_ctx, instance_field)
        
        if attr_name not in self._WRITE_INTERCEPT_SKIP:
            setattr_var = self._make_temp_var("setattr", call_site, f"{attr_token}@{id(base_obj)}")
            solver.add_constraint(
                scope,
                context,
                LoadConstraint(
                    base=inst_var,
                    field=attr("__setattr__"),
                    target=setattr_var,
                ),
            )
            solver.add_constraint(
                scope,
                context,
                CallConstraint(
                    callee=setattr_var,
                    args=(name_var, source_var),
                    kwargs=frozenset(),
                    target=None,
                    call_site=call_site,
                ),
            )

    def _apply_class_attr_read(self, solver, scope, class_obj, attr_field,
                              attr_name, target_var, call_site):
        from .class_protocol import apply_class_metaclass_read
        class_field = self._get_class_field(solver, scope, class_obj, attr_field)
        target = solver.state.get_variable(scope, scope.context, target_var)
        # Ordinary values and functions retain their existing inherited binding.
        # Descriptor results join this may-value; no speculative fact is killed.
        solver.state._add_var_points_flow(class_field, target)
        self._register_descriptor_access(
            solver, scope, class_field, None, class_obj, "__get__",
            call_site, self._attr_token(attr_name), target=target_var,
        )
        apply_class_metaclass_read(solver, scope, class_obj, attr_field,
                                  target_var, call_site)

    def _register_descriptor_access(self, solver, scope, class_field, receiver,
                                    owner, method, call_site, token,
                                    target=None, source=None):
        rule = _DescriptorAccessConstraint(
            receiver, owner, method, call_site, token, target, source,
        )
        solver._register_dynamic_constraint(scope, class_field, rule)

    def _apply_super_attr_read(self, solver, scope, proxy, attr_field,
                              attr_name, target_var, call_site):
        """Join descriptor results from the super suffix with actual receivers.

        A super proxy is not the instance passed to ``descriptor.__get__``.
        Its source field includes late ancestor candidates; retain a persistent
        descriptor subscription as well as ordinary function/value may facts.
        """
        from ..pointer_flow_graph import GuardNode, NormalNode, PointerFlowEdge, PointerFlowKind
        from .class_protocol import METACLASS_FIELD

        state = solver.state
        class_field = state.get_field(scope, scope.context, proxy, attr_field)
        target = state.get_variable(scope, scope.context, target_var)
        state._add_var_points_flow(class_field, target)
        receiver = proxy.instance_obj
        token = "super_" + self._attr_token(attr_name) + "@" + str(id(proxy))
        if isinstance(receiver, InstanceObject):
            self._register_descriptor_access(solver, scope, class_field, receiver,
                receiver.class_obj, "__get__", call_site, token, target=target_var)
        elif isinstance(receiver, ClassObject):
            # On super(T, C), C may be a subclass of T or an instance of a
            # metaclass subtype. Until that distinction is proved, keep both
            # unbound class access and bound metaclass access possibilities.
            seen = set()
            def install(owner):
                if not isinstance(owner, ClassObject) or owner in seen:
                    return
                seen.add(owner)
                for actual in (None, receiver):
                    self._register_descriptor_access(solver, scope, class_field, actual,
                        owner, "__get__", call_site, token, target=target_var)
            install(receiver)
            metadata = state.get_field(scope, scope.context, receiver, METACLASS_FIELD)
            def observe(_edge, points):
                for owner in points:
                    install(owner)
                return PointsToSet.empty()
            state._add_points_flow_edge(PointerFlowEdge(NormalNode(metadata), GuardNode(observe), PointerFlowKind.NORMAL))
            for owner in state.get_points_to(metadata):
                install(owner)
        elif isinstance(proxy.current_class, ClassObject):
            # Lexical fallback only; unknown receiver/type/descriptor behavior
            # remains an explicit super lookup residual.
            self._register_descriptor_access(solver, scope, class_field, receiver,
                proxy.current_class, "__get__", call_site, token, target=target_var)

    def _apply_descriptor_candidates(self, solver, scope, rule, candidates):
        for descriptor in candidates:
            # Function/MethodObject binding is already represented by the PFG.
            # User and builtin descriptor objects need an actual protocol call.
            if not isinstance(descriptor, (InstanceObject, BuiltinInstanceObject)):
                continue
            token = f"{rule.token}@{id(descriptor)}@{id(rule.receiver)}@{id(rule.owner)}"
            descriptor_var = self._make_object_var(
                solver, scope, descriptor, "descriptor_candidate", rule.call_site, token,
            )
            hook_var = self._make_temp_var("descriptor_hook_" + rule.method,
                                           rule.call_site, token)
            solver.add_constraint(scope, scope.context, LoadConstraint(
                base=descriptor_var, field=attr(rule.method), target=hook_var,
            ))
            if rule.receiver is None:
                receiver_var = self._make_none_var(solver, scope, rule.call_site)
            else:
                receiver_var = self._make_object_var(
                    solver, scope, rule.receiver, "descriptor_receiver", rule.call_site, token,
                )
            if rule.method == "__get__":
                owner_var = self._make_object_var(
                    solver, scope, rule.owner, "descriptor_owner", rule.call_site, token,
                )
                args = (receiver_var, owner_var)
            else:
                args = (receiver_var, rule.source)
            solver.add_constraint(scope, scope.context, CallConstraint(
                callee=hook_var, args=args, kwargs=frozenset(),
                target=rule.target, call_site=rule.call_site,
            ))

    def _make_none_var(self, solver, scope, call_site):
        name = f"$const_descriptor_none_{call_site.short_id()}"
        var = Variable(name=name, kind=VariableKind.TEMPORARY)
        site = self._const_alloc_sites.get(name)
        if site is None:
            statement = IRAssign(ast.Assign(
                targets=[ast.Name(id=name, ctx=ast.Store())], value=ast.Constant(value=None),
            ))
            site = AllocSite.from_ir_node(statement, AllocKind.CONSTANT)
            self._const_alloc_sites[name] = site
        solver.add_constraint(scope, scope.context, AllocConstraint(target=var, alloc_site=site))
        return var

    @staticmethod
    def _apply_classmethod_binding(solver, scope, rule):
        state = solver.state
        functions = state.get_points_to(state.get_variable(scope, scope.context, rule.function))
        owners = set()
        if len(rule.arguments) > 1:
            owner_pts = state.get_points_to(state.get_variable(scope, scope.context, rule.arguments[1]))
            owners.update(obj for obj in owner_pts if isinstance(obj, ClassObject))
        # __get__(instance) and __get__(instance, None) use type(instance).
        # Joining this alternative is conservative when the optional owner may
        # itself be imprecise, without dropping any explicit owner candidate.
        if rule.arguments:
            instances = state.get_points_to(state.get_variable(scope, scope.context, rule.arguments[0]))
            owners.update(obj.class_obj for obj in instances if isinstance(obj, InstanceObject))
        target = state.get_variable(scope, scope.context, rule.target)
        for function in functions:
            if not isinstance(function, FunctionObject):
                # Wrapped user descriptor/callable support is a separate
                # protocol obligation. Retain the existing may-value fallback.
                solver.handle_new_points_to(target, scope, PointsToSet.singleton(function))
                state._residual_effects[(scope, rule, function)] = {
                    "origin": str(rule.call_site),
                    "reason": "classmethod binding for a non-function wrapped object is unmodeled",
                    "context": str(scope.context),
                    "effects": ["descriptor_binding", "may_invoke_callbacks", "may_raise"],
                }
                continue
            for owner in owners:
                bound = MethodObject(function.context, function.alloc_site,
                                     function.container_scope, function.ir, owner, owner)
                solver.handle_new_points_to(target, scope, PointsToSet.singleton(bound))
    
    @staticmethod
    def _resolve_attr(attr_value: Any) -> Tuple[Field, str]:
        if isinstance(attr_value, Field):
            field = attr_value
        elif isinstance(attr_value, str):
            field = attr(attr_value if attr_value else "<unknown>")
        else:
            field = unknown()
        if field.kind == FieldKind.ATTRIBUTE and field.name:
            return field, field.name
        return field, "<unknown>"
    
    def _make_const_name_var(
        self,
        solver: 'PointerSolver',
        scope: 'Scope',
        name: str,
        call_site,
    ) -> Variable:
        token = self._attr_token(name)
        var_name = f"$const_attr_{call_site.short_id()}_{token}"
        var = Variable(name=var_name, kind=VariableKind.TEMPORARY)
        alloc_site = self._const_alloc_sites.get(var_name)
        if alloc_site is None:
            assign = ast.Assign(
                targets=[ast.Name(id=var_name, ctx=ast.Store())],
                value=ast.Constant(value=name),
            )
            const_assign = IRAssign(assign)
            alloc_site = AllocSite.from_ir_node(const_assign, AllocKind.CONSTANT)
            self._const_alloc_sites[var_name] = alloc_site
        solver.add_constraint(
            scope,
            scope.context,
            AllocConstraint(target=var, alloc_site=alloc_site),
        )
        return var
    
    @staticmethod
    def _attr_token(name: str) -> str:
        return "".join(ch if (ch.isalnum() or ch in "._") else "_" for ch in name)
    
    @staticmethod
    def _make_temp_var(prefix: str, call_site, token: Optional[str] = None) -> Variable:
        suffix = f"@{token}" if token else ""
        return Variable(
            name=f"${prefix}@{call_site.short_id()}{suffix}",
            kind=VariableKind.TEMPORARY,
        )
    
    @staticmethod
    def _make_object_var(
        solver: 'PointerSolver',
        scope: 'Scope',
        obj: 'AbstractObject',
        prefix: str,
        call_site,
        token: str,
    ) -> Variable:
        var = Variable(
            name=f"${prefix}@{call_site.short_id()}@{token}",
            kind=VariableKind.TEMPORARY,
        )
        ctx_var = solver.state.get_variable(scope, scope.context, var)
        solver.handle_new_points_to(ctx_var, scope, PointsToSet.singleton(obj))
        return var
    
    @staticmethod
    def _get_class_field(
        solver: 'PointerSolver',
        scope: 'Scope',
        class_obj: 'ClassObject',
        field: Field,
    ) -> 'Ctx[Any]':
        class_scope = solver.state.get_internal_scope(class_obj)
        if class_scope is None:
            return solver.state.get_field(scope, scope.context, class_obj, field)
        return solver.state.get_field(class_scope, class_scope.context, class_obj, field)
