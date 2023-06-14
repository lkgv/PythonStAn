import ast
from ast import stmt as Statement
from queue import Queue
import copy
from typing import List, Dict, Optional, Tuple

from .base_block import BaseBlock
from .edges import *
from .cfg import ControlFlowGraph
from pythonstan.ir import *


class CFGBuilder:
    cfg: ControlFlowGraph
    funcs: List[IRFunc]
    classes: List[IRClass]
    next_idx: int
    scope: Optional[IRScope]

    def __init__(self, scope=None, next_idx=1, cfg=None):
        self.next_idx = next_idx
        self.cfg = cfg if cfg is not None else ControlFlowGraph()
        self.scope = scope
        self.funcs = []
        self.classes = []

    def set_scope(self, scope: IRScope):
        self.scope = scope

    def new_blk(self) -> BaseBlock:
        blk = BaseBlock(self.next_idx)
        self.next_idx += 1
        return blk

    def build_module(self, mod_def: IRModule) -> IRModule:
        builder = CFGBuilder(scope=mod_def)

        new_blk = builder.new_blk()
        edge = NormalEdge(builder.cfg.entry_blk, new_blk)
        builder.cfg.add_edge(edge)
        mod_info = builder._build(mod_def.ast.body, builder.cfg, new_blk)
        builder.cfg.add_exit(mod_info['last_block'])
        builder.cfg.add_super_exit_blk(builder.new_blk())

        mod_def.set_cfg(builder.cfg)
        mod.set_funcs(mod_info['func'])
        mod.set_classes(mod_info['class'])
        mod.set_imports(mod_info['import'])
        return mod_def

    def build_func(self, func_def: IRFunc) -> IRFunc:
        builder = CFGBuilder(scope=func_def)
        new_blk = builder.new_blk()
        edge = NormalEdge(builder.cfg.entry_blk, new_blk)
        builder.cfg.add_edge(edge)
        func_info = builder._build(stmt.body, builder.cfg, new_blk)
        for ret_blk, _ in func_info['return']:
            builder.cfg.add_exit(ret_blk)
        for yield_blk, _ in func_info['yield']:
            builder.cfg.add_exit(yield_blk)
        for raise_blk, _ in func_info['raise']:
            builder.cfg.add_exit(raise_blk)
        builder.cfg.add_exit(func_info['last_block'])
        builder.cfg.add_super_exit_blk(builder.new_blk())

        func.set_cfg(builder.cfg)
        func.set_funcs(func_info['func'])
        func.set_classes(func_info['class'])
        func.set_imports(func_info['import'])
        return func

    def build_class(self, cls: IRClass) -> Tuple[IRClass, Dict]:
        builder = CFGBuilder(scope=cls)
        new_blk = builder.new_blk()
        edge = NormalEdge(builder.cfg.entry_blk, new_blk)
        builder.cfg.add_edge(edge)
        cls_info = builder._build(cls.ast.body, builder.cfg, new_blk)
        builder.cfg.add_exit(cls_info['last_block'])
        builder.cfg.add_super_exit_blk(builder.new_blk())

        cls.set_cfg(builder.cfg)
        cls.set_funcs(cls_info['func'])
        cls.set_classes(cls_info['class'])
        cls.set_imports(cls_info['import'])
        return cls, cls_info

    def _build(self, stmts: List[Statement],
               cfg: ControlFlowGraph,
               cur_blk: BaseBlock
               ) -> Dict[str, List]:

        exit_stmt_list = [
            'break', 'continue', 'return', 'yield', 'raise',
            'func', 'class', 'import']
        exit_stmt = {k: [] for k in exit_stmt_list}
        n_stmt = len(stmts)

        def gen_next_blk(idx, bblk, edge_builder, force_next=False):
            if bblk.n_stmt() > 0 and (force_next or idx < len(stmts) - 1):
                new_blk = self.new_blk()
                edge = edge_builder(bblk, new_blk)
                cfg.add_blk(new_blk)
                cfg.add_edge(edge)
                return new_blk
            else:
                return bblk

        def extend_info(info, exclude=None, include=None):
            if include is None:
                include = exit_stmt_list
            if exclude is None:
                exclude = []
            for key in exit_stmt.keys():
                if key not in exclude and key in include:
                    exit_stmt[key].extend(info[key])

        for i, stmt in enumerate(stmts):
            if isinstance(stmt, ast.FunctionDef):
                func_def = IRFuncDef(stmt)
                cfg.add_stmt(cur_blk, func_def)
                func = self.build_func(stmt, func_def)
                exit_stmt['func'].append(func)

            elif isinstance(stmt, ast.AsyncFunctionDef):
                func_def = CFGAsyncFuncDef(stmt)
                cfg.add_stmt(cur_blk, func_def)
                func = self.build_func(stmt, func_def)
                exit_stmt['func'].append(func)

            elif isinstance(stmt, ast.ClassDef):
                cls_def = IRClassDef(stmt)
                cfg.add_stmt(cur_blk, cls_def)
                cls, build_info = self.build_class(stmt, cls_def)
                exit_stmt['class'].append(cls)
                # extend_info(build_info, include=['raise'])

            elif isinstance(stmt, ast.Break):
                goto = Goto()
                cfg.add_stmt(cur_blk, goto)
                exit_stmt['break'].append((cur_blk, goto))
                cur_blk = self.new_blk()
                self.cfg.add_blk(cur_blk)

            elif isinstance(stmt, ast.Continue):
                goto = Goto()
                cfg.add_stmt(cur_blk, goto)
                exit_stmt['continue'].append((cur_blk, goto))
                cur_blk = self.new_blk()
                self.cfg.add_blk(cur_blk)

            elif isinstance(stmt, ast.Return):
                cfg.add_stmt(cur_blk, IRAstStmt(stmt))
                exit_stmt['return'].append((cur_blk, stmt))
                cur_blk = self.new_blk()
                self.cfg.add_blk(cur_blk)

            elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
                cfg.add_stmt(cur_blk, IRAstStmt(stmt))
                exit_stmt['import'].append((cur_blk, IRImport(stmt)))

            elif isinstance(stmt, ast.While):
                new_stmt = JumpIfFalse(test=stmt.test)
                cur_blk = gen_next_blk(i, cur_blk, NormalEdge, True)
                cfg.add_stmt(cur_blk, new_stmt)
                cur_label = cur_blk.gen_label()
                loop_blk = gen_next_blk(i, cur_blk, WhileEdge, True)
                loop_info = self._build(stmt.body, cfg, loop_blk)
                extend_info(loop_info, exclude=['break', 'continue'])
                next_blk = self.new_blk()
                next_label = next_blk.gen_label()
                for blk, break_stmt in loop_info['break']:
                    cfg.add_edge(NormalEdge(blk, next_blk))
                    break_stmt.set_label(next_label)
                for blk, continue_stmt in loop_info['continue']:
                    cfg.add_edge(NormalEdge(blk, cur_blk))
                    continue_stmt.set_label(cur_label)
                cfg.add_edge(NormalEdge(loop_info['last_block'], cur_blk))
                if len(stmt.orelse) > 0:
                    else_blk = gen_next_blk(i, cur_blk, WhileElseEdge)
                    else_info = self._build(stmt.orelse, cfg, else_blk)
                    extend_info(else_info)
                    cfg.add_edge(NormalEdge(else_info['last_block'], next_blk))
                    back_goto = Goto(cur_label)
                    cfg.add_stmt(else_info['last_block'], back_goto)
                    new_stmt.set_label(else_blk.gen_label())
                else:
                    cfg.add_edge(NormalEdge(cur_blk, next_blk))
                    new_stmt.set_label(next_label)
                cur_blk = next_blk

            elif isinstance(stmt, ast.If):
                if_false_jump = JumpIfFalse(test=stmt.test)
                cfg.add_stmt(cur_blk, if_false_jump)
                then_blk = gen_next_blk(i, cur_blk,
                                        lambda u, v: IfEdge(u, v, True),
                                        True)
                then_info = self._build(stmt.body, cfg, then_blk)
                extend_info(then_info)
                next_blk = self.new_blk()
                cfg.add_edge(NormalEdge(then_info['last_block'], next_blk))
                if len(stmt.orelse) > 0:
                    else_blk = gen_next_blk(i, cur_blk,
                                            lambda u, v: IfEdge(u, v, False),
                                            True)
                    else_info = self._build(stmt.orelse, cfg, else_blk)
                    extend_info(else_info)
                    cfg.add_edge(NormalEdge(else_info['last_block'], next_blk))
                    next_label = next_blk.gen_label()
                    then_end_goto = Goto(next_label)
                    cfg.add_stmt(then_info['last_block'],then_end_goto)
                    label = else_blk.gen_label()
                else:
                    cfg.add_edge(IfEdge(cur_blk, next_blk, False))
                    label = next_blk.gen_label()
                if_false_jump.set_label(label)
                cur_blk = next_blk

            elif isinstance(stmt, ast.With):
                new_stmt = ast.With(items=stmt.items, body=[],
                                    type_comment=stmt.type_comment)
                ast.copy_location(new_stmt, stmt)
                cfg.add_stmt(cur_blk, IRAstStmt(new_stmt))
                var = stmt.items[0].optional_vars
                with_blk = gen_next_blk(i, cur_blk,
                                        lambda u, v: WithEdge(u, v, var),
                                        True)
                with_info = self._build(stmt.body, cfg, with_blk)
                extend_info(with_info)
                cur_blk = gen_next_blk(i, with_info['last_block'],
                                       lambda u, v: WithEndEdge(u, v, var))

            elif isinstance(stmt, ast.AsyncWith):
                new_stmt = ast.AsyncWith(items=stmt.items, body=[],
                                         type_comment=stmt.type_comment)
                ast.copy_location(new_stmt, stmt)
                cfg.add_stmt(cur_blk, IRAstStmt(new_stmt))
                var = stmt.items[0].optional_vars
                with_blk = gen_next_blk(i, cur_blk,
                                        lambda u, v: WithEdge(u, v, var),
                                        True)
                with_info = self._build(stmt.body, cfg, with_blk)
                extend_info(with_info)
                cur_blk = gen_next_blk(i, with_info['last_block'],
                                       lambda u, v: WithEndEdge(u, v, var))

            elif isinstance(stmt, ast.Raise):
                exit_stmt['raise'].append((cur_blk, stmt))
                cfg.add_stmt(cur_blk, IRAstStmt(stmt))
                cur_blk = self.new_blk()
                self.cfg.add_blk(cur_blk)

            elif isinstance(stmt, ast.Try):
                new_handlers = []
                for h in stmt.handlers:
                    new_h = ast.ExceptHandler(type=h.type,
                                              name=h.name, body=[])
                    ast.copy_location(new_h, h)
                    new_handlers.append(new_h)
                new_stmt = ast.Try(body=[],
                                   handlers=new_handlers,
                                   orelse=[],
                                   finalbody=[])
                ast.copy_location(new_stmt, stmt)
                cfg.add_stmt(cur_blk, IRAstStmt(new_stmt))
                try_blk = gen_next_blk(i, cur_blk, NormalEdge, True)
                try_info = self._build(stmt.body, cfg, try_blk)
                extend_info(try_info)
                next_blk = self.new_blk()
                has_final = (len(stmt.finalbody) > 0)
                if len(stmt.orelse) > 0:
                    else_blk = gen_next_blk(i, try_info['last_block'], NormalEdge, True)
                    else_info = self._build(stmt.orelse, cfg, else_blk)
                    extend_info(else_info)
                    cfg.add_edge(NormalEdge(else_info['last_block'], next_blk))
                else:
                    cfg.add_edge(NormalEdge(try_info['last_block'], next_blk))
                for new_expt, expt in zip(new_handlers, stmt.handlers):
                    e_blk = gen_next_blk(i, try_info['last_block'],
                                         lambda u, v: ExceptionEdge(u, v, new_expt),
                                         force_next=True)
                    e_info = self._build(expt.body, cfg, e_blk)
                    extend_info(e_info)
                    cfg.add_edge(ExceptionEndEdge(e_info['last_block'], next_blk, new_expt))
                if has_final:
                    final_info = self._build(stmt.finalbody, cfg, next_blk)
                    extend_info(final_info)
                    next_blk = gen_next_blk(i, final_info['last_block'], NormalEdge)
                cur_blk = next_blk

            else:
                if isinstance(stmt, ast.Assign) and \
                        isinstance(stmt.value, (ast.Yield, ast.YieldFrom)):
                    exit_stmt['yield'].append((cur_blk, stmt))
                    cfg.add_stmt(cur_blk, IRAstStmt(stmt))
                    cur_blk = gen_next_blk(i, cur_blk, NormalEdge)

                elif isinstance(stmt, ast.Assign) and \
                        isinstance(stmt.value, ast.Call):
                    cur_blk = gen_next_blk(i, cur_blk, NormalEdge, True)
                    cfg.add_stmt(cur_blk, IRAstStmt(stmt))
                    cur_blk = gen_next_blk(i, cur_blk, NormalEdge)

                else:
                    cfg.add_stmt(cur_blk, IRAstStmt(stmt))

        ret_info = exit_stmt
        ret_info['last_block'] = cur_blk
        return ret_info

    def add_label(self, cfg, entry):
        visited = {*()}
        q = Queue()
        q.put(entry)
        while not q.empty():
            cur = q.get()
            if cfg.in_degree_of(cur) > 1:
                lab = Label(cur)
                cur.stmts.insert(0, lab)



