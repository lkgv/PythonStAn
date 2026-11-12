"""Generator and coroutine call handling processor.

Detects generator functions (containing yield) and async functions,
returning GeneratorObject/CoroutineObject instead of normal call semantics.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING, override

from pythonstan.graph.call_graph.call_edge import CallEdge, CallKind
from pythonstan.ir.ir_statements import IRCall, IRFunc, IRYield

from ..allocation import canonical_constant
from ..constraints import (
    AwaitConstraint,
    CallConstraint,
    LoadConstraint,
    ResidualEffectConstraint,
    StoreConstraint,
)
from ..context import CallSite, Ctx, Scope
from ..heap_model import Field, FieldKind, attr, elem
from ..object import (
    AllocKind,
    AllocSite,
    AsyncGeneratorObject,
    BuiltinFunctionObject,
    BuiltinInstanceObject,
    BuiltinMethodObject,
    CoroutineObject,
    FunctionObject,
    GeneratorObject,
    InstanceObject,
    ListObject,
    MethodObject,
    SetObject,
    TupleObject,
)
from ..points_to_set import PointsToSet
from ..variable import FieldAccess, Variable, VariableKind
from .normal_call import NormalCallProcessor, require_context_selector, require_ir_translator
from .processor import Processor

if TYPE_CHECKING:
    from ..constraints import Constraint
    from ..context import NativeContext
    from ..object import AbstractObject
    from ..solver import PointerSolver

logger = logging.getLogger(__name__)

__all__ = ["GeneratorProcessor"]
_RESUME_TARGET = Field(FieldKind.CLASS_METADATA, name="coroutine.resume_target")
_ASYNC_RESUME_TARGET = Field(FieldKind.CLASS_METADATA, name="async_generator.resume_target")
_ASYNC_SEND_VALUE = Field(FieldKind.CLASS_METADATA, name="async_generator.send_value")
_GENERATOR_COMPLETION = Field(FieldKind.CLASS_METADATA, name="generator.completion_value")
_SUSPENSION = Field(FieldKind.CLASS_METADATA, name="continuation.suspension")


type BodyBinding = tuple[FunctionObject, IRFunc, Scope, NativeContext]


class GeneratorProcessor(Processor):
    """Processor for generator and coroutine function calls.

    Detects when a generator or async function is called and returns
    the appropriate iterator/coroutine object instead of executing
    normal call semantics.
    """

    # Cache for generator function detection
    _generator_funcs: set[IRFunc] = set()
    _checked_funcs: set[IRFunc] = set()

    def __init__(self) -> None:
        self._generator_funcs = set()
        self._checked_funcs = set()
        self._normal_calls = NormalCallProcessor()
        self._bodies: dict[AbstractObject, dict[Scope, BodyBinding]] = {}
        self._resumers: dict[AbstractObject, set[tuple[Scope, CallSite | None]]] = {}
        self._active_scopes: set[Scope] = set()
        self._custom_awaits: set[tuple[Scope, AwaitConstraint, InstanceObject]] = set()
        self._async_resume_steps: dict[tuple[Scope, AwaitConstraint], BuiltinInstanceObject] = {}
        self._async_steps: dict[
            tuple[Scope, CallSite | None, AsyncGeneratorObject], BuiltinInstanceObject
        ] = {}
        self._delegations: set[tuple[Scope, AwaitConstraint, InstanceObject]] = set()

    @override
    def handle_new_constraint(
        self, solver: PointerSolver, scope: Scope, constraint: Constraint
    ) -> bool:
        if not isinstance(constraint, AwaitConstraint):
            return False
        trigger = solver.state.get_variable(scope, scope.context, constraint.awaitable)
        solver._register_dynamic_constraint(scope, trigger, constraint)
        return True

    @override
    def handle_constraint(
        self,
        solver: PointerSolver,
        target: Ctx[Variable | FieldAccess] | None,
        scope: Scope,
        constraint: Constraint,
        points: PointsToSet,
    ) -> bool:
        if not isinstance(constraint, AwaitConstraint):
            return False
        for obj in points:
            if constraint.operation in {"delegate", "delegate_iterator"}:
                self._delegate(solver, scope, constraint, obj)
                continue
            if constraint.operation == "await_complete":
                if isinstance(obj, GeneratorObject):
                    if constraint.sent_value:
                        solver.state._add_var_points_flow(
                            solver.state.get_variable(scope, scope.context, constraint.sent_value),
                            solver.state.get_field(scope, scope.context, obj, attr("$sent")),
                        )
                    self._resume_value(solver, scope, constraint.call_site, obj)
                    if constraint.suspension_target:
                        solver.state._add_var_points_flow(
                            solver.state.get_field(scope, scope.context, obj, elem()),
                            solver.state.get_variable(
                                scope, scope.context, constraint.suspension_target
                            ),
                        )
                    if constraint.target:
                        solver.state._add_var_points_flow(
                            solver.state.get_field(
                                scope, scope.context, obj, _GENERATOR_COMPLETION
                            ),
                            solver.state.get_variable(scope, scope.context, constraint.target),
                        )
                elif (
                    isinstance(obj, BuiltinInstanceObject)
                    and obj.builtin_type == "coroutine_iterator"
                ):
                    self._resume_value(
                        solver, scope, constraint.call_site, obj, await_constraint=constraint
                    )
                elif (
                    isinstance(obj, BuiltinInstanceObject)
                    and obj.builtin_type == "async_generator_asend"
                ):
                    self._resume_value(
                        solver, scope, constraint.call_site, obj, await_constraint=constraint
                    )
                    if constraint.target:
                        solver.state._add_var_points_flow(
                            solver.state.get_field(
                                scope, scope.context, obj, attr("$await_result")
                            ),
                            solver.state.get_variable(scope, scope.context, constraint.target),
                        )
                elif isinstance(obj, InstanceObject) and constraint.call_site is not None:
                    # Preserve protocol effects without mistaking a yielded
                    # value for StopIteration.value on an arbitrary iterator.
                    from .type_protocol import install_instance_method_call

                    install_instance_method_call(
                        solver, scope, obj, "__next__", (), None, constraint.call_site
                    )
                continue
            if constraint.operation in {"aiter", "anext"}:
                if isinstance(obj, AsyncGeneratorObject):
                    if constraint.operation == "aiter" and constraint.target:
                        destination = solver.state.get_variable(
                            scope, scope.context, constraint.target
                        )
                        solver.handle_new_points_to(destination, scope, PointsToSet.singleton(obj))
                    elif constraint.operation == "anext":
                        self._make_async_step(
                            solver, scope, constraint.call_site, obj, constraint.target
                        )
                elif isinstance(obj, InstanceObject) and constraint.call_site is not None:
                    from .type_protocol import install_instance_method_call

                    method = "__aiter__" if constraint.operation == "aiter" else "__anext__"
                    install_instance_method_call(
                        solver, scope, obj, method, (), constraint.target, constraint.call_site
                    )
                continue
            if constraint.operation == "async_resume":
                if isinstance(obj, AsyncGeneratorObject):
                    step = self._async_resume_steps[(scope, constraint)]
                    solver.state._add_var_points_flow(
                        solver.state.get_field(scope, scope.context, obj, elem()),
                        solver.state.get_field(scope, scope.context, step, attr("$await_result")),
                    )
                    solver.state._add_var_points_flow(
                        solver.state.get_field(scope, scope.context, obj, _SUSPENSION),
                        solver.state.get_field(scope, scope.context, step, elem()),
                    )
                    solver.state._add_var_points_flow(
                        solver.state.get_field(scope, scope.context, step, _ASYNC_SEND_VALUE),
                        solver.state.get_field(scope, scope.context, obj, attr("$sent")),
                    )
                    solver.state._add_var_points_flow(
                        solver.state.get_field(scope, scope.context, step, attr("$sent")),
                        solver.state.get_field(scope, scope.context, obj, attr("$sent")),
                    )
                    self._resumers.setdefault(obj, set()).add((scope, constraint.call_site))
                    for binding in tuple(self._bodies.get(obj, {}).values()):
                        self._publish_body(solver, binding, scope, constraint.call_site)
                continue
            if constraint.operation == "iter":
                if constraint.target and (
                    isinstance(obj, GeneratorObject)
                    or (
                        isinstance(obj, BuiltinInstanceObject)
                        and obj.builtin_type == "coroutine_iterator"
                    )
                ):
                    destination = solver.state.get_variable(scope, scope.context, constraint.target)
                    solver.handle_new_points_to(destination, scope, PointsToSet.singleton(obj))
                elif isinstance(obj, InstanceObject) and constraint.call_site is not None:
                    from .type_protocol import install_instance_method_call

                    install_instance_method_call(
                        solver, scope, obj, "__iter__", (), constraint.target, constraint.call_site
                    )
            else:
                is_await = constraint.operation == "await"
                if constraint.operation == "next":
                    self._seed_implicit_send_none(solver, scope, constraint.call_site, obj)
                self._resume_value(
                    solver,
                    scope,
                    constraint.call_site,
                    obj,
                    allow_coroutine=is_await,
                    await_constraint=constraint if is_await else None,
                )
                if (
                    not is_await
                    and isinstance(obj, InstanceObject)
                    and constraint.call_site is not None
                ):
                    from .type_protocol import install_instance_method_call

                    install_instance_method_call(
                        solver, scope, obj, "__next__", (), constraint.target, constraint.call_site
                    )
                if (
                    constraint.operation == "advance"
                    and constraint.target
                    and isinstance(obj, (GeneratorObject, BuiltinInstanceObject))
                ):
                    solver.add_constraint(
                        scope,
                        scope.context,
                        LoadConstraint(constraint.awaitable, elem(), constraint.target),
                    )
        return True

    def _delegate(
        self, solver: PointerSolver, scope: Scope, constraint: AwaitConstraint, obj: AbstractObject
    ) -> None:
        """Keep yielded and completed values separate at an actual delegate.

        Body publication happens only here, after the containing generator was
        resumed. Every relation is a persistent field/variable dependency.
        Custom iterators retain next effects, not an invented terminal value.
        """
        state = solver.state

        def transfer(field: Field, target: Variable | None) -> None:
            if target is not None:
                state._add_var_points_flow(
                    state.get_field(scope, scope.context, obj, field),
                    state.get_variable(scope, scope.context, target),
                )

        if isinstance(obj, GeneratorObject):
            if constraint.sent_value is not None:
                state._add_var_points_flow(
                    state.get_variable(scope, scope.context, constraint.sent_value),
                    state.get_field(scope, scope.context, obj, attr("$sent")),
                )
            self._resume_value(solver, scope, constraint.call_site, obj)
            transfer(elem(), constraint.target)
            transfer(_GENERATOR_COMPLETION, constraint.completion_target)
            return
        if isinstance(obj, BuiltinInstanceObject) and obj.builtin_type in {
            "coroutine_iterator",
            "async_generator_asend",
        }:
            complete = AwaitConstraint(
                constraint.awaitable,
                constraint.completion_target,
                constraint.call_site,
                "await_complete",
                suspension_target=constraint.target,
                sent_value=constraint.sent_value,
            )
            self.handle_constraint(solver, None, scope, complete, PointsToSet.singleton(obj))
            return
        from ..native_mapping import NATIVE_MAPPING_KEYS, exact_native_mapping

        native_mapping = exact_native_mapping(obj)
        if (
            native_mapping
            or isinstance(obj, (ListObject, TupleObject, SetObject))
            or (
                isinstance(obj, BuiltinInstanceObject)
                and obj.builtin_type
                in {"iterator", "enumerate", "zip", "map", "filter", "reversed", "dict_keys"}
            )
        ):
            # Native dict iteration exposes stored key identities, never its
            # value summary. Reading this provenance does not hash keys again.
            transfer(NATIVE_MAPPING_KEYS if native_mapping else elem(), constraint.target)
            if native_mapping:
                solver.add_constraint(
                    scope,
                    scope.context,
                    ResidualEffectConstraint(
                        str(constraint.call_site),
                        "Native dictionary key iteration retains known key provenance; complete key presence, opaque producers, insertion order, resize/removal and exception routing remain unproved",
                        (
                            "native_mapping_key_provenance",
                            "key_presence",
                            "iteration_order",
                            "iteration_mutation",
                            "exception_routing",
                            "may_raise",
                        ),
                    ),
                )
            if constraint.completion_target:
                none = canonical_constant(
                    solver.state,
                    require_context_selector(solver, "empty_context").empty_context(),
                    AllocSite(("native_iteration_completion", obj), AllocKind.CONSTANT),
                    None,
                )
                destination = state.get_variable(scope, scope.context, constraint.completion_target)
                solver.handle_new_points_to(destination, scope, PointsToSet.singleton(none))
            return
        if isinstance(obj, InstanceObject) and constraint.call_site is not None:
            from .type_protocol import install_instance_method_call

            if constraint.operation == "delegate_iterator":
                install_instance_method_call(
                    solver, scope, obj, "__next__", (), constraint.target, constraint.call_site
                )
            else:
                identity = (scope, constraint, obj)
                if identity not in self._delegations:
                    self._delegations.add(identity)
                    iterator = solver.variable_factory.make_variable(
                        f"$delegated_iterator@{id(constraint)}@{id(obj)}", VariableKind.TEMPORARY
                    )
                    install_instance_method_call(
                        solver, scope, obj, "__iter__", (), iterator, constraint.call_site
                    )
                    solver.add_constraint(
                        scope,
                        scope.context,
                        AwaitConstraint(
                            iterator,
                            constraint.target,
                            constraint.call_site,
                            "delegate_iterator",
                            constraint.completion_target,
                            constraint.sent_value,
                        ),
                    )
            solver.add_constraint(
                scope,
                scope.context,
                ResidualEffectConstraint(
                    str(constraint.call_site),
                    "Custom delegation retains next effects; StopIteration.value and send/throw/close routing remain unproved",
                    (
                        "delegation",
                        "iterator_completion",
                        "exception_routing",
                        "cleanup_order",
                        "may_raise",
                    ),
                ),
            )
            return
        solver.add_constraint(
            scope,
            scope.context,
            ResidualEffectConstraint(
                str(constraint.call_site),
                "Delegated object family is not modeled, including opaque native producers without reviewed iteration semantics",
                (
                    "delegation",
                    "unknown_yield",
                    "unknown_completion",
                    "may_invoke_callbacks",
                    "may_mutate_heap",
                    "may_raise",
                ),
            ),
        )

    @staticmethod
    def _may_default_return(statements: Sequence[ast.stmt]) -> bool:
        """Conservative syntactic default-return check; no path proof claim."""
        if not statements:
            return True
        last = statements[-1]
        if isinstance(last, ast.Return):
            return last.value is None
        if isinstance(last, ast.Raise):
            return False
        if isinstance(last, ast.If):
            return GeneratorProcessor._may_default_return(
                last.body
            ) or GeneratorProcessor._may_default_return(last.orelse)
        return True

    def _make_async_step(
        self,
        solver: PointerSolver,
        scope: Scope,
        site: CallSite | None,
        receiver: AsyncGeneratorObject,
        target: Variable | None,
        send_value: Variable | None = None,
    ) -> BuiltinInstanceObject:
        """Create a lazy async-generator advance; retain every later value.

        The wrapper is a finite native allocation at the original operation,
        linked privately to its generator. Publishing it does not resume the
        generator, including when yielded-result subscriptions are installed.
        """
        identity = (scope, site, receiver)
        step = self._async_steps.get(identity)
        if step is None:
            allocation = AllocSite(
                ("async_generator_asend", scope, site, receiver), AllocKind.BUILTIN
            )
            step = BuiltinInstanceObject(
                solver.state.select_allocation_context(scope.context, allocation),
                allocation,
                "async_generator_asend",
            )
            self._async_steps[identity] = step
        state = solver.state
        link = state.get_field(scope, scope.context, step, _ASYNC_RESUME_TARGET)
        solver.handle_new_points_to(link, scope, PointsToSet.singleton(receiver))
        state._add_var_points_flow(
            state.get_field(scope, scope.context, receiver, elem()),
            state.get_field(scope, scope.context, step, attr("$await_result")),
        )
        state._add_var_points_flow(
            state.get_field(scope, scope.context, receiver, _SUSPENSION),
            state.get_field(scope, scope.context, step, elem()),
        )
        sent = state.get_field(scope, scope.context, step, _ASYNC_SEND_VALUE)
        if send_value is not None:
            state._add_var_points_flow(state.get_variable(scope, scope.context, send_value), sent)
        else:
            none = canonical_constant(
                solver.state,
                require_context_selector(solver, "empty_context").empty_context(),
                AllocSite(("async_generator_anext_none", site), AllocKind.CONSTANT),
                None,
            )
            solver.handle_new_points_to(sent, scope, PointsToSet.singleton(none))
        if target:
            destination = state.get_variable(scope, scope.context, target)
            solver.handle_new_points_to(destination, scope, PointsToSet.singleton(step))
        return step

    def _remember_body(
        self,
        solver: PointerSolver,
        obj: AbstractObject,
        function: FunctionObject,
        ir: IRFunc,
        callee_scope: Scope,
        context: NativeContext,
    ) -> None:
        bindings = self._bodies.setdefault(obj, {})
        bindings[callee_scope] = (function, ir, callee_scope, context)
        for caller, site in tuple(self._resumers.get(obj, ())):
            self._publish_body(solver, bindings[callee_scope], caller, site)

    def _publish_body(
        self, solver: PointerSolver, binding: BodyBinding, caller: Scope, site: CallSite | None
    ) -> None:
        function, ir, callee_scope, context = binding
        if callee_scope not in self._active_scopes:
            self._active_scopes.add(callee_scope)
            self._translate_function_body(solver, function, ir, callee_scope, context)
            function_tree = ir.get_ast()
            if (
                isinstance(callee_scope.obj, GeneratorObject)
                and isinstance(function_tree, (ast.FunctionDef, ast.AsyncFunctionDef))
                and self._may_default_return(function_tree.body)
            ):
                none = canonical_constant(
                    solver.state,
                    require_context_selector(solver, "empty_context").empty_context(),
                    AllocSite(
                        ("generator_default_completion", callee_scope.obj), AllocKind.CONSTANT
                    ),
                    None,
                )
                completion = solver.state.get_field(
                    callee_scope, context, callee_scope.obj, _GENERATOR_COMPLETION
                )
                solver.handle_new_points_to(completion, callee_scope, PointsToSet.singleton(none))
            solver.add_constraint(
                callee_scope,
                context,
                ResidualEffectConstraint(
                    ir.get_qualname(),
                    "First-resume body may-summary does not discharge yield stages, exhaustion, throw/close or cancellation",
                    ("suspend_resume", "exception_routing", "cleanup_order", "may_raise"),
                ),
            )
        if site is not None:
            solver.state.call_graph.add_edge(
                CallEdge(
                    kind=CallKind.FUNCTION,
                    callsite=Ctx(caller.context, caller, site),
                    callee=callee_scope,
                )
            )

    def _resume_value(
        self,
        solver: PointerSolver,
        scope: Scope,
        site: CallSite | None,
        obj: AbstractObject,
        *,
        allow_coroutine: bool = False,
        await_constraint: AwaitConstraint | None = None,
    ) -> None:
        if isinstance(obj, BuiltinInstanceObject) and obj.builtin_type == "async_generator_asend":
            if await_constraint is not None and await_constraint.sent_value:
                solver.state._add_var_points_flow(
                    solver.state.get_variable(scope, scope.context, await_constraint.sent_value),
                    solver.state.get_field(scope, scope.context, obj, attr("$sent")),
                )
            if await_constraint is not None and await_constraint.suspension_target:
                solver.state._add_var_points_flow(
                    solver.state.get_field(scope, scope.context, obj, elem()),
                    solver.state.get_variable(
                        scope, scope.context, await_constraint.suspension_target
                    ),
                )
            link = solver.state.get_field(scope, scope.context, obj, _ASYNC_RESUME_TARGET)
            rule = AwaitConstraint(
                solver.variable_factory.make_variable(
                    f"$async_resume@{id(obj)}", VariableKind.TEMPORARY
                ),
                None,
                site,
                "async_resume",
            )
            self._async_resume_steps[(scope, rule)] = obj
            solver._register_dynamic_constraint(scope, link, rule)
            return
        native = isinstance(obj, GeneratorObject) or (
            allow_coroutine and isinstance(obj, CoroutineObject)
        )
        if native:
            if await_constraint is not None and await_constraint.sent_value:
                solver.state._add_var_points_flow(
                    solver.state.get_variable(scope, scope.context, await_constraint.sent_value),
                    solver.state.get_field(scope, scope.context, obj, attr("$sent")),
                )
            self._resumers.setdefault(obj, set()).add((scope, site))
            for binding in tuple(self._bodies.get(obj, {}).values()):
                self._publish_body(solver, binding, scope, site)
            if (
                isinstance(obj, CoroutineObject)
                and await_constraint is not None
                and await_constraint.target
            ):
                solver.state._add_var_points_flow(
                    solver.state.get_field(scope, scope.context, obj, attr("$await_result")),
                    solver.state.get_variable(scope, scope.context, await_constraint.target),
                )
            if (
                isinstance(obj, CoroutineObject)
                and await_constraint is not None
                and await_constraint.suspension_target
            ):
                solver.state._add_var_points_flow(
                    solver.state.get_field(scope, scope.context, obj, _SUSPENSION),
                    solver.state.get_variable(
                        scope, scope.context, await_constraint.suspension_target
                    ),
                )
            return
        if isinstance(obj, BuiltinInstanceObject) and obj.builtin_type == "coroutine_iterator":
            source = solver.state.get_field(scope, scope.context, obj, _RESUME_TARGET)
            # Subscribe to the actual heap relation, including late production.
            rule = AwaitConstraint(
                await_constraint.awaitable
                if await_constraint
                else solver.variable_factory.make_variable(
                    "$resume_target", VariableKind.TEMPORARY
                ),
                await_constraint.target if await_constraint else None,
                site,
                suspension_target=await_constraint.suspension_target if await_constraint else None,
                sent_value=await_constraint.sent_value if await_constraint else None,
            )
            solver._register_dynamic_constraint(scope, source, rule)
            return
        if await_constraint is not None and isinstance(obj, InstanceObject):
            self._install_custom_await(solver, scope, await_constraint, obj)

    def _install_custom_await(
        self,
        solver: PointerSolver,
        scope: Scope,
        constraint: AwaitConstraint,
        receiver: InstanceObject,
    ) -> None:
        identity = (scope, constraint, receiver)
        if identity in self._custom_awaits:
            return
        self._custom_awaits.add(identity)
        if constraint.call_site is None:
            solver.add_constraint(
                scope,
                scope.context,
                ResidualEffectConstraint(
                    scope.stmt.get_qualname(),
                    "Custom await has no original source callsite",
                    ("await_protocol", "may_invoke_callbacks", "may_mutate_heap", "may_raise"),
                ),
            )
            return
        from .type_protocol import install_instance_method_call

        iterator = solver.variable_factory.make_variable(
            f"$await_iterator@{id(constraint)}@{id(receiver)}", VariableKind.TEMPORARY
        )
        # Type-level special lookup must still bind arbitrary descriptors.
        install_instance_method_call(
            solver, scope, receiver, "__await__", (), iterator, constraint.call_site
        )
        # Implicit advancement bypasses a user global named ``next``.
        solver.add_constraint(
            scope,
            scope.context,
            AwaitConstraint(
                iterator,
                constraint.target,
                constraint.call_site,
                operation="await_complete",
                suspension_target=constraint.suspension_target,
                sent_value=constraint.sent_value,
            ),
        )
        solver.add_constraint(
            scope,
            scope.context,
            ResidualEffectConstraint(
                str(constraint.call_site),
                "Custom await native-generator return may-summary leaves stages, exhaustion, arbitrary iterator StopIteration.value, sends and exception routing open",
                ("await_protocol", "resume_order", "exception_routing", "may_raise"),
            ),
        )

    def _is_generator_function(self, func_ir: IRFunc, solver: PointerSolver) -> bool:
        """Check if function is a generator (contains yield statements)."""
        if func_ir in self._checked_funcs:
            return func_ir in self._generator_funcs

        self._checked_funcs.add(func_ir)

        # Get function IR statements
        stmts = solver.state.scope_manager.get_statement_ir(func_ir)
        if stmts is None:
            return False

        # Check for yield statements
        for stmt in stmts:
            if isinstance(stmt, IRYield):
                self._generator_funcs.add(func_ir)
                return True

        return False

    @staticmethod
    def _known_bad_arity(call: CallConstraint, allowed: Collection[int]) -> bool:
        statement = call.call_site.statement
        arguments = statement.get_args() if isinstance(statement, IRCall) else ()
        if any(starred for _, starred in arguments) or any(name == "**" for name, _ in call.kwargs):
            return False  # Expanded argument binding remains an open may-path.
        return len(call.args) not in allowed or bool(call.kwargs)

    @staticmethod
    def _record_bad_arity(solver: PointerSolver, scope: Scope, call: CallConstraint) -> None:
        solver.add_constraint(
            scope,
            scope.context,
            ResidualEffectConstraint(
                str(call.call_site),
                "Native async protocol rejects this explicit argument shape",
                ("argument_binding", "exception_routing", "may_raise"),
            ),
        )

    @staticmethod
    def _seed_implicit_send_none(
        solver: PointerSolver, scope: Scope, site: CallSite | None, receiver: AbstractObject
    ) -> None:
        if not isinstance(receiver, GeneratorObject) and not (
            isinstance(receiver, BuiltinInstanceObject)
            and receiver.builtin_type in {"coroutine_iterator", "async_generator_asend"}
        ):
            return
        none = canonical_constant(
            solver.state,
            require_context_selector(solver, "empty_context").empty_context(),
            AllocSite(("implicit_continuation_send_none", site), AllocKind.CONSTANT),
            None,
        )
        target = solver.state.get_field(scope, scope.context, receiver, attr("$sent"))
        solver.handle_new_points_to(target, scope, PointsToSet.singleton(none))

    @override
    def handle_call(
        self,
        solver: PointerSolver,
        target: Ctx[Variable | FieldAccess],
        scope: Scope,
        constraint: Constraint,
        callee_obj: AbstractObject,
    ) -> bool:
        """Handle calls to generator/async functions.

        Returns True if this is a generator/async call and was handled,
        False to let other processors handle normal function calls.
        """
        if not isinstance(constraint, CallConstraint):
            return False

        call = constraint
        if isinstance(callee_obj, BuiltinFunctionObject) and call.args:
            if callee_obj.function_name in {"aiter", "anext"}:
                allowed = {1} if callee_obj.function_name == "aiter" else {1, 2}
                if self._known_bad_arity(call, allowed):
                    self._record_bad_arity(solver, scope, call)
                    return True
                solver.add_constraint(
                    scope,
                    scope.context,
                    AwaitConstraint(
                        call.args[0],
                        call.target,
                        call.call_site,
                        operation=callee_obj.function_name,
                    ),
                )
                if len(call.args) > 1:
                    solver.add_constraint(
                        scope,
                        scope.context,
                        ResidualEffectConstraint(
                            str(call.call_site),
                            "anext default/exhaustion routing is not discharged",
                            ("async_iteration_dispatch", "exception_routing", "may_raise"),
                        ),
                    )
                return True
            if callee_obj.function_name == "iter" and len(call.args) == 1:
                solver.add_constraint(
                    scope,
                    scope.context,
                    AwaitConstraint(call.args[0], call.target, call.call_site, operation="iter"),
                )
            elif callee_obj.function_name == "next":
                solver.add_constraint(
                    scope,
                    scope.context,
                    AwaitConstraint(call.args[0], call.target, call.call_site, operation="next"),
                )
            # Keep the existing builtin fallback for mixed object families.
            return False
        if isinstance(callee_obj, BuiltinMethodObject):
            receiver, method = callee_obj.receiver, callee_obj.method_name
            if isinstance(receiver, AsyncGeneratorObject):
                if method in {"__aiter__", "__anext__", "asend"} and self._known_bad_arity(
                    call, {1} if method == "asend" else {0}
                ):
                    self._record_bad_arity(solver, scope, call)
                    return True
                if method == "__aiter__":
                    if call.target:
                        target = solver.state.get_variable(scope, scope.context, call.target)
                        solver.handle_new_points_to(target, scope, PointsToSet.singleton(receiver))
                    return True
                if method in {"__anext__", "asend"}:
                    self._make_async_step(
                        solver,
                        scope,
                        call.call_site,
                        receiver,
                        call.target,
                        call.args[0] if method == "asend" and call.args else None,
                    )
                    return True
            if (
                isinstance(receiver, BuiltinInstanceObject)
                and receiver.builtin_type == "async_generator_asend"
            ):
                if method in {
                    "__await__",
                    "__iter__",
                    "__next__",
                    "send",
                } and self._known_bad_arity(call, {1} if method == "send" else {0}):
                    self._record_bad_arity(solver, scope, call)
                    return True
                if method in {"__await__", "__iter__"}:
                    if call.target:
                        target = solver.state.get_variable(scope, scope.context, call.target)
                        solver.handle_new_points_to(target, scope, PointsToSet.singleton(receiver))
                    return True
                if method in {"send", "__next__"}:
                    if method == "send" and callee_obj.receiver_var and call.args:
                        solver.add_constraint(
                            scope,
                            scope.context,
                            StoreConstraint(callee_obj.receiver_var, attr("$sent"), call.args[0]),
                        )
                    if method == "__next__":
                        self._seed_implicit_send_none(solver, scope, call.call_site, receiver)
                    self._resume_value(solver, scope, call.call_site, receiver)
                    if call.target:
                        source = solver.state.get_field(scope, scope.context, receiver, elem())
                        target = solver.state.get_variable(scope, scope.context, call.target)
                        solver.state._add_var_points_flow(source, target)
                    return True
            if isinstance(receiver, CoroutineObject) and method == "send":
                if self._known_bad_arity(call, {1}):
                    self._record_bad_arity(solver, scope, call)
                    return True
                if callee_obj.receiver_var and call.args:
                    solver.add_constraint(
                        scope,
                        scope.context,
                        StoreConstraint(callee_obj.receiver_var, attr("$sent"), call.args[0]),
                    )
                self._resume_value(solver, scope, call.call_site, receiver, allow_coroutine=True)
                if call.target:
                    solver.state._add_var_points_flow(
                        solver.state.get_field(scope, scope.context, receiver, _SUSPENSION),
                        solver.state.get_variable(scope, scope.context, call.target),
                    )
                return True
            if (
                isinstance(receiver, BuiltinInstanceObject)
                and receiver.builtin_type == "coroutine_iterator"
                and method in {"send", "__next__"}
            ):
                if method == "send" and callee_obj.receiver_var and call.args:
                    solver.add_constraint(
                        scope,
                        scope.context,
                        StoreConstraint(callee_obj.receiver_var, attr("$sent"), call.args[0]),
                    )
                if method == "__next__":
                    self._seed_implicit_send_none(solver, scope, call.call_site, receiver)
                self._resume_value(solver, scope, call.call_site, receiver)
                if call.target:
                    solver.state._add_var_points_flow(
                        solver.state.get_field(scope, scope.context, receiver, elem()),
                        solver.state.get_variable(scope, scope.context, call.target),
                    )
                return True
            if isinstance(receiver, CoroutineObject) and method == "__await__":
                handler = solver.builtin_manager.get_handler() if solver.builtin_manager else None
                if handler is None:
                    return False
                generated = handler._handle_coroutine_await(scope, scope.context, call, callee_obj)
                if call.target and callee_obj.receiver_var:
                    generated.append(
                        StoreConstraint(call.target, _RESUME_TARGET, callee_obj.receiver_var)
                    )
                    sent = solver.variable_factory.make_variable(
                        f"$await_sent@{id(call)}", VariableKind.TEMPORARY
                    )
                    generated.extend(
                        (
                            LoadConstraint(call.target, attr("$sent"), sent),
                            StoreConstraint(callee_obj.receiver_var, attr("$sent"), sent),
                        )
                    )
                for rule in generated:
                    solver.add_constraint(scope, scope.context, rule)
                return True
            if method in {"__next__", "send"}:
                if method == "__next__":
                    self._seed_implicit_send_none(solver, scope, call.call_site, receiver)
                self._resume_value(
                    solver, scope, call.call_site, receiver, allow_coroutine=method == "send"
                )
            return False
        func_ir: IRFunc | None = None
        func_obj: FunctionObject | None = None

        # Extract function IR from different callable types
        if isinstance(callee_obj, (FunctionObject, MethodObject)):
            func_obj = callee_obj
            if isinstance(callee_obj.alloc_site.stmt, IRFunc):
                func_ir = callee_obj.alloc_site.stmt

        if func_ir is None or func_obj is None:
            return False

        # Async generators have an iterator protocol, not coroutine.__await__.
        if func_ir.is_async and self._is_generator_function(func_ir, solver):
            return self._handle_generator_call(
                solver, scope, scope.context, call, func_obj, func_ir, async_generator=True
            )

        # Check for async function
        if func_ir.is_async:
            return self._handle_coroutine_call(
                solver, scope, scope.context, call, func_obj, func_ir
            )

        # Check for generator function
        if self._is_generator_function(func_ir, solver):
            return self._handle_generator_call(
                solver, scope, scope.context, call, func_obj, func_ir
            )

        return False

    def _handle_generator_call(
        self,
        solver: PointerSolver,
        scope: Scope,
        context: NativeContext,
        call: CallConstraint,
        func_obj: FunctionObject,
        func_ir: IRFunc,
        async_generator: bool = False,
    ) -> bool:
        """Handle calling a generator function.

        Creates a GeneratorObject and sets up the function body to yield
        values into the generator's elem() field.
        """
        logger.debug(f"Handling generator call: {call.call_site} -> {func_ir.name}")

        # Create generator object allocation
        gen_alloc = AllocSite(
            stmt=call.stmt,
            kind=(AllocKind.ASYNC_GENERATOR if async_generator else AllocKind.GENERATOR),
        )
        allocation_context = context
        if call.semantic_partition is not None:
            from ..protocol_context import apply_native_protocol_partition

            allocation_context = apply_native_protocol_partition(
                allocation_context, call.semantic_partition
            )
        gen_context = solver.state.select_allocation_context(allocation_context, gen_alloc)
        object_type = AsyncGeneratorObject if async_generator else GeneratorObject
        gen_obj = object_type(
            context=gen_context,
            alloc_site=gen_alloc,
            func_obj=func_obj,
            container_scope=func_obj.container_scope,
        )

        # Set up call context for the generator body
        call_context = require_context_selector(solver, "select_call_context").select_call_context(
            call.call_site, context, None, frozenset()
        )
        if call.semantic_partition is not None:
            from ..protocol_context import apply_native_protocol_partition

            call_context = apply_native_protocol_partition(call_context, call.semantic_partition)
        callee_scope = Scope.new(
            gen_obj,
            func_obj.container_scope.module,
            call_context,
            func_ir,
            func_obj.container_scope,
        )

        # Store generator object in heap
        solver.state._heap.set_obj(scope, context, gen_alloc, gen_obj)
        solver.state.obj_scope[gen_obj] = callee_scope

        # Set up $generator variable in callee scope for yield statements
        gen_var = solver.variable_factory.make_variable("$generator", VariableKind.TEMPORARY)
        ctx_gen_var = solver.state.get_variable(callee_scope, call_context, gen_var)
        solver.handle_new_points_to(ctx_gen_var, callee_scope, PointsToSet.singleton(gen_obj))
        if not async_generator:
            # Native generator return is the custom-await completion channel;
            # ordinary iteration continues to observe only elem() yields.
            returned = solver.variable_factory.make_variable("$return", VariableKind.TEMPORARY)
            solver.add_constraint(
                callee_scope,
                call_context,
                StoreConstraint(gen_var, _GENERATOR_COMPLETION, returned),
            )

        # Captures must exist before registering any body constraint: CELL
        # resolution is used to choose persistent subscription targets.
        self._dispatch_closure(solver, func_obj, call_context, scope, callee_scope)

        # Match parameters (generator still receives arguments)
        self._match_parameters_simple(
            solver, func_obj, call, scope, callee_scope, context, call_context
        )
        self._remember_body(solver, gen_obj, func_obj, func_ir, callee_scope, call_context)

        # If there's a call target, point it to the generator object
        if call.target:
            target_var = solver.state.get_variable(scope, context, call.target)
            solver.handle_new_points_to(target_var, scope, PointsToSet.singleton(gen_obj))

        # Add call edge
        call_edge = CallEdge(
            kind=CallKind.FUNCTION,
            callsite=Ctx(context, scope, call.call_site),
            callee=callee_scope,
        )
        if solver.state.call_graph.has_edge(call_edge):
            return True
        solver.state.call_graph.add_edge(call_edge)

        return True

    def _handle_coroutine_call(
        self,
        solver: PointerSolver,
        scope: Scope,
        context: NativeContext,
        call: CallConstraint,
        func_obj: FunctionObject,
        func_ir: IRFunc,
    ) -> bool:
        """Handle calling an async function.

        Creates a CoroutineObject and sets up the function body to store
        its return value into the coroutine's $await_result field.
        """
        logger.debug(f"Handling coroutine call: {call.call_site} -> {func_ir.name}")

        # Create coroutine object allocation
        coro_alloc = AllocSite(stmt=call.stmt, kind=AllocKind.COROUTINE)
        allocation_context = context
        if call.semantic_partition is not None:
            from ..protocol_context import apply_native_protocol_partition

            allocation_context = apply_native_protocol_partition(
                allocation_context, call.semantic_partition
            )
        coro_context = solver.state.select_allocation_context(allocation_context, coro_alloc)
        coro_obj = CoroutineObject(
            context=coro_context,
            alloc_site=coro_alloc,
            func_obj=func_obj,
            container_scope=func_obj.container_scope,
        )

        # Set up call context for the coroutine body
        call_context = require_context_selector(solver, "select_call_context").select_call_context(
            call.call_site, context, None, frozenset()
        )
        if call.semantic_partition is not None:
            from ..protocol_context import apply_native_protocol_partition

            call_context = apply_native_protocol_partition(call_context, call.semantic_partition)
        callee_scope = Scope.new(
            coro_obj,
            func_obj.container_scope.module,
            call_context,
            func_ir,
            func_obj.container_scope,
        )

        # Store coroutine object in heap
        solver.state._heap.set_obj(scope, context, coro_alloc, coro_obj)
        solver.state.obj_scope[coro_obj] = callee_scope

        # Set up $coroutine variable in callee scope for return statements
        coro_var = solver.variable_factory.make_variable("$coroutine", VariableKind.TEMPORARY)
        ctx_coro_var = solver.state.get_variable(callee_scope, call_context, coro_var)
        solver.handle_new_points_to(ctx_coro_var, callee_scope, PointsToSet.singleton(coro_obj))

        # Captures must exist before registering any body constraint: CELL
        # resolution is used to choose persistent subscription targets.
        self._dispatch_closure(solver, func_obj, call_context, scope, callee_scope)

        # Match parameters
        self._match_parameters_simple(
            solver, func_obj, call, scope, callee_scope, context, call_context
        )
        self._remember_body(solver, coro_obj, func_obj, func_ir, callee_scope, call_context)

        # If there's a call target, point it to the coroutine object
        if call.target:
            target_var = solver.state.get_variable(scope, context, call.target)
            solver.handle_new_points_to(target_var, scope, PointsToSet.singleton(coro_obj))

        # Add call edge
        call_edge = CallEdge(
            kind=CallKind.FUNCTION,
            callsite=Ctx(context, scope, call.call_site),
            callee=callee_scope,
        )
        if solver.state.call_graph.has_edge(call_edge):
            return True
        solver.state.call_graph.add_edge(call_edge)

        return True

    def _translate_function_body(
        self,
        solver: PointerSolver,
        func_obj: FunctionObject,
        func_ir: IRFunc,
        callee_scope: Scope,
        call_context: NativeContext,
    ) -> None:
        """Translate function body and add constraints."""
        translator = require_ir_translator(solver)
        old_scope = translator._current_scope
        translator._current_scope = func_ir

        try:
            body_constraints = translator.translate_function(func_ir)
        except Exception as e:
            logger.warning(f"Error translating generator/coroutine body: {e}")
            body_constraints = [
                ResidualEffectConstraint(
                    location=func_ir.get_qualname(),
                    reason=f"generator/coroutine body translation failed: {type(e).__name__}: {e}",
                    effects=(
                        "may_invoke_callbacks",
                        "may_mutate_heap",
                        "may_raise",
                        "unknown_return",
                    ),
                )
            ]
        finally:
            translator._current_scope = old_scope

        for constraint in body_constraints:
            solver.add_constraint(callee_scope, call_context, constraint)

    def _dispatch_closure(
        self,
        solver: PointerSolver,
        callee_obj: FunctionObject,
        call_context: NativeContext,
        scope: Scope,
        callee_scope: Scope,
    ) -> None:
        """Use the same lexical captures as an ordinary call."""
        self._normal_calls._dispatch_closure(solver, callee_obj, call_context, scope, callee_scope)

    def _match_parameters_simple(
        self,
        solver: PointerSolver,
        callee_obj: FunctionObject,
        call: CallConstraint,
        scope: Scope,
        callee_scope: Scope,
        context: NativeContext,
        call_context: NativeContext,
    ) -> None:
        """Preserve bound receivers, defaults, and variadic argument binding."""
        receiver = None
        if isinstance(callee_obj, MethodObject) and not callee_obj.ir.is_static_method:
            receiver = (
                callee_obj.class_obj if callee_obj.ir.is_class_method else callee_obj.instance_obj
            )
        self_var = None
        if receiver is not None:
            variable = solver.variable_factory.make_variable(
                f"$async_self@{call.call_site.short_id()}",
                VariableKind.TEMPORARY,
            )
            self_var = solver.state.get_variable(scope, context, variable)
            solver.handle_new_points_to(self_var, scope, PointsToSet.singleton(receiver))
        self._normal_calls._match_parameters(
            solver,
            callee_obj,
            call,
            scope,
            callee_scope,
            context,
            call_context,
            self_var=self_var,
        )
