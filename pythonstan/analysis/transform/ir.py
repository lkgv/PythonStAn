from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal, overload, override

from pythonstan.analysis.analysis import AnalysisConfig
from pythonstan.ir.ir_statements import (
    Goto,
    IRAnno,
    IRAssign,
    IRAstStmt,
    IRAwait,
    IRCall,
    IRCatchException,
    IRClass,
    IRContextEnter,
    IRContextExit,
    IRCopy,
    IRDel,
    IRFunc,
    IRImport,
    IRLoadAttr,
    IRLoadSubscr,
    IRPass,
    IRRaise,
    IRReturn,
    IRScope,
    IRStatement,
    IRStoreAttr,
    IRStoreSubscr,
    IRYield,
    JumpIfFalse,
    Label,
)
from pythonstan.world.world import World

from .transform import Transform

__all__ = ["STAGE_NAME", "IR", "IRTransformer"]
STAGE_NAME = "ir"


imports: list[IRImport] = []


@dataclass(frozen=True, slots=True)
class _FinallyCleanup:
    body: list[ast.stmt]
    loop_depth: int
    outer_handlers: tuple[IRCatchException, ...]


@dataclass(frozen=True, slots=True)
class _WithCleanup:
    entry: IRContextEnter
    loop_depth: int
    outer_handlers: tuple[IRCatchException, ...]


@dataclass(frozen=True, slots=True)
class _ExceptTargetCleanup:
    name: str
    origin: ast.ExceptHandler
    loop_depth: int
    outer_handlers: tuple[IRCatchException, ...]


type _CleanupFrame = _FinallyCleanup | _WithCleanup | _ExceptTargetCleanup
type _AbruptCleanup = Literal["return", "break", "continue"]


class IR(Transform):
    transformer: IRTransformer

    def __init__(self, config: AnalysisConfig) -> None:
        super().__init__(config)

    @override
    def transform(self, module: IRScope) -> None:
        three_address_form: object = World().scope_manager.get_ir(module, "three address form")
        if not isinstance(three_address_form, ast.Module):
            raise TypeError("Three-address input must be a module AST")
        imports.clear()
        self.transformer = IRTransformer(module)
        self.transformer.process_stmts(three_address_form.body)
        ir = self.transformer.stmts
        World().scope_manager.set_ir(module, STAGE_NAME, ir)
        # Each module owns its ledger. The next transform clears the scratch
        # list, which must not erase or replace imports of earlier modules.
        World().scope_manager.set_ir(module, "imports", list(imports))
        # self.results = imports


class LabelGenerator:
    next_idx: int

    def __init__(self) -> None:
        self.next_idx = 0

    def gen(self) -> Label:
        label = Label(self.next_idx)
        self.next_idx += 1
        return label


