from __future__ import annotations

import ast
import logging
from typing import TYPE_CHECKING, Literal, override

from pythonstan.frontend.source import SourceLiteral, make_source_constant, source_constant
from pythonstan.graph.call_graph.call_edge import CallEdge, CallKind
from pythonstan.ir.ir_statements import IRAssign, IRFunc

from .. import debug_boundary
from ..allocation import canonical_constant
from ..constraints import (
    AllocConstraint,
    CallConstraint,
    LoadConstraint,
    MappingCopyConstraint,
    ResidualEffectConstraint,
)
from ..context import Ctx, NativeContext, Scope, ordered_binding_token
from ..context_selector import ContextSelector
from ..heap_model import attr, elem, key
from ..native_mapping import NATIVE_MAPPING_KEYS
from ..object import (
    AllocKind,
    AllocSite,
    BuiltinClassObject,
    BuiltinFunctionObject,
    BuiltinInstanceObject,
    BuiltinMethodObject,
    BuiltinObject,
    ClassObject,
    DictObject,
    FunctionObject,
    InstanceObject,
    MethodObject,
    TupleObject,
)
from ..points_to_set import PointsToSet
from ..unknown_tracker import UnknownKind
from ..variable import FieldAccess, Variable, VariableKind
from .processor import Processor

if TYPE_CHECKING:
    from ..ir_translator import IRTranslator
    from ..object import AbstractObject
    from ..solver import PointerSolver

logger = logging.getLogger(__name__)

__all__ = ["NormalCallProcessor"]


def require_context_selector(
    solver: PointerSolver, method: Literal["empty_context", "select_call_context"]
) -> ContextSelector:
    """Read the actual optional selector at the point an operation requires it."""
    selector = solver.context_selector
    if selector is None:
        raise AttributeError(f"'NoneType' object has no attribute '{method}'")
    return selector


def require_ir_translator(solver: PointerSolver) -> IRTranslator:
    """Read the installed translator without inventing a source provider."""
    translator = solver.ir_translator
    if translator is None:
        raise AttributeError("'NoneType' object has no attribute '_current_scope'")
    return translator


