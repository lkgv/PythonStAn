"""IR to constraint translation.

This module translates IR events to pointer constraints for analysis.
"""

from __future__ import annotations

import ast
import logging
from collections import defaultdict
from collections.abc import Callable
from typing import override

from pythonstan.frontend.source import source_constant
from pythonstan.ir.ir_statements import (
    IRAssign,
    IRAstStmt,
    IRAwait,
    IRCall,
    IRCatchException,
    IRClass,
    IRContextEnter,
    IRContextExit,
    IRCopy,
    IRFunc,
    IRImport,
    IRLoadAttr,
    IRLoadSubscr,
    IRModule,
    IRRaise,
    IRReturn,
    IRScope,
    IRStatement,
    IRStoreAttr,
    IRStoreSubscr,
    IRYield,
    JumpIfFalse,
    JumpIfTrue,
)

from .config import Config
from .constraints import (
    AllocConstraint,
    AttrReadConstraint,
    AttrWriteConstraint,
    AwaitConstraint,
    CallConstraint,
    Constraint,
    CopyConstraint,
    LoadConstraint,
    LoadSubscrConstraint,
    OperatorProtocolConstraint,
    ResidualEffectConstraint,
    SpecialMethodLookupConstraint,
    StoreConstraint,
    StoreSubscrConstraint,
    TruthTestConstraint,
)
from .context import CallSite
from .heap_model import Field, FieldKind, attr, elem, key
from .native_mapping import NATIVE_MAPPING_KEY_FIELDS, NativeMappingProtocolConstraint
from .object import AllocKind, AllocSite
from .variable import Variable, VariableFactory, VariableKind

logger = logging.getLogger(__name__)

__all__ = ["IRTranslator"]

BINOP_TABLE = {
    ast.Add: "__add__",  # +
    ast.Sub: "__sub__",  # -
    ast.Mult: "__mul__",  # *
    ast.Div: "__truediv__",  # /
    ast.FloorDiv: "__floordiv__",  # //
    ast.Mod: "__mod__",  # %
    ast.Pow: "__pow__",  # **
    ast.LShift: "__lshift__",  # <<
    ast.RShift: "__rshift__",  # >>
    ast.BitOr: "__or__",  # |
    ast.BitXor: "__xor__",  # ^
    ast.BitAnd: "__and__",  # &
    ast.MatMult: "__matmul__",  # @ (矩阵乘法)
    ast.Eq: "__eq__",  # ==
    ast.NotEq: "__ne__",  # !=
    ast.Lt: "__lt__",  # <
    ast.LtE: "__le__",  # <=
    ast.Gt: "__gt__",  # >
    ast.GtE: "__ge__",  # >=
    ast.In: "__contains__",  # in
}

UNARYOP_TABLE = {
    ast.UAdd: "__pos__",  # +x
    ast.USub: "__neg__",  # -x
    ast.Not: "__bool__",  # not x
    ast.Invert: "__invert__",  # ~x
}


