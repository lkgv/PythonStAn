import ast
from ast import stmt
import copy
from lib2to3.pytree import Base
from typing import List, Dict

from .models import *


class CFGBuilder:
    cfg: ControlFlowGraph
    funcs: List[CFGFunc]
    classes: List[CFGClass]
    next_idx: int
    scope: Optional[CFGScope]

    def __init__(self, scope=None, next_idx=0, cfg=None):
        self.next_idx = next_idx
        self.cfg = cfg if cfg is not None else ControlFlowGraph()
        self.scope = scope
        self.cfg.set_scope(scope)
        self.funcs = []
        self.classes = []
    
    def set_scope(self, scope: CFGScope):
        self.scope = scope
        self.cfg.set_scope(scope)

    def new_blk(self) -> BaseBlock:
        blk = BaseBlock(self.next_idx, self.cfg)
        self.next_idx += 1
        return blk
    
    def build_module(self, stmts: List[stmt]) -> CFGModule:
        mod = CFGModule()
        builder = CFGBuilder(scope=mod)

        new_blk = builder.new_blk()
        edge = NormalEdge(builder.cfg.entry_blk, new_blk)
        builder.cfg.add_edge(edge)
        mod_info = builder._build(stmts, builder.cfg, new_blk)
        builder.cfg.add_exit(mod_info['last_block'])
        builder.cfg.add_super_exit_blk(builder.new_blk())

        mod.set_cfg(builder.cfg)
        mod.set_funcs(mod_info['func'])
        mod.set_classes(mod_info['class'])
        mod.set_imports(mod_info['import'])
        return mod

    def build_func(self, stmt, func_def) -> CFGFunc:
        func = CFGFunc(func_def, scope=self.scope)
        builder = CFGBuilder(scope=func)
        new_blk = builder.new_blk()
        edge = NormalEdge(builder.cfg.entry_blk, new_blk)
        builder.cfg.add_edge(edge)
        func_info = builder._build(stmt.body, builder.cfg, new_blk)
        for ret_blk, _ in func_info['return']:
            builder.cfg.add_exit(ret_blk)
        for yield_blk, _ in func_info['yield']:
            builder.cfg.add_exit(yield_blk)
        builder.cfg.add_exit(func_info['last_block'])
        builder.cfg.add_super_exit_blk(builder.new_blk())

        func.set_cfg(builder.cfg)
        func.set_funcs(func_info['func'])
        func.set_classes(func_info['class'])
        func.set_imports(func_info['import'])
        return func

    def build_class(self, stmt: ast.ClassDef,
                    cls_def: CFGClassDef) -> Tuple[CFGClass, Dict]:
        cls = CFGClass(cls_def, scope=self.scope)
        builder = CFGBuilder(scope=cls)
        new_blk = builder.new_blk()
        edge = NormalEdge(builder.cfg.entry_blk, new_blk)
        builder.cfg.add_edge(edge)
        cls_info = builder._build(stmt.body, builder.cfg, new_blk)
        builder.cfg.add_exit(cls_info['last_block'])
        builder.cfg.add_super_exit_blk(builder.new_blk())

        cls.set_cfg(builder.cfg)
        cls.set_funcs(cls_info['func'])
        cls.set_classes(cls_info['class'])
        cls.set_imports(cls_info['import'])
        return cls, cls_info

    def _build(self, stmts: List[stmt],
               cfg: ControlFlowGraph,
               cur_blk: BaseBlock
               ) -> Dict[str, List]:
        
        exit_stmt_list = [
            'break', 'continue', 'return', 'yield', 'raise',
            'func', 'class', 'import']
        exit_stmt = { k:[] for k in exit_stmt_list }
        n_stmt = len(stmts)

        def gen_next_blk(cur_blk, edge_builder):
            if cur_blk.n_stmt() > 0 and i < cur_blk.n_stmt() - 1:
                new_blk = self.new_blk()
                edge = edge_builder(cur_blk, new_blk)
                cfg.add_blk(new_blk)
                cfg.add_edge(edge)
                return new_blk
            else:
                return cur_blk
        
        def extend_info(info, exclude=[],
                        include=exit_stmt_list):
            for key in exit_stmt.keys():
                if key not in exclude and key in include:
                    exit_stmt[key].extend(info[key])
        
        for i, stmt in enumerate(stmts):
            if isinstance(stmt, ast.FunctionDef):
                func_def = CFGFuncDef(stmt)
                cfg.add_stmt(cur_blk, func_def.to_ast())
                func = self.build_func(stmt, func_def)
                exit_stmt['func'].append(func)
            
            elif isinstance(stmt, ast.AsyncFunctionDef):
                func_def = CFGAsyncFuncDef(stmt)
                cfg.add_stmt(cur_blk, func_def.to_ast())
                func = self.build_func(stmt, func_def)
                exit_stmt['func'].append(func)

            elif isinstance(stmt, ast.ClassDef):
                cls_def = CFGClassDef(stmt)
                cfg.add_stmt(cur_blk, cls_def.to_ast())
                cls, build_info = self.build_class(stmt, cls_def)
                exit_stmt['class'].append(cls)
                # extend_info(build_info, include=['raise'])
                
            elif isinstance(stmt, ast.Break):
                cfg.add_stmt(cur_blk, stmt)
                exit_stmt['break'].append((cur_blk, stmt))
                cur_blk = gen_next_blk(cur_blk, NormalEdge)

            elif isinstance(stmt, ast.Continue):
                cfg.add_stmt(cur_blk, stmt)
                exit_stmt['continue'].append((cur_blk, stmt))
                cur_blk = gen_next_blk(cur_blk, NormalEdge)

            elif isinstance(stmt, ast.Return):
                cfg.add_stmt(cur_blk, stmt)
                exit_stmt['return'].append((cur_blk, stmt))
                cur_blk = gen_next_blk(cur_blk, NormalEdge)
            
            elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
                cfg.add_stmt(cur_blk, stmt)
                exit_stmt['import'].append((cur_blk, CFGImport(stmt)))

            elif isinstance(stmt, ast.For):
                new_stmt = ast.For(target=stmt.target,
                                   iter=stmt.iter,
                                   body=[],
                                   orelse=[],
                                   type_comment=stmt.type_comment)
                ast.copy_location(new_stmt, stmt)
                cur_blk = gen_next_blk(cur_blk, NormalEdge)
                cfg.add_stmt(cur_blk, new_stmt)
                loop_blk = gen_next_blk(cur_blk, ForEdge)
                loop_info = self._build(stmt.body, cfg, loop_blk)
                extend_info(loop_info, exclude=['break', 'continue'])
                next_blk = self.new_blk()
                for blk, _ in loop_info['break']:
                    cfg.add_edge(NormalEdge(blk, next_blk))
                for blk, _ in loop_info['continue']:
                    cfg.add_edge(NormalEdge(blk, cur_blk))
                cfg.add_edge(NormalEdge(loop_info['last_block'], cur_blk))
                if len(stmt.orelse) > 0:
                    else_blk = gen_next_blk(cur_blk, ForElseEdge)
                    else_info = self._build(stmt.orelse, cfg, else_blk)
                    extend_info(else_info)
                    cfg.add_edge(NormalEdge(else_info['last_block'], next_blk))
                else:
                    cfg.add_edge(NormalEdge(cur_blk, next_blk))
                cur_blk = next_blk
                
            elif isinstance(stmt, ast.AsyncFor):
                new_stmt = ast.AsyncFor(target=stmt.target,
                                       iter=stmt.iter,
                                        body=[],
                                        orelse=[],
                                        type_comment=stmt.type_comment)
                ast.copy_location(new_stmt, stmt)
                cur_blk = gen_next_blk(cur_blk, NormalEdge)
                cfg.add_stmt(cur_blk, new_stmt)
                loop_blk = gen_next_blk(cur_blk, ForEdge)
                loop_info = self._build(stmt.body, cfg, loop_blk)
                extend_info(loop_info, exclude=['break', 'continue'])
                next_blk = self.new_blk()
                for blk, _ in loop_info['break']:
                    cfg.add_edge(NormalEdge(blk, next_blk))
                for blk, _ in loop_info['continue']:
                    cfg.add_edge(NormalEdge(blk, cur_blk))
                cfg.add_edge(NormalEdge(loop_info['last_block'], cur_blk))
                if len(stmt.orelse) > 0:
                    else_blk = gen_next_blk(cur_blk, ForElseEdge)
                    else_info = self._build(stmt.orelse, cfg, else_blk)
                    extend_info(else_info)
                    cfg.add_edge(NormalEdge(else_info['last_block'], next_blk))
                else:
                    cfg.add_edge(NormalEdge(cur_blk, next_blk))
                cur_blk = next_blk

            elif isinstance(stmt, ast.While):
                new_stmt = ast.While(test=stmt.test, body=[], orelse=[])
                ast.copy_location(new_stmt, stmt)
                cur_blk = gen_next_blk(cur_blk, NormalEdge)
                cfg.add_stmt(cur_blk, new_stmt)
                loop_blk = gen_next_blk(cur_blk, WhileEdge)
                loop_info = self._build(stmt.body, cfg, loop_blk)
                extend_info(loop_info, exclude=['break', 'continue'])
                next_blk = self.new_blk()
                for blk, _ in loop_info['break']:
                    cfg.add_edge(NormalEdge(blk, next_blk))
                for blk, _ in loop_info['continue']:
                    cfg.add_edge(NormalEdge(blk, cur_blk))
                cfg.add_edge(NormalEdge(loop_info['last_block'], cur_blk))
                if len(stmt.orelse) > 0:
                    else_blk = gen_next_blk(cur_blk, WhileElseEdge)
                    else_info = self._build(stmt.orelse, cfg, else_blk)
                    extend_info(else_info)
                    cfg.add_edge(NormalEdge(else_info['last_block'], next_blk))
                else:
                    cfg.add_edge(NormalEdge(cur_blk, next_blk))
                cur_blk = next_blk

            elif isinstance(stmt, ast.If):
                new_stmt = ast.If(test=stmt.test, body=[], orelse=[])
                ast.copy_location(new_stmt, stmt)
                cfg.add_stmt(cur_blk, new_stmt)
                then_blk = gen_next_blk(cur_blk,
                                        lambda u, v: IfEdge(u, v, True))
                then_info = self._build(stmt.body, cfg, then_blk)
                extend_info(then_info)
                next_blk = self.new_blk()
                cfg.add_edge(NormalEdge(then_info['last_block'], next_blk))
                if len(stmt.orelse) > 0:
                    else_blk = gen_next_blk(cur_blk,
                                            lambda u, v: IfEdge(u, v, False))
                    else_info = self._build(stmt.orelse, cfg, else_blk)
                    extend_info(else_info)
                    cfg.add_edge(NormalEdge(else_info['last_block'], next_blk))
                cur_blk = next_blk
                
            elif isinstance(stmt, ast.With):
                new_stmt = ast.With(items=stmt.items, body=[],
                                    type_comment=stmt.type_comment)
                ast.copy_location(new_stmt, stmt)
                cfg.add_stmt(cur_blk, new_stmt)
                var = stmt.items[0].optional_vars
                with_blk = gen_next_blk(cur_blk,
                                        lambda u, v: WithEdge(u, v, var))
                with_info = self._build(stmt.body, cfg, with_blk)
                extend_info(with_info)
                cur_blk = gen_next_blk(with_info['last_block'],
                                       lambda u, v: WithEndEdge(u, v, var))

            elif isinstance(stmt, ast.AsyncWith):
                new_stmt = ast.AsyncWith(items=stmt.items, body=[],
                                    type_comment=stmt.type_comment)
                ast.copy_location(new_stmt, stmt)
                cfg.add_stmt(cur_blk, new_stmt)
                var = stmt.items[0].optional_vars
                with_blk = gen_next_blk(cur_blk,
                                        lambda u, v: WithEdge(u, v, var))
                with_info = self._build(stmt.body, cfg, with_blk)
                extend_info(with_info)
                cur_blk = gen_next_blk(with_info['last_block'],
                                       lambda u, v: WithEndEdge(u, v, var))
                
            elif isinstance(stmt, ast.Raise):
                exit_stmt['raise'].append((cur_blk, stmt))
                cfg.add_stmt(cur_blk, stmt)
                cur_blk = gen_next_blk(cur_blk, NormalEdge)

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
                cfg.add_stmt(cur_blk, new_stmt)
                try_blk = gen_next_blk(cur_blk, NormalEdge)
                try_info = self._build(stmt.body, cfg, try_blk)
                extend_info(try_info)
                next_blk = self.new_blk()
                has_final = (len(stmt.finalbody) > 0)
                if len(stmt.orelse) > 0:
                    else_blk = gen_next_blk(try_info['last_block'], NormalEdge)
                    else_info = self._build(stmt.orelse, cfg, else_blk)
                    extend_info(else_info)
                    cfg.add_edge(NormalEdge(else_info['last_block'], next_blk))
                else:
                    cfg.add_edge(NormalEdge(try_info['last_block'], next_blk))
                for expt in stmt.handlers:
                    e_blk = gen_next_blk(try_info['last_block'],
                                         lambda u, v: ExceptionEdge(u, v, expt))
                    e_info = self._build(expt.body, cfg, e_blk)
                    extend_info(e_info)
                    cfg.add_edge(ExceptionEndEdge(e_info['last_block'], next_blk, expt))
                if has_final:
                    final_info = self._build(stmt.finalbody, cfg, next_blk)
                    extend_info(final_info)
                    next_blk = gen_next_blk(final_info['last_block'], NormalEdge)
                cur_blk = next_blk

            else:
                if isinstance(stmt, ast.Assign) and \
                    isinstance(stmt.value, (ast.Yield, ast.YieldFrom)):
                    exit_stmt['yield'].append((cur_blk, stmt))
                    cfg.add_stmt(cur_blk, stmt)
                    cur_blk = gen_next_blk(cur_blk, NormalEdge)

                elif isinstance(stmt, ast.Assign) and \
                    isinstance(stmt.value, ast.Call):
                    cur_blk = gen_next_blk(cur_blk, NormalEdge)
                    cfg.add_stmt(cur_blk, stmt)
                    cur_blk = gen_next_blk(cur_blk, NormalEdge)

                else:
                    cfg.add_stmt(cur_blk, stmt)
        
        ret_info = exit_stmt
        ret_info['last_block'] = cur_blk
        return ret_info