class StmtCFGTransformer:
    scope: IRScope

    # map a bblock in source cfg to the head block in the target cfg
    head_map: Dict[BaseBlock, BaseBlock]
    next_idx: int

    def trans(self, scope: IRScope):
        self.scope = copy.deepcopy(scope)
        entry = scope.cfg.entry_blk
        self.scope.set_cfg(ControlFlowGraph())
        self.next_idx = 1
        self.head_map = {scope.cfg.entry_blk: self.scope.cfg.entry_blk}
        self._trans(scope.cfg, entry, self.scope.cfg.entry_blk)
        super_exit = self.new_blk()
        self.scope.cfg.add_super_exit_blk(super_exit)
        self.scope.set_funcs([
            StmtCFGTransformer().trans_func(f) for f in scope.funcs])
        self.scope.set_classes(
            [StmtCFGTransformer().trans_cls(c) for c in scope.classes])
        return self.scope

    def trans_func(self, func: IRFunc) -> IRFunc:
        ret = self.trans(func)
        assert isinstance(ret, IRFunc)
        return ret

    def trans_cls(self, func: IRClass) -> IRClass:
        ret = self.trans(func)
        assert isinstance(ret, IRClass)
        return ret

    def new_blk(self, statement: Optional[IRStatement] = None):
        if statement is not None:
            stmts = [statement]
        else:
            stmts = []
        blk = BaseBlock(self.next_idx, stmts)
        self.next_idx += 1
        return blk

    def _trans(self, cfg: ControlFlowGraph, entry: BaseBlock, stmt_entry: BaseBlock):
        tgt_cfg = self.scope.cfg
        if entry in cfg.exit_blks:
            tgt_cfg.add_exit(stmt_entry)
        for e in cfg.out_edges_of(entry):
            if e.tgt == cfg.super_exit_blk:
                continue
            stmt_e = copy.deepcopy(e)
            stmt_e.src = stmt_entry
            if e.tgt in self.head_map:
                stmt_e.tgt = self.head_map[e.tgt]
                tgt_cfg.add_edge(stmt_e)
            else:
                head_blk = stmt_entry
                tail_blk = stmt_entry
                if e.tgt.n_stmt() > 0:
                    for idx, cur_stmt in enumerate(e.tgt.stmts):
                        blk = self.new_blk(cur_stmt)
                        tgt_cfg.add_blk(blk)
                        if idx == 0:
                            head_blk = blk
                        else:
                            tgt_cfg.add_edge(NormalEdge(tail_blk, blk))
                        tail_blk = blk
                else:
                    blk = self.new_blk()
                    tgt_cfg.add_blk(blk)
                    head_blk = blk
                    tail_blk = blk
                self.head_map[e.tgt] = head_blk
                stmt_e.tgt = head_blk
                tgt_cfg.add_edge(stmt_e)
                self._trans(cfg, e.tgt, tail_blk)