class IRTranslator:
    """Translates IR to pointer constraints."""

    def __init__(self, config: Config) -> None:
        self.function_constraint_transform: (
            Callable[[IRFunc, list[Constraint]], list[Constraint]] | None
        ) = None
        self.config = config
        self._var_factory = VariableFactory()
        self._current_scope: IRScope | None = None
        self._current_module: IRModule | None = None
        self._scope_constraints: dict[IRScope, list[Constraint]] = {}
        self._class_variables: dict[IRClass, tuple[Variable, ...]] = {}
        self.used_variables: list[Variable]
        self._import_depth = 0  # Track import depth for recursion limit
        self._local_vars: dict[IRScope, set[str]] = defaultdict(set)

        from pythonstan.world.world import World

        self.world = World()
        self.scope_manager = self.world.scope_manager
        self.namespace_manager = self.world.namespace_manager

    def translate_function(self, func: IRFunc) -> list[Constraint]:
        constraints: list[Constraint] = []

        # avoid infinite recursion
        if func in self._scope_constraints:
            return self._scope_constraints[func]

        self._current_scope = func
        locals_set = self._local_vars.setdefault(func, set())
        locals_set.update(func.get_arg_names())
        stmts = self.scope_manager.get_ir(func, "ir")
        if stmts is not None:
            for stmt in stmts:
                constraints.extend(self._process_stmt(stmt))

        self._scope_constraints[func] = constraints
        return constraints

    def translate_module(
        self, module: IRModule, import_stmt: IRImport | IRModule | None = None
    ) -> list[Constraint]:
        assert isinstance(module, IRModule), f"Module is not an IRModule: {type(module)}"

        # avoid infinite recursion
        if module in self._scope_constraints:
            return self._scope_constraints[module]

        constraints: list[Constraint] = []
        ir = self.scope_manager.get_statement_ir(module)

        if ir is not None:
            self._current_scope = module
            self._current_module = module
            for stmt in ir:
                constraints.extend(self._process_stmt(stmt))

            self._scope_constraints[module] = constraints

        return constraints

    def translate_class(self, cls_stmt: IRClass) -> list[Constraint]:
        """Translate class definition: allocate class object and bind methods."""
        assert isinstance(cls_stmt, IRClass), f"Class is not an IRClass: {type(cls_stmt)}"

        if cls_stmt in self._scope_constraints:
            self.used_variables = list(self._class_variables[cls_stmt])
            return self._scope_constraints[cls_stmt]

        constraints: list[Constraint] = []
        self.used_variables = []
        irs = self.scope_manager.get_ir(cls_stmt, "ir")

        if irs is not None:
            old_scope = self._current_scope
            self._current_scope = cls_stmt
            for stmt in irs:
                constraints.extend(self._process_stmt(stmt))
            self._current_scope = old_scope

        # This list belongs to this class, including an empty class. A warm
        # translation must not expose the last unrelated class's namespace.
        # Explicit global/nonlocal stores update their lexical binding, not
        # the class namespace, and must not trigger namespace descriptor hooks.
        self.used_variables = [var for var in self.used_variables if var.kind == VariableKind.LOCAL]
        self._class_variables[cls_stmt] = tuple(self.used_variables)
        self._scope_constraints[cls_stmt] = constraints

        return constraints

    def _make_variable(self, name: str) -> Variable:
        if self._current_scope is None:
            raise RuntimeError("No active scope for variable creation")

        # Check for temporary variables first (applies to all scopes)
        if name.startswith("$") and not name.startswith("$const"):
            kind = VariableKind.TEMPORARY
        elif isinstance(self._current_scope, IRModule) or name.startswith("$const"):
            kind = VariableKind.GLOBAL
        elif isinstance(self._current_scope, IRFunc):
            locals_in_scope = self._local_vars.get(self._current_scope, set())
            if name in self._current_scope.get_global_vars():
                kind = VariableKind.GLOBAL
            elif name in self._current_scope.get_nonlocal_vars():
                kind = VariableKind.NONLOCAL
            elif name in self._current_scope.get_cell_vars():
                kind = VariableKind.CELL
            elif name in locals_in_scope or name in self._current_scope.arg_names:
                kind = VariableKind.LOCAL
            else:
                enclosing_kind = self._resolve_enclosing_variable_kind(name)
                # Treat unresolved names as globals (module-level).
                kind = enclosing_kind if enclosing_kind is not None else VariableKind.GLOBAL
        elif isinstance(self._current_scope, IRClass):
            locals_in_scope = self._local_vars.get(self._current_scope, set())
            if name in self._current_scope.get_global_vars():
                kind = VariableKind.GLOBAL
            elif name in self._current_scope.get_nonlocal_vars():
                kind = VariableKind.NONLOCAL
            elif name in locals_in_scope:
                kind = VariableKind.LOCAL
            else:
                enclosing_kind = self._resolve_enclosing_variable_kind(name)
                kind = enclosing_kind if enclosing_kind is not None else VariableKind.GLOBAL
        elif name in self._current_scope.get_nonlocal_vars():
            kind = VariableKind.NONLOCAL
        elif name in self._current_scope.get_global_vars():
            kind = VariableKind.GLOBAL
        else:
            kind = VariableKind.LOCAL

        """
        if name.startswith('$'):
            kind = VariableKind.TEMPORARY
        elif name in self._current_scope.get_nonlocal_vars():
            kind = VariableKind.NONLOCAL
        elif name in self._current_scope.get_cell_vars():
            kind = VariableKind.CELL
        elif name in self._current_scope.get_global_vars():
            kind = VariableKind.GLOBAL
        else:
            kind = VariableKind.LOCAL
        """

        return self._var_factory.make_variable(name=name, kind=kind)

    def _register_local_var(self, name_or_var: str | Variable | None) -> None:
        if not name_or_var:
            return
        if not isinstance(self._current_scope, (IRFunc, IRClass)):
            return
        if isinstance(name_or_var, str):
            name = name_or_var
            is_temporary = not name.isidentifier()
        else:
            name = name_or_var.name
            is_temporary = name_or_var.is_temporary
        if not name:
            return
        if is_temporary:
            return
        locals_set = self._local_vars.setdefault(self._current_scope, set())
        locals_set.add(name)

    def _process_stmt(self, stmt: IRStatement) -> list[Constraint]:
        ret = []

        if isinstance(stmt, IRContextEnter):
            ret = self._translate_context_enter(stmt)
        elif isinstance(stmt, IRContextExit):
            ret = self._translate_context_exit(stmt)
        elif isinstance(stmt, IRCopy):
            ret = self._translate_copy(stmt)
        elif isinstance(stmt, IRAssign):
            ret = self._translate_assign(stmt)
        elif isinstance(stmt, IRLoadAttr):
            ret = self._translate_load_attr(stmt)
        elif isinstance(stmt, IRStoreAttr):
            ret = self._translate_store_attr(stmt)
        elif isinstance(stmt, IRCall):
            ret = self._translate_call(stmt)
        elif isinstance(stmt, IRReturn):
            ret = self._translate_return(stmt)
        elif isinstance(stmt, IRLoadSubscr):
            ret = self._translate_load_subscr(stmt)
        elif isinstance(stmt, IRStoreSubscr):
            ret = self._translate_store_subscr(stmt)
        elif isinstance(stmt, IRFunc):
            _, ret = self._translate_function_def(stmt)
        elif isinstance(stmt, IRClass):
            _, ret = self._translate_class_def(stmt)
        elif isinstance(stmt, IRImport):
            ret = self._translate_import(stmt)
        elif isinstance(stmt, IRYield):
            ret = self._translate_yield(stmt)
        elif isinstance(stmt, IRAwait):
            ret = self._translate_await(stmt)
        elif isinstance(stmt, IRAstStmt):
            ret = [
                self._residual(
                    stmt,
                    "unsupported source statement retained by IR",
                    "may_invoke_callbacks",
                    "may_mutate_heap",
                    "may_raise",
                )
            ]
        elif isinstance(stmt, (IRRaise, IRCatchException, JumpIfFalse, JumpIfTrue)):
            ret = [
                self._residual(
                    stmt,
                    "flow-insensitive constraints do not discharge control/exception routing",
                    "branch_selection",
                    "exception_routing",
                )
            ]
            if isinstance(stmt, (JumpIfFalse, JumpIfTrue)) and isinstance(stmt.test, ast.Name):
                ret.append(
                    TruthTestConstraint(
                        self._make_variable(stmt.test.id),
                        CallSite(stmt, self._current_scope_label()),
                    )
                )

        # Preserve frontend obligations independently of unknown diagnostics.
        # The existing constraints and may-call edges remain available.
        for residual in stmt.get_semantic_residuals():
            ret.append(
                self._residual(stmt, f"{residual.code}: {residual.reason}", *residual.effects)
            )

        for c in ret:
            assert isinstance(c, Constraint), (
                f"Constraint is not a constraint: {type(c)}, stmt: {stmt}"
            )

        return ret

    def _translate_copy(self, stmt: IRCopy) -> list[Constraint]:
        """Translate IRCopy: target = source"""
        constraints: list[Constraint] = []

        lval = stmt.get_lval().id
        rval = stmt.get_rval().id
        self._register_local_var(lval)
        target_var = self._make_variable(lval)
        source_var = self._make_variable(rval)
        constraints.append(CopyConstraint(source=source_var, target=target_var))

        # for fields of class, we need to store the value to the class
        if (
            isinstance(self._current_scope, IRClass)
            and target_var is not None
            and not target_var.is_temporary
        ):  # only store the field to the class if it is not a temporary variable
            self.used_variables.append(target_var)

        return constraints

    def _translate_yield(self, stmt: IRYield) -> list[Constraint]:
        """Translate IRYield: [target =] yield value.

        Yields store values to the generator object's elem() field, enabling
        iteration via next()/for-loops. If a target is present (target = yield value),
        the sent value flows into the target from the generator's $sent field.

        Design:
        - Yielded values -> generator.$elem via StoreConstraint
        - Sent values -> target via LoadConstraint from generator.$sent (future)
        - Generator object tracked via special $generator variable in scope
        """
        constraints: list[Constraint] = []

        # Get or create the generator variable for this scope
        constraints.append(
            self._residual(
                stmt,
                "generator continuation, throw/close and cleanup routing are not discharged",
                "suspend_resume",
                "exception_routing",
                "cleanup_order",
            )
        )
        generator_var = self._make_variable("$generator")

        if stmt.is_yield_from():
            value = stmt.value
            if value is None:
                raise AttributeError("yield-from IR has no delegated value")
            delegated = self._make_variable(value.id)
            yielded = self._make_variable(f"$delegated_yield@{id(stmt)}")
            sent = self._make_variable(f"$delegated_sent@{id(stmt)}")
            completion = None
            if isinstance(stmt.target, ast.Name):
                self._register_local_var(stmt.target.id)
                completion = self._make_variable(stmt.target.id)
            constraints.extend(
                (
                    LoadConstraint(generator_var, attr("$sent"), sent),
                    AwaitConstraint(
                        delegated,
                        yielded,
                        CallSite(stmt, self._get_current_scope_label()),
                        operation="delegate",
                        completion_target=completion,
                        sent_value=sent,
                    ),
                    StoreConstraint(generator_var, elem(), yielded),
                    self._residual(
                        stmt,
                        "yield-from may channels retain exhaustion, exception forwarding and arbitrary iterator completion obligations",
                        "delegation",
                        "iterator_exhaustion",
                        "exception_routing",
                        "cleanup_order",
                    ),
                )
            )
            return constraints

        # If there's a value being yielded, store it to generator.elem()
        if stmt.value is not None:
            value_var = self._make_variable(stmt.value.id)
            constraints.append(StoreConstraint(base=generator_var, field=elem(), source=value_var))
        else:
            # Bare yield produces the real Python value None. An empty value
            # set would erase consumers of a valid synchronous/async chunk.
            value_var = self._make_variable(f"$bare_yield_none@{id(stmt)}")
            assignment = ast.Assign(
                targets=[ast.Name(id=value_var.name, ctx=ast.Store())],
                value=ast.Constant(value=None),
            )
            ast.copy_location(assignment, stmt.get_ast())
            constraints.extend(
                (
                    AllocConstraint(
                        value_var, AllocSite.from_ir_node(IRAssign(assignment), AllocKind.CONSTANT)
                    ),
                    StoreConstraint(generator_var, elem(), value_var),
                )
            )

        # If there's a target (target = yield value), it receives sent values
        # For now, we model send() conservatively by loading from a $sent field
        if stmt.target is not None and isinstance(stmt.target, ast.Name):
            target_name = stmt.target.id
            self._register_local_var(target_name)
            target_var = self._make_variable(target_name)
            # Load from generator.$sent field (populated by generator.send())
            constraints.append(
                LoadConstraint(base=generator_var, field=attr("$sent"), target=target_var)
            )

        return constraints

    def _translate_await(self, stmt: IRAwait) -> list[Constraint]:
        """Translate IRAwait: [target =] await value.

        Awaiting loads the result from the awaitable's $await_result field.
        The async function's return statement stores to this field.

        Design:
        - await coro -> Load coro.$await_result into target
        - For soundness, also copy the awaitable directly (handles non-coroutine awaitables)
        """
        constraints: list[Constraint] = []

        constraints.append(
            self._residual(
                stmt,
                "await value flow does not discharge scheduling, cancellation or custom await protocol",
                "await_protocol",
                "cancellation",
                "resume_order",
            )
        )
        # Get the awaitable variable
        awaitable_expr = stmt.get_value()
        if not isinstance(awaitable_expr, ast.Name):
            return constraints

        awaitable_var = self._make_variable(awaitable_expr.id)

        # If there's a target, load the await result into it
        target = stmt.get_target()
        target_var = None
        if target is not None and isinstance(target, ast.Name):
            target_name = target.id
            self._register_local_var(target_name)
            target_var = self._make_variable(target_name)

            # Load from awaitable.$await_result (set by async function return)
            constraints.append(
                LoadConstraint(base=awaitable_var, field=attr("$await_result"), target=target_var)
            )

        suspension = self._make_variable(f"$await_suspension@{id(stmt)}")
        sent = self._make_variable(f"$await_sent@{id(stmt)}")
        suspension_field = Field(FieldKind.CLASS_METADATA, "continuation.suspension")
        # A coroutine binds $coroutine; an async generator binds $generator.
        # The other variable has no native holder and publishes no heap fact.
        for holder in ("$coroutine", "$generator"):
            constraints.append(
                StoreConstraint(self._make_variable(holder), suspension_field, suspension)
            )
            constraints.append(LoadConstraint(self._make_variable(holder), attr("$sent"), sent))
        # Result fields do not execute a coroutine. This ordinary persistent
        # dependency also observes awaitables published after registration.
        constraints.append(
            AwaitConstraint(
                awaitable_var,
                target_var,
                CallSite(statement=stmt, scope_name=self._current_scope_label()),
                suspension_target=suspension,
                sent_value=sent,
            )
        )

        return constraints

    def _translate_assign(self, stmt: IRAssign) -> list[Constraint]:
        """Translate IRAssign: may allocate objects"""
        constraints: list[Constraint] = []
        lval = stmt.get_lval()
        rval = stmt.get_rval()

        self._register_local_var(lval.id)
        target_var = self._make_variable(lval.id)

        if isinstance(rval, ast.List):
            alloc_site = AllocSite.from_ir_node(stmt, AllocKind.LIST)
            constraints.append(AllocConstraint(target=target_var, alloc_site=alloc_site))
            if hasattr(rval, "elts"):
                for i, elt_expr in enumerate(rval.elts):
                    if isinstance(elt_expr, ast.Name):
                        elem_var = self._make_variable(elt_expr.id)
                        if self.config.index_sensitive:
                            # Store at specific position field
                            constraints.append(
                                StoreConstraint(base=target_var, field=key(i), source=elem_var)
                            )
                        # Also store at generic elem() for soundness
                        constraints.append(
                            StoreConstraint(base=target_var, field=elem(), source=elem_var)
                        )

        elif isinstance(rval, ast.BinOp):
            if not isinstance(rval.left, ast.Name):
                raise AttributeError(f"'{type(rval.left).__name__}' object has no attribute 'id'")
            if not isinstance(rval.right, ast.Name):
                raise AttributeError(f"'{type(rval.right).__name__}' object has no attribute 'id'")
            lvar = self._make_variable(rval.left.id)
            rvar = self._make_variable(rval.right.id)
            target = self._make_variable(lval.id)
            constraints.extend(self._translate_binary_op(lvar, rvar, target, rval.op, stmt))

        elif isinstance(rval, ast.UnaryOp):
            if not isinstance(rval.operand, ast.Name):
                raise AttributeError(
                    f"'{type(rval.operand).__name__}' object has no attribute 'id'"
                )
            operand = self._make_variable(rval.operand.id)
            target = self._make_variable(lval.id)
            constraints.extend(self._translate_unary_op(operand, target, rval.op, stmt))

        elif isinstance(rval, ast.Compare):
            constraints.append(
                self._residual(
                    stmt,
                    "comparison, membership and reflected comparison protocols remain unmodeled",
                    "comparison_protocol",
                    "may_invoke_callbacks",
                    "may_mutate_heap",
                    "may_raise",
                )
            )
            constraints.append(
                AllocConstraint(target_var, AllocSite.from_ir_node(stmt, AllocKind.UNKNOWN))
            )

        elif isinstance(rval, ast.Dict):
            alloc_site = AllocSite.from_ir_node(stmt, AllocKind.DICT)
            constraints.append(AllocConstraint(target=target_var, alloc_site=alloc_site))
            if hasattr(rval, "keys") and hasattr(rval, "values"):
                for i, (key_expr, val_expr) in enumerate(zip(rval.keys, rval.values, strict=False)):
                    if isinstance(val_expr, ast.Name):
                        val_var = self._make_variable(val_expr.id)
                        if key_expr is None:
                            # **mapping contributes values, not the mapping
                            # object itself. Unknown keys use the wildcard cell.
                            unpacked = self._make_variable(f"$dict_unpack_{id(stmt)}_{i}")
                            constraints.append(LoadConstraint(val_var, elem(), unpacked))
                            constraints.append(StoreConstraint(target_var, elem(), unpacked))
                            for field in NATIVE_MAPPING_KEY_FIELDS:
                                private_keys = self._make_variable(
                                    f"$dict_unpack_keys_{id(stmt)}_{i}_{field.name}"
                                )
                                constraints.append(LoadConstraint(val_var, field, private_keys))
                                constraints.append(StoreConstraint(target_var, field, private_keys))
                            constraints.append(
                                self._residual(
                                    stmt,
                                    "Dictionary unpacking retains known key metadata; custom mapping protocols and opaque key provenance remain open",
                                    "native_mapping_key_provenance",
                                    "mapping_protocol",
                                    "may_invoke_callbacks",
                                    "may_raise",
                                )
                            )
                            continue
                        if isinstance(key_expr, ast.Name):
                            constraints.append(
                                NativeMappingProtocolConstraint(
                                    target_var,
                                    self._make_variable(key_expr.id),
                                    CallSite(stmt, self._get_current_scope_label(), index=i),
                                    True,
                                    "literal_insert",
                                )
                            )
                        # Handle both constant keys and name keys (from TAC transformation)
                        if isinstance(key_expr, ast.Constant) and self.config.index_sensitive:
                            # Direct constant key
                            field = key(source_constant(key_expr))
                            constraints.append(
                                StoreConstraint(base=target_var, field=field, source=val_var)
                            )
                        elif isinstance(key_expr, ast.Name) and self.config.index_sensitive:
                            # Key is a name (e.g., from TAC: $const_1 = "a"; d = {$const_1: v})
                            # Use StoreSubscrConstraint for dynamic resolution
                            key_var = self._make_variable(key_expr.id)
                            constraints.append(
                                StoreSubscrConstraint(
                                    base=target_var, index=key_var, source=val_var
                                )
                            )
                        # Always add elem() constraint for soundness
                        constraints.append(
                            StoreConstraint(
                                base=target_var,
                                field=elem(),
                                source=val_var,
                                summary_only=self.config.index_sensitive
                                and isinstance(key_expr, (ast.Constant, ast.Name)),
                            )
                        )

        # Tuple uses position-based fields for precise tracking
        elif isinstance(rval, ast.Tuple):
            alloc_site = AllocSite.from_ir_node(stmt, AllocKind.TUPLE)
            constraints.append(AllocConstraint(target=target_var, alloc_site=alloc_site))
            if hasattr(rval, "elts"):
                for i, elt_expr in enumerate(rval.elts):
                    if isinstance(elt_expr, ast.Name):
                        elem_var = self._make_variable(elt_expr.id)
                        if self.config.index_sensitive:
                            # Store at specific position field
                            constraints.append(
                                StoreConstraint(base=target_var, field=key(i), source=elem_var)
                            )
                        # Also store at generic elem() for soundness
                        constraints.append(
                            StoreConstraint(base=target_var, field=elem(), source=elem_var)
                        )

        elif isinstance(rval, ast.Set):
            alloc_site = AllocSite.from_ir_node(stmt, AllocKind.SET)
            constraints.append(AllocConstraint(target=target_var, alloc_site=alloc_site))
            if hasattr(rval, "elts"):
                for elt_expr in rval.elts:
                    if isinstance(elt_expr, ast.Name):
                        elem_var = self._make_variable(elt_expr.id)
                        constraints.append(
                            StoreConstraint(base=target_var, field=elem(), source=elem_var)
                        )

        elif isinstance(rval, ast.Constant):
            alloc_site = AllocSite.from_ir_node(stmt, AllocKind.CONSTANT)
            constraints.append(AllocConstraint(target=target_var, alloc_site=alloc_site))

        else:
            alloc_site = AllocSite.from_ir_node(stmt, AllocKind.UNKNOWN)
            constraints.append(AllocConstraint(target=target_var, alloc_site=alloc_site))

        # for fields of class, we need to store the value to the class
        if (
            isinstance(self._current_scope, IRClass)
            and target_var is not None
            and not target_var.is_temporary
        ):  # only store the field to the class if it is not a temporary variable
            self.used_variables.append(target_var)

        return constraints

    def _translate_load_attr(self, stmt: IRLoadAttr) -> list[Constraint]:
        """Translate IRLoadAttr: target = base.attr"""
        lval = stmt.get_lval().id
        obj_name = stmt.get_obj().id
        attr_name = stmt.get_attr()

        self._register_local_var(lval)
        base_var = self._make_variable(obj_name)
        target_var = self._make_variable(lval)
        attr_value = attr_name if attr_name else "<unknown>"
        call_site = CallSite(statement=stmt, scope_name=self._get_current_scope_label())

        if (
            isinstance(self._current_scope, IRClass)
            and target_var is not None
            and not target_var.is_temporary
        ):  # only store the field to the class if it is not a temporary variable
            self.used_variables.append(target_var)

        return [
            AttrReadConstraint(
                base=base_var, attr=attr_value, target=target_var, call_site=call_site
            )
        ]

    def _translate_store_attr(self, stmt: IRStoreAttr) -> list[Constraint]:
        """Translate IRStoreAttr: base.attr = source"""
        obj_name = stmt.get_obj().id
        attr_name = stmt.get_attr()
        value_name = stmt.get_rval().id

        base_var = self._make_variable(obj_name)
        source_var = self._make_variable(value_name)
        attr_value = attr_name if attr_name else "<unknown>"
        call_site = CallSite(statement=stmt, scope_name=self._get_current_scope_label())

        return [
            AttrWriteConstraint(
                base=base_var, attr=attr_value, source=source_var, call_site=call_site
            )
        ]

    def _translate_call(self, stmt: IRCall) -> list[Constraint]:
        """Translate IRCall: target = callee(args...)"""
        # Only trusted frontend lowering attaches this in-memory AST marker.
        # Implicit async iteration performs type-level protocol lookup, without
        # consulting source globals named iter/next/aiter/anext.
        async_operation = getattr(stmt.call, "_pythonstan_async_iteration_operation", None)
        if async_operation in {"aiter", "anext"}:
            arguments = stmt.get_args()
            if len(arguments) != 1 or arguments[0][1]:
                return [
                    self._residual(
                        stmt,
                        "Malformed internal async iteration operation",
                        "async_iteration_dispatch",
                        "may_raise",
                    )
                ]
            target = stmt.get_target()
            if target:
                self._register_local_var(target)
            return [
                AwaitConstraint(
                    self._make_variable(arguments[0][0]),
                    self._make_variable(target) if target else None,
                    CallSite(stmt, self._get_current_scope_label()),
                    operation=async_operation,
                )
            ]
        # TODO all conditions of arguments should be translated to constraints
        constraints: list[Constraint] = []

        lval = stmt.get_target()
        callee_expr = stmt.get_func_name()
        args = stmt.get_args()

        callee_var = self._make_variable(callee_expr)
        arg_values: list[Variable] = []
        for idx, (arg, _) in enumerate(args):
            if arg.startswith("<Constant: ") and arg.endswith(">"):
                value_str = arg[len("<Constant: ") : -1]
                try:
                    const_value = ast.literal_eval(value_str)
                except Exception:
                    const_value = value_str
                const_name = f"$const_{id(stmt)}_{idx}"
                const_var = self._make_variable(const_name)
                const_assign = IRAssign(
                    ast.Assign(
                        targets=[ast.Name(id=const_name, ctx=ast.Store())],
                        value=ast.Constant(value=const_value),
                    )
                )
                const_alloc = AllocSite.from_ir_node(const_assign, AllocKind.CONSTANT)
                constraints.append(AllocConstraint(target=const_var, alloc_site=const_alloc))
                arg_values.append(const_var)
            else:
                arg_values.append(self._make_variable(arg))
        arg_vars = tuple(arg_values)

        target_var = None
        if lval:
            self._register_local_var(lval)
            target_var = self._make_variable(lval)

        call_site_scope = self._get_current_scope_label()
        call_site = CallSite(statement=stmt, scope_name=call_site_scope)
        # Keep every expansion operand: distinct ** mappings may contribute
        # different keys, so a dict keyed only by the marker loses operands.
        keyword_vars = frozenset(
            (kw_name if kw_name is not None else "**", self._make_variable(kw_val))
            for kw_name, kw_val in stmt.get_keywords()
        )
        constraints.append(
            CallConstraint(
                callee=callee_var,
                args=arg_vars,
                kwargs=keyword_vars,
                target=target_var,
                call_site=call_site,
            )
        )

        # for fields of class, we need to store the value to the class
        if (
            isinstance(self._current_scope, IRClass)
            and target_var is not None
            and not target_var.is_temporary
        ):  # only store the field to the class if it is not a temporary variable
            self.used_variables.append(target_var)

        return constraints

    def _current_scope_label(self) -> str:
        if self._current_scope is None:
            raise RuntimeError("No active scope for constraint translation")
        return self._current_scope.get_qualname()

    def _get_current_scope_label(self) -> str:
        """Return a stable string label for the current scope.

        Ensures that the entry module is always labeled '__main__' so tests
        expecting module-level calls from __main__ succeed, while other modules
        retain their qualified names.
        """
        if self._current_scope is None:
            return "<unknown>"

        try:
            entry_module = getattr(self.world, "entry_module", None)
        # except AttributeError:
        #     entry_module = None
        finally:
            ...

        if isinstance(self._current_scope, IRModule):
            if entry_module is None or self._current_scope is entry_module:
                return "__main__"
            return self._current_scope.get_qualname()

        return self._current_scope.get_qualname()

    def _resolve_enclosing_variable_kind(self, name: str) -> VariableKind | None:
        """Infer variable kind from enclosing scopes when closure metadata is missing."""
        if self._current_scope is None:
            return None

        father_map = self.scope_manager.father
        parent = father_map.get(self._current_scope)

        while parent is not None:
            locals_in_parent = self._local_vars.get(parent, set())
            if isinstance(parent, IRFunc):
                arg_names = parent.arg_names
                if name in parent.get_global_vars():
                    return VariableKind.GLOBAL
                if name in parent.get_nonlocal_vars():
                    return VariableKind.NONLOCAL
                if name in parent.get_cell_vars() or name in locals_in_parent or name in arg_names:
                    return VariableKind.CELL
            # Enclosing class namespaces are not lexical closure scopes for
            # nested class or function bodies. Bases/decorators are translated
            # in their actual surrounding scope separately.
            elif (
                isinstance(parent, IRModule)
                and hasattr(parent, "global_vars")
                and name in parent.get_global_vars()
            ):
                return VariableKind.GLOBAL
            parent = father_map.get(parent)

        return None

    def _translate_return(self, stmt: IRReturn) -> list[Constraint]:
        """Translate IRReturn: return value.

        For regular functions: stores to $return variable.
        For async functions: stores to $coroutine.$await_result field.
        For generator functions: yield handles the values, return just exits.
        """
        rval = stmt.get_value()

        if not rval:
            return []

        source_var = self._make_variable(rval)
        constraints: list[Constraint] = []

        # Check if we're in an async function
        is_async = isinstance(self._current_scope, IRFunc) and self._current_scope.is_async

        if is_async:
            # For async functions, store to coroutine's $await_result field
            coroutine_var = self._make_variable("$coroutine")
            constraints.append(
                StoreConstraint(base=coroutine_var, field=attr("$await_result"), source=source_var)
            )

        # Always copy to $return for normal call handling
        return_var = self._make_variable("$return")
        constraints.append(CopyConstraint(source=source_var, target=return_var))

        return constraints

    def _translate_load_subscr(self, stmt: IRLoadSubscr) -> list[Constraint]:
        """Translate IRLoadSubscr: target = container[index]"""
        lval = stmt.get_lval().id
        container = stmt.get_obj()
        if not isinstance(container, ast.Name):
            raise TypeError("Native subscript base must be a normalized name")
        container_name = container.id

        subslice = stmt.get_slice()
        self._register_local_var(lval)
        container_var = self._make_variable(container_name)
        target_var = self._make_variable(lval)

        constraints: list[Constraint] = []

        # Extract index variable for dynamic field resolution
        if hasattr(subslice, "id"):
            index_var = self._make_variable(subslice.id)
        else:
            # For non-name indexes (constants, etc.), create a temporary
            index_name = f"$index_{id(stmt)}"
            index_var = self._make_variable(index_name)
            if isinstance(subslice, ast.Constant):
                const_assign = IRAssign(
                    ast.Assign(
                        targets=[ast.Name(id=index_name, ctx=ast.Store())],
                        value=subslice,
                    )
                )
                const_alloc = AllocSite.from_ir_node(const_assign, AllocKind.CONSTANT)
                constraints.append(AllocConstraint(target=index_var, alloc_site=const_alloc))

        # Generate LoadSubscrConstraint with index variable for dynamic resolution
        constraints.append(
            NativeMappingProtocolConstraint(
                container_var,
                index_var,
                CallSite(stmt, self._get_current_scope_label()),
                False,
                "subscript_load",
            )
        )
        if self.config.index_sensitive:
            constraints.append(
                LoadSubscrConstraint(base=container_var, index=index_var, target=target_var)
            )
        else:
            constraints.append(LoadConstraint(base=container_var, field=elem(), target=target_var))

        try:
            # Also generate __getitem__ call for custom container types
            getitem_method_var = self._make_variable(f"$getitem_{id(stmt)}")
            constraints.append(
                LoadConstraint(
                    base=container_var,
                    field=attr("__getitem__"),
                    target=getitem_method_var,
                    index=None,
                )
            )
            call_site = CallSite(statement=stmt, scope_name=self._get_current_scope_label())
            constraints.append(
                CallConstraint(
                    callee=getitem_method_var,
                    args=(index_var,),
                    kwargs=frozenset(),
                    target=target_var,
                    call_site=call_site,
                )
            )
        finally:
            pass

        # for fields of class, we need to store the value to the class
        if (
            isinstance(self._current_scope, IRClass)
            and target_var is not None
            and not target_var.is_temporary
        ):  # only store the field to the class if it is not a temporary variable
            self.used_variables.append(target_var)

        return constraints

    def _translate_store_subscr(self, stmt: IRStoreSubscr) -> list[Constraint]:
        """Translate IRStoreSubscr: container[index] = value"""
        container = stmt.get_obj()
        if not isinstance(container, ast.Name):
            raise TypeError("Native subscript base must be a normalized name")
        container_name = container.id
        value_name = stmt.get_rval().id

        container_var = self._make_variable(container_name)
        value_var = self._make_variable(value_name)

        constraints: list[Constraint] = []

        # Extract index variable for dynamic field resolution
        index_expr = stmt.get_slice()
        if hasattr(index_expr, "id"):
            index_var = self._make_variable(index_expr.id)
        else:
            # For non-name indexes (constants, etc.), create a temporary
            index_name = f"$index_{id(stmt)}"
            index_var = self._make_variable(index_name)
            if isinstance(index_expr, ast.Constant):
                const_assign = IRAssign(
                    ast.Assign(
                        targets=[ast.Name(id=index_name, ctx=ast.Store())],
                        value=index_expr,
                    )
                )
                const_alloc = AllocSite.from_ir_node(const_assign, AllocKind.CONSTANT)
                constraints.append(AllocConstraint(target=index_var, alloc_site=const_alloc))

        # Generate StoreSubscrConstraint with index variable for dynamic resolution
        constraints.append(
            NativeMappingProtocolConstraint(
                container_var,
                index_var,
                CallSite(stmt, self._get_current_scope_label()),
                True,
                "subscript_store",
            )
        )
        constraints.append(
            StoreSubscrConstraint(base=container_var, index=index_var, source=value_var)
        )
        if self.config.index_sensitive:
            constraints.append(
                StoreSubscrConstraint(base=container_var, index=index_var, source=value_var)
            )
        else:
            constraints.append(StoreConstraint(base=container_var, field=elem(), source=value_var))

        # Also generate __setitem__ call for custom container types
        setitem_method_var = self._make_variable(f"$setitem_{id(stmt)}")
        constraints.append(
            LoadConstraint(
                base=container_var, field=attr("__setitem__"), target=setitem_method_var, index=None
            )
        )

        try:
            call_site = CallSite(statement=stmt, scope_name=self._get_current_scope_label())

            constraints.append(
                CallConstraint(
                    callee=setitem_method_var,
                    args=(index_var, value_var),
                    kwargs=frozenset(),
                    target=None,
                    call_site=call_site,
                )
            )
        finally:
            ...
        # except Exception as e:
        #     logger.debug(f"Error generating __setitem__ call: {e}")

        return constraints

    def _translate_expression(
        self, expression: ast.expr, owner: IRStatement, index: int = 0
    ) -> tuple[Variable, list[Constraint]]:
        """Normalize direct-IR callers through the shared TAC expression path.

        In the normal source pipeline decorators are already names. This
        fallback also supports clients that directly construct IRFunc nodes.
        """
        if isinstance(expression, ast.Name):
            return self._make_variable(expression.id), []
        from pythonstan.analysis.transform.ir import IRTransformer
        from pythonstan.analysis.transform.three_address import (
            ThreeAddressTransformer,
            make_constant,
        )
        from pythonstan.utils import TempVarGenerator

        prefix = f"$expr_{id(owner)}_{index}_"
        transformer = ThreeAddressTransformer()
        transformer.tmp_gen = TempVarGenerator(template=prefix + "%d")
        statements, value = transformer.split_expr(expression)
        constants = transformer.const_colle.dump()
        constant_names = {
            name: prefix + "constant_" + str(i) for i, (name, _) in enumerate(constants)
        }

        class RenameConstants(ast.NodeTransformer):
            @override
            def visit_Name(self, node: ast.Name) -> ast.Name:
                if node.id in constant_names:
                    return ast.copy_location(
                        ast.Name(id=constant_names[node.id], ctx=node.ctx), node
                    )
                return node

        rename = RenameConstants()
        statements = [rename.visit(statement) for statement in statements]
        value = rename.visit(value)
        constant_stmts = [
            ast.Assign(
                targets=[ast.Name(id=constant_names[name], ctx=ast.Store())],
                value=make_constant(constant),
            )
            for name, constant in constants
        ]
        scope = self._current_scope
        if scope is None:
            raise RuntimeError("No active scope for expression translation")
        ir = IRTransformer(scope)
        ir.process_stmts(constant_stmts + statements)
        constraints: list[Constraint] = []
        for statement in ir.get_stmts():
            constraints.extend(self._process_stmt(statement))
        if not isinstance(value, ast.Name):
            raise ValueError("expression did not normalize to a name")
        return self._make_variable(value.id), constraints

    def _residual(self, stmt: IRStatement, reason: str, *effects: str) -> ResidualEffectConstraint:
        # Legacy source-location extensions are diagnostic metadata only. Keep
        # their historical precedence without treating an arbitrary extension
        # value as executable IR or a semantic state record.
        statement_ast: object = getattr(stmt, "stmt_ast", None)
        statement: object = getattr(stmt, "stmt", statement_ast)
        original: object = getattr(stmt, "origin", statement)
        line: object = getattr(original, "lineno", "?")
        location = f"{self._get_current_scope_label()}:{line}:{type(stmt).__name__}"
        return ResidualEffectConstraint(location, reason, tuple(effects))

    def _context_variables(self, entry: IRContextEnter) -> dict[str, Variable]:
        prefix = f"$context_{id(entry)}_"
        return {
            name: self._make_variable(prefix + name)
            for name in ("manager", "enter", "exit", "entered", "exit_result", "exception")
        }

    def _translate_context_enter(self, stmt: IRContextEnter) -> list[Constraint]:
        variables = self._context_variables(stmt)
        manager, constraints = self._translate_expression(stmt.item.context_expr, stmt)
        constraints.append(CopyConstraint(manager, variables["manager"]))
        enter_name, exit_name = (
            ("__aenter__", "__aexit__") if stmt.is_async else ("__enter__", "__exit__")
        )
        # Acquire both type-level methods before invoking enter. An instance
        # attribute or __getattribute__ must not replace this implicit lookup.
        constraints.extend(
            [
                SpecialMethodLookupConstraint(variables["manager"], enter_name, variables["enter"]),
                SpecialMethodLookupConstraint(variables["manager"], exit_name, variables["exit"]),
                CallConstraint(
                    callee=variables["enter"],
                    args=(variables["manager"],),
                    kwargs=frozenset(),
                    target=variables["entered"],
                    call_site=CallSite(stmt, self._get_current_scope_label(), index=0),
                ),
                self._residual(
                    stmt,
                    "context-manager may-call lowering leaves exceptional cleanup, suppression, "
                    "enter failure and custom special-method descriptor binding unresolved",
                    "exception_routing",
                    "cleanup_order",
                    "exception_suppression",
                    "custom_descriptor_binding",
                    "may_mutate_heap",
                    "may_invoke_callbacks",
                ),
            ]
        )
        target = stmt.item.optional_vars
        if isinstance(target, ast.Name):
            self._register_local_var(target.id)
            target_var = self._make_variable(target.id)
            if stmt.is_async:
                awaited = IRAwait(
                    ast.Assign(
                        targets=[ast.Name(id=target_var.name, ctx=ast.Store())],
                        value=ast.Await(
                            value=ast.Name(id=variables["entered"].name, ctx=ast.Load())
                        ),
                    )
                )
                constraints.extend(self._translate_await(awaited))
            else:
                constraints.append(CopyConstraint(variables["entered"], target_var))
        elif target is not None:
            constraints.append(
                self._residual(stmt, "context as-target was not normalized", "target_assignment")
            )
        if stmt.is_async:
            constraints.append(
                self._residual(
                    stmt,
                    "async context suspension and cancellation not discharged",
                    "await_protocol",
                    "cancellation",
                    "resume_order",
                )
            )
        return constraints

    def _translate_context_exit(self, stmt: IRContextExit) -> list[Constraint]:
        entry = stmt.entry
        variables = self._context_variables(entry)
        # Union normal None with an unknown exceptional triple. This is a
        # may-value abstraction; it does not prove eligibility or suppression.
        none_stmt = IRAssign(
            ast.Assign(
                targets=[ast.Name(id=variables["exception"].name, ctx=ast.Store())],
                value=ast.Constant(value=None),
            )
        )
        constraints: list[Constraint] = [
            AllocConstraint(
                variables["exception"], AllocSite.from_ir_node(none_stmt, AllocKind.CONSTANT)
            ),
            AllocConstraint(
                variables["exception"], AllocSite.from_ir_node(stmt, AllocKind.UNKNOWN)
            ),
            CallConstraint(
                callee=variables["exit"],
                args=(
                    variables["manager"],
                    variables["exception"],
                    variables["exception"],
                    variables["exception"],
                ),
                kwargs=frozenset(),
                target=variables["exit_result"],
                call_site=CallSite(stmt, self._get_current_scope_label(), index=1),
            ),
        ]
        if entry.is_async:
            awaited = IRAwait(
                ast.Expr(
                    value=ast.Await(
                        value=ast.Name(id=variables["exit_result"].name, ctx=ast.Load())
                    )
                )
            )
            constraints.extend(self._translate_await(awaited))
        return constraints

    def _translate_function_def(self, stmt: IRFunc) -> tuple[Variable, list[Constraint]]:
        """Allocate raw function, then apply fully evaluated decorators."""
        constraints: list[Constraint] = []
        self._register_local_var(stmt.name)
        self._local_vars.setdefault(stmt, set()).update(stmt.get_arg_names())
        decorators = []
        for index, expression in enumerate(stmt.decorator_list):
            decorator, evaluation = self._translate_expression(expression, stmt, index)
            constraints.extend(evaluation)
            decorators.append(decorator)
        kind = (
            AllocKind.METHOD
            if isinstance(self._current_scope, IRClass) and not stmt.is_static_method
            else AllocKind.FUNCTION
        )
        func_var = self._make_variable(stmt.name)
        # A decorated binding receives only the final result. Keeping the raw
        # function in it manufactures bypass paths through replacement decorators.
        raw_var = self._make_variable(f"$raw_function_{id(stmt)}") if decorators else func_var
        constraints.append(AllocConstraint(raw_var, AllocSite.from_ir_node(stmt, kind)))
        current_var = raw_var
        for index, decorator in enumerate(reversed(decorators)):
            result_var = self._make_variable(f"$decorated_{id(stmt)}_{index}")
            constraints.append(
                CallConstraint(
                    callee=decorator,
                    args=(current_var,),
                    kwargs=frozenset(),
                    target=result_var,
                    call_site=CallSite(stmt, self._get_current_scope_label(), index=index),
                )
            )
            current_var = result_var
        if decorators:
            constraints.append(CopyConstraint(current_var, func_var))
        if isinstance(self._current_scope, IRClass) and not func_var.is_temporary:
            self.used_variables.append(func_var)
        if self.function_constraint_transform is not None:
            constraints = self.function_constraint_transform(stmt, constraints)
        return func_var, constraints

    def _translate_class_def(self, ir_cls: IRClass) -> tuple[Variable, list[Constraint]]:
        """Translate class definition: allocate class object and bind methods."""
        constraints: list[Constraint] = []
        self._register_local_var(ir_cls.name)
        # Base expressions are evaluated in the surrounding lexical scope.
        # Preserve the resolved kinds before entering the class scope.
        ir_cls._kcfa_base_variables = tuple(
            self._make_variable(base.id) for base in ir_cls.get_bases()
        )

        class_alloc = AllocSite.from_ir_node(ir_cls, AllocKind.CLASS)
        class_var = self._make_variable(ir_cls.name)
        constraints.append(AllocConstraint(target=class_var, alloc_site=class_alloc))

        # Metaclass identity is a monotone relation, not part of object identity.
        # ThreeAddress evaluates keyword expressions before the class body.
        from .processor.class_protocol import METACLASS_FIELD

        for keyword in ir_cls.keywords:
            if keyword.arg == "metaclass" and isinstance(keyword.value, ast.Name):
                constraints.append(
                    StoreConstraint(
                        base=class_var,
                        field=METACLASS_FIELD,
                        source=self._make_variable(keyword.value.id),
                    )
                )

        # TODO [CRITICAL] the method of treating inheritances is totally wrong, we should use the class hierarchy manager to handle this.
        """
        for base_name in ir_cls.get_bases():
            if not isinstance(base_name, ast.Name):
                continue # TODO resolve the base name in detail
            
            base_var = self._make_variable(base_name.id)
            constraints.append(StoreConstraint(
                base=class_var,
                field=attr("__bases__"),
                source=base_var
            ))
        """

        # for fields of class, we need to store the value to the class
        if (
            isinstance(self._current_scope, IRClass) and not class_var.is_temporary
        ):  # only store the field if it is not a temporary variable
            self.used_variables.append(class_var)

        return class_var, constraints

    def _translate_import(self, stmt: IRImport) -> list[Constraint]:
        """Preserve Python's module binding, re-export and submodule paths.

        Import order and attribute-presence precedence remain may-relations;
        neither a missing export fact nor a file of the same name proves that
        the package's exported value is absent.
        """
        constraints: list[Constraint] = []
        graph = self.scope_manager.get_module_graph()
        declaring_module = self._current_scope
        ancestors: set[IRScope] = set()
        while (
            declaring_module is not None
            and not isinstance(declaring_module, IRModule)
            and declaring_module not in ancestors
        ):
            ancestors.add(declaring_module)
            declaring_module = self.scope_manager.father.get(declaring_module)
        module_ir = (
            graph.get_succ_module(declaring_module, stmt)
            if isinstance(declaring_module, IRModule)
            else None
        )
        bound_name = stmt.asname or (stmt.name.split(".")[0] if stmt.module is None else stmt.name)
        self._register_local_var(bound_name)
        local_var = self._make_variable(bound_name)
        if module_ir is None:
            constraints.append(
                AllocConstraint(local_var, AllocSite.from_ir_node(stmt, AllocKind.UNKNOWN))
            )
            constraints.append(
                ResidualEffectConstraint(
                    str(stmt),
                    "Unresolved import retains unknown value and external initialization/callback obligations",
                    ("unresolved_import", "may_mutate_heap", "may_invoke_callbacks", "may_raise"),
                )
            )
            return constraints

        constraints.append(
            ResidualEffectConstraint(
                str(stmt),
                "Shared source-module may facts leave import order, loader hooks, reload and dynamic exports open",
                (
                    "module_initialization_order",
                    "import_hooks",
                    "module_dynamic_exports",
                    "may_raise",
                ),
            )
        )

        def allocate(module: IRModule, variable: Variable) -> None:
            constraints.append(
                AllocConstraint(variable, AllocSite.from_ir_node(module, AllocKind.MODULE))
            )

        if stmt.module is None:
            bound_module = module_ir
            if stmt.asname is None and "." in stmt.name:
                root_module = self.scope_manager.get_module(stmt.name.split(".")[0])
                if isinstance(root_module, IRModule):
                    bound_module = root_module
                else:
                    constraints.append(
                        ResidualEffectConstraint(
                            str(stmt),
                            "Namespace-package parent binding requires a modeled package object",
                            ("namespace_package", "may_raise"),
                        )
                    )
            allocate(bound_module, local_var)
        else:
            base_name = stmt.peek_import_base_name()
            parent_module = self.scope_manager.get_module(base_name) if base_name else None
            # When a child source file exists, bind that module directly. Also
            # retain the package export path: __init__ may define an equal-named
            # value before Python's submodule fallback is considered.
            child_path = base_name and module_ir.get_qualname() == base_name + "." + stmt.name
            if child_path:
                allocate(module_ir, local_var)
            export_module = parent_module if child_path else module_ir
            if isinstance(export_module, IRModule):
                module_var = self._make_variable(f"$import_module@{id(stmt)}")
                allocate(export_module, module_var)
                if stmt.name == "*":
                    exports: set[str] = set()
                    for instruction in self.scope_manager.get_statement_ir(export_module) or ():
                        if isinstance(instruction, (IRFunc, IRClass)):
                            exports.add(instruction.name)
                        else:
                            exports.update(instruction.get_stores())
                    # __all__ can include private lexical names and can be
                    # assembled dynamically. Retain those candidates as well;
                    # a lexical public-name filter would miss real callbacks.
                    for name in sorted(n for n in exports if n.isidentifier()):
                        if self._current_scope is None:
                            raise RuntimeError("No active scope for import binding")
                        extra = self._current_scope.ensure_source_extra_exports()
                        extra.add(name)
                        self._register_local_var(name)
                        constraints.append(
                            LoadConstraint(module_var, attr(name), self._make_variable(name))
                        )
                    constraints.append(
                        ResidualEffectConstraint(
                            str(stmt),
                            "Star imports retain known lexical exports including private candidates; dynamic __all__ and module __getattr__ remain open",
                            ("star_import_names", "may_invoke_callbacks", "may_raise"),
                        )
                    )
                else:
                    constraints.append(LoadConstraint(module_var, attr(stmt.name), local_var))
        # for fields of class, we need to store the value to the class
        if (
            isinstance(self._current_scope, IRClass) and not local_var.is_temporary
        ):  # only store the field to the class if it is not a temporary variable
            self.used_variables.append(local_var)

        return constraints

    def _translate_with_enter(
        self, context_manager_var: Variable, target_var: Variable
    ) -> list[Constraint]:
        """Generate constraints for context manager __enter__.
        Used for: with obj as target: ...
        Generates: temp = obj.__enter__; target = temp()
        """
        constraints: list[Constraint] = []

        enter_method_var = self._make_variable(f"$enter_{id(context_manager_var)}")
        constraints.append(
            LoadConstraint(
                base=context_manager_var, field=attr("__enter__"), target=enter_method_var
            )
        )

        parsed = ast.parse(f"[{target_var}] = {context_manager_var}.__enter__()").body[0]
        if not isinstance(parsed, ast.Assign):
            raise TypeError("Generated enter call must be an assignment")
        call_stmt = IRCall(parsed)
        call_site = CallSite(statement=call_stmt, scope_name=self._get_current_scope_label())
        constraints.append(
            CallConstraint(
                callee=enter_method_var,
                args=(),
                kwargs=frozenset(),
                target=target_var,
                call_site=call_site,
            )
        )

        return constraints

    def _translate_with_exit(self, context_manager_var: Variable) -> list[Constraint]:
        """Generate constraints for context manager __exit__.
        Used for: with obj: ... (at exit)
        Generates: temp = obj.__exit__; temp(None, None, None)
        """
        constraints: list[Constraint] = []

        exit_method_var = self._make_variable(f"$exit_{id(context_manager_var)}")
        constraints.append(
            LoadConstraint(base=context_manager_var, field=attr("__exit__"), target=exit_method_var)
        )

        parsed = ast.parse(f"{context_manager_var}.__exit__(None, None, None)").body[0]
        if not isinstance(parsed, ast.Expr) or not isinstance(parsed.value, ast.Call):
            raise TypeError("Generated exit call must be a call expression")
        call_stmt = IRCall(parsed.value)
        call_site = CallSite(statement=call_stmt, scope_name=self._get_current_scope_label())
        constraints.append(
            CallConstraint(
                callee=exit_method_var,
                args=(),
                kwargs=frozenset(),
                target=None,
                call_site=call_site,
            )
        )

        return constraints

    def _translate_iter(self, iterable_var: Variable, target_var: Variable) -> list[Constraint]:
        """Generate constraints for iterator creation.
        Used for: for item in obj: ...
        Generates: iter_temp = obj.__iter__; target = iter_temp()
        """
        constraints: list[Constraint] = []

        iter_method_var = self._make_variable(f"$iter_{id(iterable_var)}")
        constraints.append(
            LoadConstraint(base=iterable_var, field=attr("__iter__"), target=iter_method_var)
        )

        parsed = ast.parse(f"[{target_var}] = {iterable_var}.__iter__()").body[0]
        if not isinstance(parsed, ast.Assign):
            raise TypeError("Generated iter call must be an assignment")
        call_stmt = IRCall(parsed)
        call_site = CallSite(statement=call_stmt, scope_name=self._get_current_scope_label())
        constraints.append(
            CallConstraint(
                callee=iter_method_var,
                args=(),
                kwargs=frozenset(),
                target=target_var,
                call_site=call_site,
            )
        )

        return constraints

    def _translate_next(self, iterator_var: Variable, target_var: Variable) -> list[Constraint]:
        """Generate constraints for iterator next."""
        constraints: list[Constraint] = []

        next_method_var = self._make_variable(f"$next_{id(iterator_var)}")
        constraints.append(
            LoadConstraint(base=iterator_var, field=attr("__next__"), target=next_method_var)
        )

        parsed = ast.parse(f"[{target_var}] = {iterator_var}.__next__()").body[0]
        if not isinstance(parsed, ast.Assign):
            raise TypeError("Generated next call must be an assignment")
        call_stmt = IRCall(parsed)
        call_site = CallSite(statement=call_stmt, scope_name=self._get_current_scope_label())
        constraints.append(
            CallConstraint(
                callee=next_method_var,
                args=(),
                kwargs=frozenset(),
                target=target_var,
                call_site=call_site,
            )
        )

        return constraints

    def _translate_binary_op(
        self,
        left_var: Variable,
        right_var: Variable,
        target_var: Variable,
        op: ast.operator,
        stmt: IRStatement,
    ) -> list[Constraint]:
        constraints: list[Constraint] = []

        op_name = BINOP_TABLE.get(type(op))
        if not op_name:
            return constraints

        constraints.append(
            AllocConstraint(target_var, AllocSite.from_ir_node(stmt, AllocKind.UNKNOWN))
        )
        constraints.append(
            OperatorProtocolConstraint(
                left_var,
                op_name,
                (right_var,),
                target_var,
                CallSite(stmt, self._get_current_scope_label(), 0),
            )
        )
        constraints.append(
            OperatorProtocolConstraint(
                right_var,
                "__r" + op_name[2:],
                (left_var,),
                target_var,
                CallSite(stmt, self._get_current_scope_label(), 1),
            )
        )

        if (
            isinstance(self._current_scope, IRClass)
            and target_var is not None
            and not target_var.is_temporary
        ):  # only store the field to the class if it is not a temporary variable
            self.used_variables.append(target_var)

        return constraints

    def _translate_unary_op(
        self,
        operand_var: Variable,
        target_var: Variable,
        op: ast.unaryop,
        stmt: IRStatement,
    ) -> list[Constraint]:
        constraints: list[Constraint] = []

        op_name = UNARYOP_TABLE.get(type(op))
        if not op_name:
            return constraints

        constraints.append(
            AllocConstraint(target_var, AllocSite.from_ir_node(stmt, AllocKind.UNKNOWN))
        )
        call_site = CallSite(stmt, self._get_current_scope_label())
        if isinstance(op, ast.Not):
            # not x produces a bool, not x itself or the raw __bool__ result.
            constraints.append(TruthTestConstraint(operand_var, call_site))
        else:
            constraints.append(
                OperatorProtocolConstraint(operand_var, op_name, (), target_var, call_site)
            )

        if (
            isinstance(self._current_scope, IRClass)
            and target_var is not None
            and not target_var.is_temporary
        ):  # only store the field to the class if it is not a temporary variable
            self.used_variables.append(target_var)

        return constraints