class StmtCFGTransformer:
    scope: CFGScope

    # map a bblock in source cfg to the head block in the target cfg
    head_map: Dict[BaseBlock, BaseBlock]
    next_idx: int

    def trans(self, scope: CFGScope):
        self.scope = copy.deepcopy(scope)
        entry = scope.cfg.entry_blk
        self.scope.set_cfg(ControlFlowGraph(scope=self.scope))
        self.next_idx = 0
        self.visited = {}
        self.head_map = {scope.cfg.entry_blk: self.scope.cfg.entry_blk}
        self._trans(scope.cfg, entry, self.scope.cfg.entry_blk)
        self.scope.set_funcs([
            StmtCFGTransformer().trans(f) for f in scope.funcs])
        self.scope.set_classes(
            [StmtCFGTransformer().trans(c) for c in scope.classes])
        return self.scope
    
    def new_blk(self, statement: Optional[stmt]=None):
        if statement is not None:
            stmts = [statement]
        else:
            stmts = []
        blk = BaseBlock(self.next_idx, self.scope.cfg, stmts)
        self.next_idx += 1
        return blk

    def _trans(self, cfg: ControlFlowGraph, entry: BaseBlock, stmt_entry: BaseBlock):
        tgt_cfg = self.scope.cfg
        for e in cfg.out_edges_of(entry):
            stmt_e = copy.deepcopy(e)
            stmt_e.start = stmt_entry
            if e.end in self.head_map:
                stmt_e.end = self.head_map[e.end]
                tgt_cfg.add_edge(stmt_e)
            else:
                tail_blk = stmt_entry
                if e.end.n_stmt() > 0:
                    for idx, cur_stmt in enumerate(e.end.stmts):
                        blk = self.new_blk(cur_stmt)
                        if idx == 0:
                            head_blk = blk
                        tgt_cfg.add_blk(blk)
                        tgt_cfg.add_edge(NormalEdge(tail_blk, blk))
                        tail_blk = blk
                else:
                    blk = self.new_blk()
                    tgt_cfg.add_blk(blk)
                    head_blk = blk
                    tail_blk = blk
                self.head_map[e.end] = head_blk
                stmt_e.end = head_blk
                tgt_cfg.add_edge(stmt_e)
                self._trans(cfg, e.end, tail_blk)