class IRTransformer(ast.NodeVisitor):
    imports: list[IRImport]
    stmts: list[IRStatement]
    funcs: list[IRFunc]
    classes: list[IRClass]
    breaks_stack: list[list[Goto]]
    continues_stack: list[list[Goto]]
    next_idx: int
    scope: IRScope
    label_gen: LabelGenerator
    cleanup_stack: list[_CleanupFrame]
    exception_handlers: tuple[IRCatchException, ...]
    orig_stmts: list[ast.stmt]
    _pending_value_index: int

    def __init__(self, scope: IRScope) -> None:
        self.scope = scope
        self.reset()

    def reset(self) -> None:
        self.breaks_stack = []
        self.continues_stack = []
        self.imports = []
        self.classes = []
        self.stmts = []
        self.funcs = []
        self.label_gen = LabelGenerator()
        self.cleanup_stack = []
        self.exception_handlers = ()
        self._pending_value_index = 0

    def emit[StatementT: IRStatement](self, statement: StatementT) -> StatementT:
        """Attach the active protected-region plan to an executable IR node.

        Handler plans are source ordered and nearest first. They are metadata,
        not ordinary successors; backend transfers select exceptional channels.
        """
        # Anchors/metadata cannot throw and must not point back into a plan
        # which already owns their label, creating cyclic observation graphs.
        structural = isinstance(statement, (Label, IRPass, IRCatchException, Goto, IRReturn))
        statement.exception_handlers = () if structural else tuple(self.exception_handlers)
        self.stmts.append(statement)
        return statement

    @overload
    def _save_pending_value(self, value: None, origin: ast.AST) -> None: ...

    @overload
    def _save_pending_value(self, value: ast.expr, origin: ast.AST) -> ast.Name: ...

    def _save_pending_value(self, value: ast.expr | None, origin: ast.AST) -> ast.Name | None:
        if value is None:
            return None
        name = f"$pending_completion_{self._pending_value_index}"
        self._pending_value_index += 1
        assignment = ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())], value=deepcopy(value))
        ast.copy_location(assignment, origin)
        ast.fix_missing_locations(assignment)
        self.emit(IRCopy(assignment))
        return ast.Name(id=name, ctx=ast.Load())

    def _delete_handler_target(self, name: str, origin: ast.AST) -> None:
        deletion = ast.Delete(targets=[ast.Name(id=name, ctx=ast.Del())])
        ast.copy_location(deletion, origin)
        ast.fix_missing_locations(deletion)
        self.emit(IRDel(deletion))

    def _run_cleanups(self, completion: _AbruptCleanup) -> None:
        """Unwind only suites crossed by this return/break/continue edge.

        During a suite, its own frame is removed. An abrupt cleanup therefore
        replaces the pending completion and still unwinds enclosing frames.
        A break in a loop nested *inside* a try does not leave that try.
        """
        frames = self.cleanup_stack
        handlers = self.exception_handlers
        loop_depth = len(self.breaks_stack)
        try:
            for index in range(len(frames) - 1, -1, -1):
                frame = frames[index]
                if completion in ("break", "continue") and frame.loop_depth < loop_depth:
                    continue
                self.cleanup_stack = frames[:index]
                self.exception_handlers = frame.outer_handlers
                if isinstance(frame, _FinallyCleanup):
                    self.visit_stmts(deepcopy(frame.body))
                elif isinstance(frame, _WithCleanup):
                    statement = IRContextExit(frame.entry)
                    statement.completion_kind = completion
                    self.emit(statement)
                else:
                    self._delete_handler_target(frame.name, frame.origin)
        finally:
            self.cleanup_stack = frames
            self.exception_handlers = handlers

    def visit_stmts(self, stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            self.visit(stmt)

    def get_imports(self) -> list[IRImport]:
        return self.imports

    def get_stmts(self) -> list[IRStatement]:
        return self.stmts

    def postprocess(self) -> None:
        self.dedumplicate_nop()

    def dedumplicate_nop(self) -> None:
        del_list = []
        for stmt, next_stmt in zip(self.stmts, self.stmts[1:], strict=False):
            if isinstance(stmt, IRPass) and not isinstance(next_stmt, Label):
                del_list.append(stmt)
        for stmt in del_list:
            self.stmts.remove(stmt)

    def process_stmts(self, stmts: list[ast.stmt]) -> None:
        self.orig_stmts = stmts

        self.reset()
        self.visit_stmts(stmts)
        self.postprocess()

    @override
    def visit_FunctionDef(self, func_def: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = f"{self.scope.get_qualname()}.{func_def.name}"
        func = IRFunc(qualname, func_def, is_method=isinstance(self.scope, IRClass))
        trans = IRTransformer(func)
        trans.process_stmts(func_def.body)
        World().scope_manager.add_func(self.scope, func)
        World().scope_manager.set_ir(func, "ir", trans.get_stmts())
        self.emit(func)

    @override
    def visit_ClassDef(self, cls_def: ast.ClassDef) -> None:
        qualname = f"{self.scope.get_qualname()}.{cls_def.name}"
        cls = IRClass(qualname, cls_def)
        trans = IRTransformer(cls)
        trans.process_stmts(cls_def.body)
        World().scope_manager.add_class(self.scope, cls)
        World().scope_manager.set_ir(cls, "ir", trans.get_stmts())
        self.emit(cls)

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def _visit_context_manager(
        self, node: ast.With | ast.AsyncWith, is_async: bool = False
    ) -> None:
        def visit_item(index: int) -> None:
            if index == len(node.items):
                self.visit_stmts(node.body)
                return
            item = deepcopy(node.items[index])
            # Cleanup uses the entered manager even when its source binding is
            # reassigned by the body. Special methods are captured by the
            # backend at the entry marker, following the protocol contract.
            item.context_expr = self._save_pending_value(item.context_expr, node)
            entry = IRContextEnter(item, node, is_async)
            outer_handlers = tuple(self.exception_handlers)
            outer_cleanups = self.cleanup_stack
            self.emit(entry)
            label_body = self.label_gen.gen()
            label_exception = self.label_gen.gen()
            label_end = self.label_gen.gen()
            plan = IRCatchException(None, label_body, label_exception, label_exception)
            frame = _WithCleanup(entry, len(self.breaks_stack), outer_handlers)
            try:
                self.cleanup_stack = outer_cleanups + [frame]
                self.exception_handlers = (plan,) + outer_handlers
                self.emit(label_body)
                self.emit(IRPass())
                visit_item(index + 1)
                self.cleanup_stack = outer_cleanups
                self.exception_handlers = outer_handlers
                normal_exit = IRContextExit(entry)
                normal_exit.completion_kind = "normal"
                self.emit(normal_exit)
                self.emit(Goto(label_end))
                self.emit(plan)
                self.emit(label_exception)
                saved_exception = self._save_pending_value(
                    ast.Name(id="@caught_except", ctx=ast.Load()), node
                )
                exceptional_exit = IRContextExit(entry)
                exceptional_exit.completion_kind = "raise"
                # True __exit__ branches to end; False resumes this exception.
                exceptional_exit.suppressed_label = label_end
                self.emit(exceptional_exit)
                self._reraise(node, saved_exception.id)
                self.emit(label_end)
                self.emit(IRPass())
            finally:
                self.cleanup_stack = outer_cleanups
                self.exception_handlers = outer_handlers

        visit_item(0)

    @override
    def visit_With(self, node: ast.With) -> None:
        self._visit_context_manager(node)

    @override
    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_context_manager(node, is_async=True)

    @override
    def visit_Break(self, node: ast.Break) -> None:
        assert len(self.breaks_stack) > 0
        self._run_cleanups("break")
        stmt = Goto()
        self.breaks_stack[-1].append(stmt)
        self.emit(stmt)

    @override
    def visit_Continue(self, node: ast.Continue) -> None:
        assert len(self.continues_stack) > 0
        self._run_cleanups("continue")
        stmt = Goto()
        self.continues_stack[-1].append(stmt)
        self.emit(stmt)

    @override
    def visit_Return(self, node: ast.Return) -> None:
        if not self.cleanup_stack:
            self.emit(IRReturn(node))
            return
        value = self._save_pending_value(node.value, node)
        self._run_cleanups("return")
        terminal = ast.Return(value=value)
        ast.copy_location(terminal, node)
        ast.fix_missing_locations(terminal)
        self.emit(IRReturn(terminal))

    @override
    def visit_Import(self, node: ast.Import) -> None:
        stmt = IRImport(node)
        imports.append(stmt)
        self.emit(stmt)

    @override
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        stmt = IRImport(node)
        imports.append(stmt)
        self.emit(stmt)

    @override
    def visit_Global(self, node: ast.Global) -> None:
        for name in node.names:
            self.scope.add_global_var(name)

    @override
    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        for name in node.names:
            self.scope.add_nonlocal_var(name)

    @override
    def visit_While(self, node: ast.While) -> None:
        self.breaks_stack.append([])
        self.continues_stack.append([])

        label_begin = self.label_gen.gen()
        self.emit(label_begin)
        self.emit(IRPass())
        cond_jmp = JumpIfFalse(test=node.test, stmt_ast=node)
        self.emit(cond_jmp)
        self.visit_stmts(node.body)
        loop_breaks = self.breaks_stack.pop()
        loop_continues = self.continues_stack.pop()
        absolute_jmp = Goto()
        absolute_jmp.set_label(label_begin)
        self.emit(absolute_jmp)

        if node.orelse is not None and len(node.orelse) > 0:
            label_orelse = self.label_gen.gen()
            self.emit(label_orelse)
            self.emit(IRPass())
            cond_jmp.set_label(label_orelse)
            self.visit_stmts(node.orelse)
            label_end = self.label_gen.gen()
            self.emit(label_end)
            self.emit(IRPass())
        else:
            label_end = self.label_gen.gen()
            self.emit(label_end)
            self.emit(IRPass())
            cond_jmp.set_label(label_end)

        for stmt in loop_breaks:
            stmt.set_label(label_end)
        for stmt in loop_continues:
            stmt.set_label(label_begin)

    @override
    def visit_If(self, node: ast.If) -> None:
        cond_jmp = JumpIfFalse(test=node.test, stmt_ast=node)
        self.emit(cond_jmp)
        self.visit_stmts(node.body)
        if len(node.orelse) > 0:
            true_end_jmp = Goto()
            self.emit(true_end_jmp)
            label_else = self.label_gen.gen()
            self.emit(label_else)
            self.emit(IRPass())
            self.visit_stmts(node.orelse)
            label_end = self.label_gen.gen()
            self.emit(label_end)
            self.emit(IRPass())
            cond_jmp.set_label(label_else)
            true_end_jmp.set_label(label_end)
        else:
            label_end = self.label_gen.gen()
            self.emit(label_end)
            self.emit(IRPass())
            cond_jmp.set_label(label_end)

    @override
    def visit_Raise(self, node: ast.Raise) -> None:
        stmt = IRRaise(node)
        self.emit(stmt)

    @staticmethod
    def _handler_names(handler: ast.ExceptHandler) -> list[str] | None:
        if handler.type is None:
            return None
        if isinstance(handler.type, ast.Name):
            return (
                ["@dynamic_exception_type"]
                if handler.type.id.startswith("$")
                else [handler.type.id]
            )
        if isinstance(handler.type, ast.Tuple):
            names: list[str] = []
            for value in handler.type.elts:
                if not isinstance(value, ast.Name):
                    return ["@dynamic_exception_type"]
                names.append(value.id)
            return names
        # Dynamic types are kept as an explicit unknown match; a backend must
        # branch conservatively and retain an unresolved-handler obligation.
        return ["@dynamic_exception_type"]

    def _reraise(self, origin: ast.AST, saved_exception_var: str | None = None) -> IRRaise:
        node = ast.Raise(
            exc=ast.Name(id=saved_exception_var or "@caught_except", ctx=ast.Load()), cause=None
        )
        ast.copy_location(node, origin)
        ast.fix_missing_locations(node)
        statement = IRRaise(node)
        statement.saved_exception_var = saved_exception_var
        return self.emit(statement)

    @override
    def visit_Try(self, node: ast.Try) -> None:
        outer_handlers = tuple(self.exception_handlers)
        outer_cleanups = self.cleanup_stack
        label_try, label_try_end = self.label_gen.gen(), self.label_gen.gen()
        label_normal_finally, label_end = self.label_gen.gen(), self.label_gen.gen()
        label_exception_finally = self.label_gen.gen() if node.finalbody else None
        handler_labels = [self.label_gen.gen() for _ in node.handlers]
        handler_plans = [
            IRCatchException(self._handler_names(handler), label_try, label_try_end, label, handler)
            for handler, label in zip(node.handlers, handler_labels, strict=False)
        ]
        finally_plan = (
            IRCatchException(None, label_try, label_try_end, label_exception_finally)
            if label_exception_finally is not None
            else None
        )
        fallback_handlers = ((finally_plan,) if finally_plan else ()) + outer_handlers
        if node.finalbody:
            frame = _FinallyCleanup(node.finalbody, len(self.breaks_stack), outer_handlers)
            self.cleanup_stack = outer_cleanups + [frame]
        try:
            self.exception_handlers = tuple(handler_plans) + fallback_handlers
            self.emit(label_try)
            self.emit(IRPass())
            self.visit_stmts(node.body)
            self.exception_handlers = fallback_handlers
            self.emit(label_try_end)
            self.visit_stmts(node.orelse)
            self.emit(Goto(label_normal_finally))

            # Metadata declarations remain inspectable to legacy consumers,
            # but no ordinary control-flow edge executes or selects them.
            for plan in handler_plans:
                self.emit(plan)
            for handler, label in zip(node.handlers, handler_labels, strict=False):
                self.exception_handlers = fallback_handlers
                self.emit(label)
                self.emit(IRPass())
                target_frame: _ExceptTargetCleanup | None = None
                label_delete_exception = None
                if handler.name is not None:
                    assignment = ast.Assign(
                        targets=[ast.Name(id=handler.name, ctx=ast.Store())],
                        value=ast.Name(id="@caught_except", ctx=ast.Load()),
                    )
                    ast.copy_location(assignment, handler)
                    ast.fix_missing_locations(assignment)
                    self.emit(IRCopy(assignment))
                    target_frame = _ExceptTargetCleanup(
                        handler.name, handler, len(self.breaks_stack), fallback_handlers
                    )
                    self.cleanup_stack = self.cleanup_stack + [target_frame]
                    label_delete_exception = self.label_gen.gen()
                    delete_plan = IRCatchException(
                        None, label, label_delete_exception, label_delete_exception
                    )
                    self.exception_handlers = (delete_plan,) + fallback_handlers
                self.visit_stmts(handler.body)
                self.exception_handlers = fallback_handlers
                if target_frame is not None:
                    self.cleanup_stack = self.cleanup_stack[:-1]
                    self._delete_handler_target(target_frame.name, handler)
                self.emit(Goto(label_normal_finally))
                if label_delete_exception is not None:
                    assert handler.name is not None
                    self.emit(label_delete_exception)
                    self._delete_handler_target(handler.name, handler)
                    self._reraise(handler)

            # An exceptional entry runs cleanup with the saved exception.
            # A new return/raise/break/continue replaces it; otherwise reraise.
            self.cleanup_stack = outer_cleanups
            self.exception_handlers = outer_handlers
            if finally_plan:
                assert label_exception_finally is not None
                self.emit(finally_plan)
                self.emit(label_exception_finally)
                self.emit(IRPass())
                saved_exception = self._save_pending_value(
                    ast.Name(id="@caught_except", ctx=ast.Load()), node
                )
                self.visit_stmts(deepcopy(node.finalbody))
                self._reraise(node, saved_exception.id)
            self.emit(label_normal_finally)
            self.emit(IRPass())
            self.visit_stmts(deepcopy(node.finalbody))
            self.emit(label_end)
            self.emit(IRPass())
        finally:
            self.cleanup_stack = outer_cleanups
            self.exception_handlers = outer_handlers

    @override
    def visit_Assign(self, stmt: ast.Assign) -> None:
        if isinstance(stmt.value, (ast.Yield, ast.YieldFrom)):
            self.emit(IRYield(stmt))
        elif isinstance(stmt.value, ast.Await):
            self.emit(IRAwait(stmt))
        elif isinstance(stmt.value, ast.Call):
            self.emit(IRCall(stmt))
        elif isinstance(stmt.targets[0], ast.Subscript):
            self.emit(IRStoreSubscr(stmt))
        elif isinstance(stmt.value, ast.Subscript):
            self.emit(IRLoadSubscr(stmt))
        elif isinstance(stmt.targets[0], ast.Attribute):
            self.emit(IRStoreAttr(stmt))
        elif isinstance(stmt.value, ast.Attribute):
            self.emit(IRLoadAttr(stmt))
        elif isinstance(stmt.value, ast.Name):
            self.emit(IRCopy(stmt))
        else:
            self.emit(IRAssign(stmt))

    @override
    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.emit(IRAnno(node))

    @override
    def visit_Delete(self, node: ast.Delete) -> None:
        self.emit(IRDel(node))

    @override
    def visit_Pass(self, node: ast.Pass) -> None:
        self.emit(IRPass(node))

    @override
    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, ast.stmt):
            self.emit(IRAstStmt(node))
        else:
            super().generic_visit(node)