class NormalCallProcessor(Processor):
    def __init__(self) -> None:
        self._default_alloc_sites: dict[tuple[IRFunc, str, int, AllocKind], AllocSite] = {}
        self._registered_bodies: set[Scope] = set()

    def _connect_call_values(
        self,
        solver: PointerSolver,
        function: FunctionObject,
        call: CallConstraint,
        caller_scope: Scope,
        callee_scope: Scope,
        context: NativeContext,
        call_context: NativeContext,
        receiver: Ctx[Variable] | None = None,
    ) -> None:
        """Each invocation has its own value slots, even for an existing edge."""
        self._dispatch_closure(solver, function, call_context, caller_scope, callee_scope)
        self._match_parameters(
            solver, function, call, caller_scope, callee_scope, context, call_context, receiver
        )
        if call.target:
            ret = solver.variable_factory.make_variable("$return", VariableKind.TEMPORARY)
            ret_var = solver.state.get_variable(callee_scope, call_context, ret)
            target = solver.state.get_variable(caller_scope, context, call.target)
            solver.state._add_var_points_flow(ret_var, target)

    def _register_body(
        self,
        solver: PointerSolver,
        function: FunctionObject,
        call: CallConstraint,
        callee_scope: Scope,
        call_context: NativeContext,
    ) -> None:
        if callee_scope in self._registered_bodies:
            return
        self._registered_bodies.add(callee_scope)
        translator = require_ir_translator(solver)
        old_scope = translator._current_scope
        translator._current_scope = function.ir
        try:
            constraints = translator.translate_function(function.ir)
        except Exception as exc:
            # Translation is deterministic for this frozen IR, so keep the
            # attempted-body memoization and an unconditional open obligation.
            # Optional diagnostic suppression cannot turn failure into coverage.
            solver.state._residual_effects[(callee_scope, "function_translation")] = {
                "origin": function.ir.get_qualname(),
                "reason": f"function translation failed: {type(exc).__name__}: {exc}",
                "context": str(call_context),
                "effects": [
                    "untranslated_source",
                    "may_mutate_heap",
                    "may_invoke_callbacks",
                    "may_raise",
                ],
            }
            solver._unknown_tracker.record(
                UnknownKind.TRANSLATION_ERROR,
                str(call.call_site),
                f"Error translating function body: {exc}",
                context=function.ir.get_qualname(),
            )
            constraints = []
        finally:
            translator._current_scope = old_scope
        for constraint in constraints:
            solver.add_constraint(callee_scope, call_context, constraint)

    @override
    def handle_call(
        self,
        solver: PointerSolver,
        target: Ctx[Variable | FieldAccess],
        scope: Scope,
        constraint: CallConstraint,
        callee_obj: AbstractObject,
    ) -> bool:
        if isinstance(callee_obj, MethodObject):
            return self._handle_method_call(solver, scope, scope.context, constraint, callee_obj)
        if isinstance(callee_obj, FunctionObject):
            return self._handle_function_call(solver, scope, scope.context, constraint, callee_obj)
        if (
            isinstance(
                callee_obj,
                (
                    BuiltinObject,
                    BuiltinFunctionObject,
                    BuiltinMethodObject,
                    BuiltinClassObject,
                    BuiltinInstanceObject,
                ),
            )
            or callee_obj.kind == AllocKind.BUILTIN
        ):
            return self._handle_builtin_call(solver, scope, scope.context, constraint, callee_obj)
        if isinstance(callee_obj, ClassObject):
            return self._handle_class_call(solver, scope, scope.context, constraint, callee_obj)
        if isinstance(callee_obj, InstanceObject):
            return self._handle_object_call(solver, scope, scope.context, constraint, callee_obj)
        return self._handle_object_call(solver, scope, scope.context, constraint, callee_obj)

    def _handle_builtin_call(
        self,
        solver: PointerSolver,
        scope: Scope,
        context: NativeContext,
        call: CallConstraint,
        builtin_obj: AbstractObject,
    ) -> bool:
        if isinstance(builtin_obj, BuiltinInstanceObject):
            if builtin_obj.builtin_type == "classmethod":
                # A raw classmethod is a descriptor, not an arbitrary callable.
                # Ordinary class lookup also contributes its bound-method path.
                callee = solver.state.get_variable(scope, context, call.callee)
                solver._record_non_callable_evidence(scope, callee, call, builtin_obj)
                solver.add_constraint(
                    scope,
                    context,
                    ResidualEffectConstraint(
                        str(call.call_site),
                        "classmethod object is not callable; TypeError remains an exceptional exit",
                        ("may_raise",),
                    ),
                )
                return True
            if builtin_obj.builtin_type == "staticmethod":
                # In the Python 3.12 profile a raw staticmethod is callable.
                # Persistent constraints preserve targets discovered later.
                wrapped = solver.variable_factory.make_variable(
                    f"$staticmethod_call@{call.call_site.site_id}", VariableKind.TEMPORARY
                )
                solver.add_constraint(
                    scope, context, LoadConstraint(call.callee, attr("func"), wrapped)
                )
                solver.add_constraint(
                    scope,
                    context,
                    CallConstraint(
                        wrapped,
                        call.args,
                        call.kwargs,
                        call.target,
                        call.call_site,
                        semantic_partition=call.semantic_partition,
                    ),
                )
                return True
        solver._register_builtin_dependencies(scope, context, call, builtin_obj)
        ai_handled = solver._try_ai_intrinsic(scope, context, call, builtin_obj)
        # AI contributes may-facts; the persistent builtin constraints remain
        # responsible for facts discovered after this snapshot.
        builtin_handled = solver._handle_builtin_call(scope, context, call, builtin_obj)
        return ai_handled or builtin_handled

    def _handle_class_call(
        self,
        solver: PointerSolver,
        scope: Scope,
        context: NativeContext,
        call: CallConstraint,
        class_obj: ClassObject,
    ) -> bool:
        from .class_protocol import install_metaclass_call

        install_metaclass_call(solver, scope, context, call, class_obj)
        return solver._handle_class_instantiation(scope, context, call, class_obj)

    def _handle_object_call(
        self,
        solver: PointerSolver,
        scope: Scope,
        context: NativeContext,
        call: CallConstraint,
        callee_obj: AbstractObject,
    ) -> bool:
        if call.callee.name.startswith("$call@"):
            return False
        call_var = solver.variable_factory.make_variable(f"$call@{call.call_site.short_id()}")
        ctx_call_var = solver.state.get_variable(scope, context, call_var)
        call_field = solver.state.get_field(scope, context, callee_obj, attr("__call__"))
        solver.state._add_var_points_flow(call_field, ctx_call_var)
        solver.add_constraint(
            scope,
            context,
            CallConstraint(
                callee=call_var,
                args=call.args,
                kwargs=call.kwargs,
                target=call.target,
                call_site=call.call_site,
                semantic_partition=call.semantic_partition,
            ),
        )
        return True

    def _make_default_alloc_site(
        self,
        func_ir: IRFunc,
        param_name: str,
        default_index: int,
        kind: AllocKind,
        value: SourceLiteral,
    ) -> AllocSite:
        key = (func_ir, param_name, default_index, kind)
        alloc_site = self._default_alloc_sites.get(key)
        if alloc_site is None:
            default_var_name = self._default_var_name(func_ir, param_name, default_index, kind)
            const_assign = IRAssign(
                ast.Assign(
                    targets=[ast.Name(id=default_var_name, ctx=ast.Store())],
                    value=make_source_constant(value),
                )
            )
            alloc_site = AllocSite.from_ir_node(const_assign, kind)
            self._default_alloc_sites[key] = alloc_site
        return alloc_site

    @staticmethod
    def _default_var_name(
        func_ir: IRFunc, param_name: str, default_index: int, kind: AllocKind
    ) -> str:
        return f"$default_{kind.value}_{id(func_ir)}_{param_name}_{default_index}"

    def _materialize_default(
        self,
        solver: PointerSolver,
        func_ir: IRFunc,
        def_scope: Scope,
        def_context: NativeContext,
        param_name: str,
        default_index: int,
        default_expr: ast.expr,
    ) -> Ctx[Variable]:
        default_var_name = self._default_var_name(
            func_ir, param_name, default_index, AllocKind.CONSTANT
        )
        default_var = solver.variable_factory.make_variable(
            default_var_name, VariableKind.TEMPORARY
        )

        def defining_variable(name: str) -> Variable:
            # Defaults evaluate in the definition environment. TAC constants
            # are module bindings even when the definition is nested.
            if name.startswith("$const"):
                kind = VariableKind.GLOBAL
            elif name.startswith("$"):
                kind = VariableKind.TEMPORARY
            else:
                kind = solver._resolve_outer_var_kind(def_scope, name)
            return solver.variable_factory.make_variable(name, kind)

        default_ctx_var = solver.state.get_variable(def_scope, def_context, default_var)

        if isinstance(default_expr, ast.Constant):
            alloc_site = self._make_default_alloc_site(
                func_ir=func_ir,
                param_name=param_name,
                default_index=default_index,
                kind=AllocKind.CONSTANT,
                value=source_constant(default_expr),
            )
            solver.add_constraint(
                def_scope,
                def_context,
                AllocConstraint(target=default_var, alloc_site=alloc_site),
            )
            return default_ctx_var

        if isinstance(default_expr, ast.Name):
            source_var = defining_variable(default_expr.id)
            source_ctx = solver.state.get_variable(def_scope, def_context, source_var)
            solver.state._add_var_points_flow(source_ctx, default_ctx_var)
            return default_ctx_var

        if isinstance(default_expr, ast.Attribute) and isinstance(default_expr.value, ast.Name):
            base_var = defining_variable(default_expr.value.id)
            solver.add_constraint(
                def_scope,
                def_context,
                LoadConstraint(
                    base=base_var,
                    field=attr(default_expr.attr),
                    target=default_var,
                ),
            )
            return default_ctx_var

        alloc_site = self._make_default_alloc_site(
            func_ir=func_ir,
            param_name=param_name,
            default_index=default_index,
            kind=AllocKind.UNKNOWN,
            value=None,
        )
        solver.add_constraint(
            def_scope,
            def_context,
            AllocConstraint(target=default_var, alloc_site=alloc_site),
        )
        return default_ctx_var

    def _handle_method_call(
        self,
        solver: PointerSolver,
        scope: Scope,
        context: NativeContext,
        call: CallConstraint,
        method_obj: AbstractObject,
    ) -> bool:
        # logger.info(f"Handling method call: {call.call_site} -> {method_obj.alloc_site.stmt.get_qualname()}")

        if not isinstance(method_obj, MethodObject):
            logger.info(f"is not method object, {type(method_obj)} got!")
            return False

        func_ir: IRFunc = method_obj.alloc_site.require_function_source()
        assert isinstance(func_ir, IRFunc), (
            f"MethodObject alloc site stmt should be IRFunc, {type(func_ir)} got!"
        )
        _func_name = func_ir.get_qualname()

        if func_ir.is_static_method:
            return self._handle_function_call(solver, scope, context, call, method_obj)

        holder_obj: ClassObject | InstanceObject | None
        if func_ir.is_class_method:
            holder_obj = method_obj.class_obj
        else:
            holder_obj = method_obj.instance_obj
            if holder_obj is None:
                return self._handle_function_call(solver, scope, context, call, method_obj)

        if not holder_obj:
            logger.info(f"No holder got in {method_obj}")
            return False

        self_var = solver.state.get_variable(
            scope,
            context,
            solver.variable_factory.make_variable(
                f"$self@{call.call_site.short_id()}@{id(call.call_site.statement)}@{id(holder_obj)}"
            ),
        )
        solver.handle_new_points_to(self_var, scope, PointsToSet.singleton(holder_obj))

        args = [solver.state.get_variable(scope, context, arg) for arg in call.args]
        args.insert(0, self_var)
        kwargs = {k: solver.state.get_variable(scope, context, arg) for k, arg in call.kwargs}

        selected_policy = require_context_selector(solver, "select_call_context").for_method(
            method_obj.container_scope.module.stmt.get_qualname(), func_ir.get_qualname()
        )
        call_context = selected_policy.select_call_context(
            call.call_site, context, holder_obj, params=ordered_binding_token(args, kwargs)
        )
        if call.semantic_partition is not None:
            from ..protocol_context import apply_native_protocol_partition

            call_context = apply_native_protocol_partition(call_context, call.semantic_partition)

        logger.debug(f"Handling function call: {call.call_site} -> {method_obj.alloc_site.stmt}")

        method_scope = solver.state.get_internal_scope(holder_obj)
        if method_scope is None:
            raise AttributeError("'NoneType' object has no attribute 'module'")
        callee_scope = Scope.new(
            method_obj, method_scope.module, call_context, func_ir, method_scope
        )
        assert holder_obj

        call_kind = CallKind.CLASS if func_ir.is_class_method else CallKind.INSTANCE
        call_edge = CallEdge(
            kind=call_kind, callsite=Ctx(context, scope, call.call_site), callee=callee_scope
        )
        self._connect_call_values(
            solver, method_obj, call, scope, callee_scope, context, call_context, self_var
        )
        if solver.state.call_graph.has_edge(call_edge):
            return True
        self._register_body(solver, method_obj, call, callee_scope, call_context)
        solver.state.call_graph.add_edge(call_edge)
        logger.debug(f"Adding call edge: {call_edge}")

        # Debug monitoring: record call edge creation
        if solver._debug_monitor and debug_boundary.feature(solver._debug_monitor, "enabled"):
            caller_name = str(
                scope.stmt.get_qualname() if hasattr(scope.stmt, "get_qualname") else scope.stmt
            )
            callee_name = str(
                call_edge.callee.stmt.get_qualname()
                if hasattr(call_edge.callee.stmt, "get_qualname")
                else call_edge.callee.stmt
            )
            debug_boundary.record_call_edge_created(
                solver._debug_monitor,
                caller=caller_name,
                callee=callee_name,
                call_site=str(call.call_site),
                callee_type="method",
            )

        return True

    def _handle_function_call(
        self,
        solver: PointerSolver,
        scope: Scope,
        context: NativeContext,
        call: CallConstraint,
        func_obj: AbstractObject,
    ) -> bool:
        """Handle function call: analyze function body with parameter bindings.
        1. Selects calling context
        2. Translates function body to constraints
        3. Generates parameter passing constraints
        4. Connects return value to caller
        5. Adds call edge to call graph
        """
        # logger.info(f"Handling function call: {call.call_site} -> {func_obj.alloc_site.stmt}")

        if not isinstance(func_obj, FunctionObject):
            logger.info(f"is not function object, {type(func_obj)} got!")
            return False

        func_ir: IRFunc = func_obj.alloc_site.require_function_source()
        _func_name = func_ir.get_name()
        _alloc_site = func_obj.alloc_site
        # logger.info(f"Handling function call: {call.call_site} -> {alloc_site}")
        args = [solver.state.get_variable(scope, context, arg) for arg in call.args]
        kwargs = {k: solver.state.get_variable(scope, context, arg) for k, arg in call.kwargs}

        selected_policy = require_context_selector(solver, "select_call_context").for_method(
            func_obj.container_scope.module.stmt.get_qualname(), func_ir.get_qualname()
        )
        call_context = selected_policy.select_call_context(
            call.call_site,
            context,
            None,  # No receiver ffor regular functions
            params=ordered_binding_token(args, kwargs),
        )
        if call.semantic_partition is not None:
            from ..protocol_context import apply_native_protocol_partition

            call_context = apply_native_protocol_partition(call_context, call.semantic_partition)

        logger.debug(f"Handling function call: {call.call_site} -> {func_obj.alloc_site.stmt}")

        # func_ir = self.function_registry[func_name]
        # method_scope = solver.state.obj_scope[func_obj]
        _alloc_site = func_obj.alloc_site
        # callee_scope = Scope.new(func_obj, method_scope.module, call_context, func_ir, method_scope)
        defining_scope = func_obj.container_scope
        callee_scope = Scope.new(
            func_obj,
            defining_scope.module,
            call_context,
            func_ir,
            defining_scope,
        )
        call_kind = CallKind.STATIC if func_ir.is_static_method else CallKind.FUNCTION
        call_edge = CallEdge(
            kind=call_kind, callsite=Ctx(context, scope, call.call_site), callee=callee_scope
        )
        self._connect_call_values(
            solver, func_obj, call, scope, callee_scope, context, call_context
        )
        if solver.state.call_graph.has_edge(call_edge):
            return True
        self._register_body(solver, func_obj, call, callee_scope, call_context)
        solver.state.call_graph.add_edge(call_edge)
        logger.debug(f"Adding call edge: {call_edge}")

        # Debug monitoring: record call edge creation
        if solver._debug_monitor and debug_boundary.feature(solver._debug_monitor, "enabled"):
            caller_name = str(
                scope.stmt.get_qualname() if hasattr(scope.stmt, "get_qualname") else scope.stmt
            )
            callee_name = str(
                call_edge.callee.stmt.get_qualname()
                if hasattr(call_edge.callee.stmt, "get_qualname")
                else call_edge.callee.stmt
            )
            debug_boundary.record_call_edge_created(
                solver._debug_monitor,
                caller=caller_name,
                callee=callee_name,
                call_site=str(call.call_site),
                callee_type="function",
            )

        return True

    def _dispatch_closure(
        self,
        solver: PointerSolver,
        callee_obj: FunctionObject,
        call_context: NativeContext,
        scope: Scope,
        callee_scope: Scope,
    ) -> None:
        """Dispatch closure: dispatch closure variables to the callee."""
        state = solver.state

        if isinstance(callee_obj, MethodObject):
            # Binding a function through an instance changes its receiver, not
            # the lexical cells captured when the function was allocated.
            if callee_obj.kind == AllocKind.FUNCTION:
                lexical_function = FunctionObject(
                    callee_obj.context,
                    callee_obj.alloc_site,
                    callee_obj.container_scope,
                    callee_obj.ir,
                )
            else:
                lexical_class = callee_obj.container_scope.obj
                if not isinstance(lexical_class, ClassObject):
                    raise TypeError("A lexical method definition must have a class owner")
                lexical_function = MethodObject(
                    callee_obj.context,
                    callee_obj.alloc_site,
                    callee_obj.container_scope,
                    callee_obj.ir,
                    lexical_class,
                    None,
                )
            state.set_cell_vars(callee_obj, state.get_cell_vars(lexical_function))
            state.set_nonlocal_vars(callee_obj, state.get_nonlocal_vars(lexical_function))
            state.set_global_vars(callee_obj, state.get_global_vars(lexical_function))

        cell_vars = state.get_cell_vars(callee_obj)
        for name, var in cell_vars.items():
            target_var = state.get_variable(
                callee_scope, call_context, solver.variable_factory.make_variable(name)
            )
            state._add_var_points_flow(var, target_var)

        nonlocal_vars = state.get_nonlocal_vars(callee_obj)
        for _name, var in nonlocal_vars.items():
            state.set_variable(callee_scope, call_context, var.content, var)

        global_vars = state.get_global_vars(callee_obj)
        for _name, var in global_vars.items():
            state.set_variable(callee_scope, call_context, var.content, var)

    def _match_parameters(
        self,
        solver: PointerSolver,
        callee_obj: FunctionObject,
        call: CallConstraint,
        scope: Scope,
        callee_scope: Scope,
        context: NativeContext,
        call_context: NativeContext,
        self_var: Ctx[Variable] | None = None,
    ) -> None:
        """Match parameters: match arguments to parameters."""
        state = solver.state

        args = [state.get_variable(scope, context, arg) for arg in call.args]
        if self_var is not None:
            args.insert(0, self_var)
        expanded = [arg for name, arg in call.kwargs if name == "**"]
        kwargs = {k: state.get_variable(scope, context, arg) for k, arg in call.kwargs if k != "**"}
        if expanded:
            from .keyword_expansion import install_keyword_expansion

            expanded = [
                install_keyword_expansion(solver, scope, mapping, call.call_site)
                for mapping in expanded
            ]
            state._residual_effects[(scope, call.call_site, "keyword_expansion")] = {
                "origin": str(call.call_site),
                "context": str(context),
                "reason": "Keyword may-flows do not prove key presence, duplicate rejection, key validity or complete mapping protocol semantics",
                "effects": [
                    "argument_presence",
                    "argument_collision",
                    "mapping_protocol",
                    "may_raise",
                ],
            }

        method_obj = callee_obj
        func_ir: IRFunc = method_obj.alloc_site.require_function_source()
        func_name = func_ir.get_qualname()

        kwarg_parameter: Ctx[Variable] | None = None
        kwarg_dict_obj: DictObject | None = None
        if hasattr(func_ir, "args"):
            func_args = func_ir.args
            diagnostics = solver._diagnostics
            observe_collector = (
                diagnostics is not None
                and func_args.kwarg is not None
                and diagnostics.selected(scope, call, "keyword_collector")
            )
            if observe_collector and diagnostics is not None:
                diagnostics.record(
                    "model_enter",
                    scope,
                    call,
                    "keyword_collector",
                    refs={
                        "callee_scope": callee_scope,
                        "callee_object": callee_obj,
                        "call_context": call_context,
                    },
                    fields={"stage": "parameter_matching"},
                )
            arg_vars = args  # Positional argument variables from call site
            kwarg_vars = (
                kwargs.copy()
            )  # Keyword argument variables from call site (dict: name -> Variable)
            # Named formals subscribe to mapping fields even before the mapping
            # object or its values exist. A literal '**' is never a parameter.
            for param in (*func_args.args, *func_args.kwonlyargs):
                for index, mapping in enumerate(expanded):
                    temporary = solver.variable_factory.make_variable(
                        f"$kwexpand:{call.call_site}:{index}:{param.arg}", VariableKind.TEMPORARY
                    )
                    solver.add_constraint(
                        scope, context, LoadConstraint(mapping, key(param.arg), temporary)
                    )
                    source = state.get_variable(scope, context, temporary)
                    target = state.get_variable(
                        callee_scope, call_context, solver.variable_factory.make_variable(param.arg)
                    )
                    state._add_var_points_flow(source, target)

            positional_params = []
            if hasattr(func_args, "posonlyargs") and func_args.posonlyargs:
                positional_params.extend(func_args.posonlyargs)
            if func_args.args:
                positional_params.extend(func_args.args)

            positional_defaults = {}
            if func_args.defaults:
                first_default_idx = len(positional_params) - len(func_args.defaults)
                for idx, default_expr in enumerate(func_args.defaults):
                    param = positional_params[first_default_idx + idx]
                    positional_defaults[param.arg] = (default_expr, first_default_idx + idx)

            # Track which parameters have been bound
            arg_index = 0
            consumed_kwargs = set()  # Track which keyword arguments have been matched

            # 1. Handle positional-only parameters (Python 3.8+)
            # These can ONLY be filled by positional arguments, not keywords
            if hasattr(func_args, "posonlyargs") and func_args.posonlyargs:
                for param in func_args.posonlyargs:
                    param_name = param.arg
                    param_var = state.get_variable(
                        callee_scope,
                        call_context,
                        solver.variable_factory.make_variable(param_name),
                    )

                    if arg_index < len(arg_vars):
                        # Bind positional argument to parameter
                        state._add_var_points_flow(arg_vars[arg_index], param_var)
                        arg_index += 1
                    else:
                        default_entry = positional_defaults.get(param_name)
                        if default_entry is not None:
                            default_expr, default_idx = default_entry
                            def_scope = callee_obj.container_scope
                            def_context = def_scope.context
                            default_ctx = self._materialize_default(
                                solver=solver,
                                func_ir=func_ir,
                                def_scope=def_scope,
                                def_context=def_context,
                                param_name=param_name,
                                default_index=default_idx,
                                default_expr=default_expr,
                            )
                            state._add_var_points_flow(default_ctx, param_var)
                        else:
                            solver._unknown_tracker.record(
                                UnknownKind.MISSING_ARGUMENT,
                                str(call.call_site),
                                f"Required positional-only parameter {param_name} not provided",
                                context=func_name,
                            )

            # 2. Handle regular positional/keyword parameters
            # These can be filled by either positional OR keyword arguments
            if func_args.args:
                for _param_idx, param in enumerate(func_args.args):
                    param_name = param.arg
                    param_var = state.get_variable(
                        callee_scope,
                        call_context,
                        solver.variable_factory.make_variable(param_name),
                    )

                    # First check if this parameter is provided as a keyword argument
                    if param_name in kwarg_vars:
                        # Bind keyword argument to parameter
                        state._add_var_points_flow(kwarg_vars[param_name], param_var)
                        consumed_kwargs.add(param_name)
                    elif arg_index < len(arg_vars):
                        # Bind positional argument to parameter
                        state._add_var_points_flow(arg_vars[arg_index], param_var)
                        arg_index += 1
                    else:
                        default_entry = positional_defaults.get(param_name)
                        if default_entry is not None:
                            default_expr, default_idx = default_entry
                            def_scope = callee_obj.container_scope
                            def_context = def_scope.context
                            default_ctx = self._materialize_default(
                                solver=solver,
                                func_ir=func_ir,
                                def_scope=def_scope,
                                def_context=def_context,
                                param_name=param_name,
                                default_index=default_idx,
                                default_expr=default_expr,
                            )
                            state._add_var_points_flow(default_ctx, param_var)
                        elif not expanded:
                            # Without ** operands, the required argument is absent.
                            solver._unknown_tracker.record(
                                UnknownKind.MISSING_ARGUMENT,
                                str(call.call_site),
                                f"Required parameter {param_name} not provided",
                                context=func_name,
                            )

            # 3. Handle *args (vararg) - collects remaining positional arguments
            if func_args.vararg:
                vararg_name = func_args.vararg.arg
                vararg_var = state.get_variable(
                    callee_scope, call_context, solver.variable_factory.make_variable(vararg_name)
                )

                # Create a tuple object to hold the varargs
                vararg_alloc = AllocSite(f"{call.call_site}:*args", AllocKind.TUPLE)
                vararg_tuple_obj = TupleObject(
                    state.select_allocation_context(call_context, vararg_alloc), vararg_alloc
                )

                # Add the tuple to the vararg parameter
                solver.handle_new_points_to(
                    vararg_var, callee_scope, PointsToSet.singleton(vararg_tuple_obj)
                )
                # Keep the tuple producer's aggregate relation persistent as
                # later argument families can introduce new positional slots.
                aggregate_var = state.get_field(
                    callee_scope, call_context, vararg_tuple_obj, elem()
                )

                # All remaining positional arguments go into *args
                for i in range(arg_index, len(arg_vars)):
                    # Store each remaining argument as an element of the tuple
                    field = key(i - arg_index)
                    element_var = state.get_field(
                        callee_scope, call_context, vararg_tuple_obj, field
                    )
                    state._add_var_points_flow(arg_vars[i], element_var)
                    state._add_var_points_flow(arg_vars[i], aggregate_var)
            elif arg_index < len(arg_vars):
                # Too many positional arguments and no *args to catch them
                solver._unknown_tracker.record(
                    UnknownKind.MISSING_ARGUMENT,
                    str(call.call_site),
                    f"Too many positional arguments: expected {arg_index}, got {len(arg_vars)}",
                    context=func_name,
                )

            # 4. Handle keyword-only parameters
            # These MUST be provided by keyword arguments (or use defaults)
            if func_args.kwonlyargs:
                for kw_idx, param in enumerate(func_args.kwonlyargs):
                    param_name = param.arg
                    param_var = state.get_variable(
                        callee_scope,
                        call_context,
                        solver.variable_factory.make_variable(param_name),
                    )

                    # Check if provided as keyword argument
                    if param_name in kwarg_vars:
                        # Bind keyword argument to parameter
                        state._add_var_points_flow(kwarg_vars[param_name], param_var)
                        consumed_kwargs.add(param_name)
                    elif func_args.kw_defaults and kw_idx < len(func_args.kw_defaults):
                        # Check if there's a default value
                        kw_default = func_args.kw_defaults[kw_idx]
                        if kw_default is not None:
                            # Has a default value
                            def_scope = callee_obj.container_scope
                            def_context = def_scope.context
                            default_ctx = self._materialize_default(
                                solver=solver,
                                func_ir=func_ir,
                                def_scope=def_scope,
                                def_context=def_context,
                                param_name=param_name,
                                default_index=kw_idx,
                                default_expr=kw_default,
                            )
                            state._add_var_points_flow(default_ctx, param_var)
                        elif not expanded:
                            # Without ** operands, the required keyword is absent.
                            solver._unknown_tracker.record(
                                UnknownKind.MISSING_ARGUMENT,
                                str(call.call_site),
                                f"Required keyword-only parameter {param_name} not provided",
                                context=func_name,
                            )
                    elif not expanded:
                        # Expanded mappings leave argument presence unresolved.
                        solver._unknown_tracker.record(
                            UnknownKind.MISSING_ARGUMENT,
                            str(call.call_site),
                            f"Required keyword-only parameter {param_name} not provided",
                            context=func_name,
                        )

            # 5. Handle **kwargs (kwarg) - collects remaining keyword arguments
            remaining_kwargs = {k: v for k, v in kwarg_vars.items() if k not in consumed_kwargs}

            if func_args.kwarg:
                kwarg_name = func_args.kwarg.arg
                kwarg_parameter = state.get_variable(
                    callee_scope, call_context, solver.variable_factory.make_variable(kwarg_name)
                )

                # Create a dict object to hold the kwargs
                kwarg_alloc = AllocSite(f"{call.call_site}:**kwargs", AllocKind.DICT)
                kwarg_dict_obj = DictObject(
                    state.select_allocation_context(call_context, kwarg_alloc), kwarg_alloc
                )

                # Add the dict to the kwarg parameter
                solver.handle_new_points_to(
                    kwarg_parameter, callee_scope, PointsToSet.singleton(kwarg_dict_obj)
                )
                if observe_collector and diagnostics is not None:
                    diagnostics.record(
                        "collector_binding",
                        scope,
                        call,
                        "keyword_collector",
                        refs={
                            "collector_parameter": kwarg_parameter,
                            "collector_object": kwarg_dict_obj,
                        },
                        fields={"stage": "publication_requested"},
                    )
                state.obj_scope[kwarg_dict_obj] = callee_scope
                excluded = frozenset(
                    param.arg for param in (*func_args.args, *func_args.kwonlyargs)
                )
                for mapping in expanded:
                    solver.add_constraint(
                        scope, context, MappingCopyConstraint(mapping, kwarg_dict_obj, excluded)
                    )
                    if observe_collector and diagnostics is not None:
                        diagnostics.record(
                            "dependency_registration_returned",
                            scope,
                            call,
                            "keyword_collector",
                            refs={"source_mapping": mapping, "collector_object": kwarg_dict_obj},
                            fields={
                                "channel": "expanded_mapping_copy",
                                "registration_kind": "mapping_constraint",
                            },
                        )

                # Store all remaining keyword arguments into the **kwargs dict
                for kw_name, kw_var in remaining_kwargs.items():
                    # Use the keyword name as the dict key (field)
                    field = key(kw_name)
                    dict_value_var = state.get_field(
                        callee_scope, call_context, kwarg_dict_obj, field
                    )
                    state._add_var_points_flow(kw_var, dict_value_var)
                    if observe_collector and diagnostics is not None:
                        diagnostics.record(
                            "dependency_registration_returned",
                            scope,
                            call,
                            "keyword_collector",
                            refs={
                                "source": kw_var,
                                "target": dict_value_var,
                                "collector_object": kwarg_dict_obj,
                            },
                            fields={
                                "channel": "key",
                                "keyword": kw_name,
                                "registration_kind": "copy_flow",
                            },
                        )
                    # Native values()/iteration consumers read the aggregate.
                    # Keep a subscription so both old and future argument
                    # values reach it, exactly as they reach the keyed slot.
                    aggregate_var = state.get_field(
                        callee_scope, call_context, kwarg_dict_obj, elem()
                    )
                    state._add_var_points_flow(kw_var, aggregate_var)
                    if observe_collector and diagnostics is not None:
                        diagnostics.record(
                            "dependency_registration_returned",
                            scope,
                            call,
                            "keyword_collector",
                            refs={
                                "source": kw_var,
                                "target": aggregate_var,
                                "collector_object": kwarg_dict_obj,
                            },
                            fields={
                                "channel": "aggregate",
                                "keyword": kw_name,
                                "registration_kind": "copy_flow",
                            },
                        )
                    # The remaining explicit keyword has a known string name.
                    # Preserve that identity separately from dictionary values;
                    # keys() must never fabricate an Unknown from those values.
                    key_site = AllocSite(
                        ("keyword_collector_key", call.call_site, kw_name), AllocKind.CONSTANT
                    )
                    key_object = state._heap.get_obj(callee_scope, call_context, key_site)
                    if key_object is None:
                        key_object = canonical_constant(state, call_context, key_site, kw_name)
                        state._heap.set_obj(callee_scope, call_context, key_site, key_object)
                        state.obj_scope[key_object] = callee_scope
                    keys_var = state.get_field(
                        callee_scope, call_context, kwarg_dict_obj, NATIVE_MAPPING_KEYS
                    )
                    solver.handle_new_points_to(
                        keys_var, callee_scope, PointsToSet.singleton(key_object)
                    )
                    if observe_collector and diagnostics is not None:
                        diagnostics.record(
                            "publication_requested",
                            scope,
                            call,
                            "keyword_collector",
                            refs={
                                "target": keys_var,
                                "object": key_object,
                                "collector_object": kwarg_dict_obj,
                            },
                            fields={"channel": "native_keys", "keyword": kw_name},
                        )
            elif remaining_kwargs:
                # Unexpected keyword arguments and no **kwargs to catch them
                extra_kw_names = ", ".join(remaining_kwargs.keys())
                solver._unknown_tracker.record(
                    UnknownKind.MISSING_ARGUMENT,
                    str(call.call_site),
                    f"Unexpected keyword arguments: {extra_kw_names}",
                    context=func_name,
                )
            if observe_collector and diagnostics is not None:
                if kwarg_parameter is None or kwarg_dict_obj is None:
                    raise RuntimeError(
                        "A keyword collector observation requires the allocated collector"
                    )
                diagnostics.record(
                    "model_complete",
                    scope,
                    call,
                    "keyword_collector",
                    refs={
                        "callee_scope": callee_scope,
                        "collector_parameter": kwarg_parameter,
                        "collector_object": kwarg_dict_obj,
                    },
                    fields={"stage": "parameter_matching_return"},
                )
