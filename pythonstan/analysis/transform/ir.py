from ast import *
from copy import deepcopy
from typing import List, Dict, Optional, Any, Union

from ..analysis import AnalysisConfig
from .transform import Transform
from pythonstan.world import World
from pythonstan.ir import *


__all__ = ['STAGE_NAME', 'IR', 'IRTransformer']
STAGE_NAME = "ir"


imports = []

class IR(Transform):
    transformer: 'IRTransformer'

    def __init__(self, config: AnalysisConfig):
        super().__init__(config)

    def transform(self, module: IRModule):
        global imports
        three_address_form = World().scope_manager.get_ir(module, "three address form")
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

    def __init__(self):
        self.next_idx = 0

    def gen(self) -> Label:
        label = Label(self.next_idx)
        self.next_idx += 1
        return label


class IRTransformer(NodeVisitor):
    imports: List[IRImport]
    stmts: List[IRStatement]
    funcs: List[IRFunc]
    classes: List[IRClass]
    breaks_stack: List[List[Goto]]
    continues_stack: List[List[Goto]]
    next_idx: int
    scope: IRScope
    label_gen: LabelGenerator

    def __init__(self, scope: IRScope):
        self.scope = scope
        self.reset()

    def reset(self):
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

    def emit(self, statement):
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

    def _save_pending_value(self, value, origin):
        if value is None:
            return None
        name = f"$pending_completion_{self._pending_value_index}"
        self._pending_value_index += 1
        assignment = Assign(targets=[Name(id=name, ctx=Store())], value=deepcopy(value))
        copy_location(assignment, origin)
        fix_missing_locations(assignment)
        self.emit(IRCopy(assignment))
        return Name(id=name, ctx=Load())

    def _delete_handler_target(self, name, origin):
        deletion = Delete(targets=[Name(id=name, ctx=Del())])
        copy_location(deletion, origin)
        fix_missing_locations(deletion)
        self.emit(IRDel(deletion))

    def _run_cleanups(self, completion):
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
                if completion in ("break", "continue") and frame["loop_depth"] < loop_depth:
                    continue
                self.cleanup_stack = frames[:index]
                self.exception_handlers = frame["outer_handlers"]
                if frame["kind"] == "finally":
                    self.visit_stmts(deepcopy(frame["body"]))
                elif frame["kind"] == "with":
                    statement = IRContextExit(frame["entry"])
                    statement.completion_kind = completion
                    self.emit(statement)
                elif frame["kind"] == "except_delete":
                    self._delete_handler_target(frame["name"], frame["origin"])
        finally:
            self.cleanup_stack = frames
            self.exception_handlers = handlers

    def visit_stmts(self, stmts: List[stmt]):
        for stmt in stmts:
            self.visit(stmt)

    def get_imports(self) -> List[IRImport]:
        return self.imports

    def get_stmts(self) -> List[IRStatement]:
        return self.stmts

    def postprocess(self):
        self.dedumplicate_nop()

    def dedumplicate_nop(self):
        del_list = []
        for stmt, next_stmt in zip(self.stmts, self.stmts[1:]):
            if isinstance(stmt, IRPass) and not isinstance(next_stmt, Label):
                del_list.append(stmt)
        for stmt in del_list:
            self.stmts.remove(stmt)

    def process_stmts(self, stmts: List[stmt]):
        self.orig_stmts = stmts


        self.reset()
        self.visit_stmts(stmts)
        self.postprocess()

    def visit_FunctionDef(self, func_def: Union[FunctionDef, AsyncFunctionDef]):
        qualname = f"{self.scope.get_qualname()}.{func_def.name}"
        func = IRFunc(qualname, func_def, is_method=isinstance(self.scope, IRClass))
        trans = IRTransformer(func)
        trans.process_stmts(func_def.body)
        World().scope_manager.add_class(self.scope, func)
        World().scope_manager.set_ir(func, "ir", trans.get_stmts())
        self.emit(func)

    def visit_ClassDef(self, cls_def: ClassDef):
        qualname = f"{self.scope.get_qualname()}.{cls_def.name}"
        cls = IRClass(qualname, cls_def)
        trans = IRTransformer(cls)
        trans.process_stmts(cls_def.body)
        World().scope_manager.add_class(self.scope, cls)
        World().scope_manager.set_ir(cls, "ir", trans.get_stmts())
        self.emit(cls)

    def visit_AsyncFunctionDef(self, node: AsyncFunctionDef):
        self.visit_FunctionDef(node)

    def _visit_context_manager(self, node, is_async=False):
        def visit_item(index):
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
            frame = {"kind":"with", "entry":entry,
                     "loop_depth":len(self.breaks_stack), "outer_handlers":outer_handlers}
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
                saved_exception = self._save_pending_value(Name(id="@caught_except", ctx=Load()), node)
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

    def visit_With(self, node):
        self._visit_context_manager(node)

    def visit_AsyncWith(self, node):
        self._visit_context_manager(node, is_async=True)

    def visit_Break(self, node: Break):
        assert len(self.breaks_stack) > 0
        self._run_cleanups("break")
        stmt = Goto()
        self.breaks_stack[-1].append(stmt)
        self.emit(stmt)

    def visit_Continue(self, node:Continue):
        assert len(self.continues_stack) > 0
        self._run_cleanups("continue")
        stmt = Goto()
        self.continues_stack[-1].append(stmt)
        self.emit(stmt)

    def visit_Return(self, node: Return):
        if not self.cleanup_stack:
            self.emit(IRReturn(node))
            return
        value = self._save_pending_value(node.value, node)
        self._run_cleanups("return")
        terminal = Return(value=value)
        copy_location(terminal, node)
        fix_missing_locations(terminal)
        self.emit(IRReturn(terminal))

    def visit_Import(self, node: Import):
        stmt = IRImport(node)
        imports.append(stmt)
        self.emit(stmt)

    def visit_ImportFrom(self, node: ImportFrom):
        stmt = IRImport(node)
        imports.append(stmt)
        self.emit(stmt)
    
    def visit_Global(self, node: Global):
        for name in node.names:
            self.scope.add_global_var(name)
    
    def visit_Nonlocal(self, node: Nonlocal):
        for name in node.names:
            self.scope.add_nonlocal_var(name)

    def visit_While(self, node: While):
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

    def visit_If(self, node: If):
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

    def visit_Raise(self, node: Raise):
        stmt = IRRaise(node)
        self.emit(stmt)


    @staticmethod
    def _handler_names(handler):
        if handler.type is None:
            return None
        if isinstance(handler.type, Name):
            return (["@dynamic_exception_type"] if handler.type.id.startswith("$")
                    else [handler.type.id])
        if isinstance(handler.type, Tuple) and all(isinstance(value, Name) for value in handler.type.elts):
            return [value.id for value in handler.type.elts]
        # Dynamic types are kept as an explicit unknown match; a backend must
        # branch conservatively and retain an unresolved-handler obligation.
        return ["@dynamic_exception_type"]

    def _reraise(self, origin, saved_exception_var=None):
        node = Raise(exc=Name(id=saved_exception_var or "@caught_except", ctx=Load()), cause=None)
        copy_location(node, origin)
        fix_missing_locations(node)
        statement = IRRaise(node)
        statement.saved_exception_var = saved_exception_var
        return self.emit(statement)

    def visit_Try(self, node: Try):
        outer_handlers = tuple(self.exception_handlers)
        outer_cleanups = self.cleanup_stack
        label_try, label_try_end = self.label_gen.gen(), self.label_gen.gen()
        label_normal_finally, label_end = self.label_gen.gen(), self.label_gen.gen()
        label_exception_finally = self.label_gen.gen() if node.finalbody else None
        handler_labels = [self.label_gen.gen() for _ in node.handlers]
        handler_plans = [IRCatchException(self._handler_names(handler), label_try,
                                         label_try_end, label, handler)
                         for handler, label in zip(node.handlers, handler_labels)]
        finally_plan = (IRCatchException(None, label_try, label_try_end,
                                        label_exception_finally)
                        if node.finalbody else None)
        fallback_handlers = ((finally_plan,) if finally_plan else ()) + outer_handlers
        if node.finalbody:
            frame = {"kind": "finally", "body": node.finalbody,
                     "loop_depth": len(self.breaks_stack), "outer_handlers": outer_handlers}
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
            for handler, label in zip(node.handlers, handler_labels):
                self.exception_handlers = fallback_handlers
                self.emit(label)
                self.emit(IRPass())
                target_frame = None
                label_delete_exception = None
                if handler.name is not None:
                    assignment = Assign(targets=[Name(id=handler.name, ctx=Store())],
                                        value=Name(id="@caught_except", ctx=Load()))
                    copy_location(assignment, handler)
                    fix_missing_locations(assignment)
                    self.emit(IRCopy(assignment))
                    target_frame = {"kind":"except_delete", "name":handler.name,
                                    "origin":handler, "loop_depth":len(self.breaks_stack),
                                    "outer_handlers":fallback_handlers}
                    self.cleanup_stack = self.cleanup_stack + [target_frame]
                    label_delete_exception = self.label_gen.gen()
                    delete_plan = IRCatchException(None, label, label_delete_exception,
                                                  label_delete_exception)
                    self.exception_handlers = (delete_plan,) + fallback_handlers
                self.visit_stmts(handler.body)
                self.exception_handlers = fallback_handlers
                if target_frame is not None:
                    self.cleanup_stack = self.cleanup_stack[:-1]
                    self._delete_handler_target(handler.name, handler)
                self.emit(Goto(label_normal_finally))
                if label_delete_exception is not None:
                    self.emit(label_delete_exception)
                    self._delete_handler_target(handler.name, handler)
                    self._reraise(handler)

            # An exceptional entry runs cleanup with the saved exception.
            # A new return/raise/break/continue replaces it; otherwise reraise.
            self.cleanup_stack = outer_cleanups
            self.exception_handlers = outer_handlers
            if finally_plan:
                self.emit(finally_plan)
                self.emit(label_exception_finally)
                self.emit(IRPass())
                saved_exception = self._save_pending_value(Name(id="@caught_except", ctx=Load()), node)
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

    def visit_Assign(self, stmt: Assign):
        if isinstance(stmt.value, (Yield, YieldFrom)):
            self.emit(IRYield(stmt))
        elif isinstance(stmt.value, Await):
            self.emit(IRAwait(stmt))
        elif isinstance(stmt.value, Call):
            self.emit(IRCall(stmt))
        elif isinstance(stmt.targets[0], Subscript):
            self.emit(IRStoreSubscr(stmt))
        elif isinstance(stmt.value, Subscript):
            self.emit(IRLoadSubscr(stmt))
        elif isinstance(stmt.targets[0], Attribute):
            self.emit(IRStoreAttr(stmt))
        elif isinstance(stmt.value, Attribute):
            self.emit(IRLoadAttr(stmt))
        elif isinstance(stmt.value, Name):
            self.emit(IRCopy(stmt))
        else:
            self.emit(IRAssign(stmt))

    def visit_AnnAssign(self, node: AnnAssign):
        self.emit(IRAnno(node))

    def visit_Delete(self, node: Delete):
        self.emit(IRDel(node))

    def visit_Pass(self, node: Pass):
        self.emit(IRPass(node))

    def generic_visit(self, node):
        if isinstance(node, stmt):
            self.emit(IRAstStmt(node))
        else:
            super().generic_visit(node)
