from .models import *
import ast
from ast import stmt
from typing import List, Dict

class CFGBuilder:
    cfg: ControlFlowGraph
    funcs: List[CFGFunc]
    classes: List[CFGClass]
    next_idx: int
    scope: Optional[Union[CFGFunc, CFGClass]]

    def __init__(self, scope=None, next_idx=0, cfg=None):
        self.next_idx = next_idx
        self.cfg = cfg if cfg is not None else ControlFlowGraph()
        self.scope = scope
        self.cfg.set_scope(scope)
        self.funcs = []
        self.classes = []
    
    def set_scope(self, scope):
        self.scope = scope
        self.cfg.set_scope(scope)

    def from_stmts(self, stmts: List[stmt]) -> ControlFlowGraph:
        next_blk = self._new_blk()
        edge = NormalEdge(self.cfg.entry_blk, next_blk)
        self.cfg.add_edge(edge)
        self._build(stmts, self.cfg, next_blk)

    def _new_blk(self) -> BaseBlock:
        blk = BaseBlock(self.next_idx, self.cfg)
        self.next_idx += 1
        return blk

    def build_func(self, stmt, cur_blk) -> CFGFunc:
        func = CFGFunc(stmt, scope=self.scope)

        builder = CFGBuilder(func, scope=func)
        func_info = builder._build(stmt.body,
                                   builder.cfg, builder.cfg.entry_blk)
        for ret_blk, _ in func_info['return']:
            builder.cfg.add_exit(ret_blk)
        for yield_blk, _ in func_info['yield']:
            builder.cfg.add_exit(yield_blk)

        func.set_cfg(builder.cfg)
        func.set_funcs(func_info['func'])
        func.set_classes(func_info['class'])
        return func

    def build_class(self, stmt, cur_blk, cfg: ControlFlowGraph) -> Tuple[CFGClass, Dict]:
        cls = CFGClass(stmt, scope=self.scope)

        scope_bak = self.scope
        self.set_scope(cls)
        cls_blk = self._new_blk()
        cls_info = self._build(stmt.body, cfg, cls_blk)
        self.set_scope(scope_bak)

        next_blk = self._new_blk()
        enter_edge = ClassDefEdge(cur_blk, cls_blk, cls)
        exit_edge = ClassEndEdge(cls_info['last_block'], next_blk, cls)
        cfg.add_edge(enter_edge)
        cfg.add_edge(exit_edge)

        cls.set_funcs(cls_info['func'])
        cls.set_classes(cls_info['class'])
        return cls, cls_info

    def _build(self, stmts: List[stmt],
               cfg: ControlFlowGraph,
               cur_blk: BaseBlock
               ) -> Dict[str, List]:
        exit_stmt_list = [
            'break', 'continue', 'return', 'yield', 'raise', 'func', 'class']
        exit_stmt = { k:[] for k in exit_stmt_list }
        n_stmt = len(stmts)

        def gen_next_blk(cur_blk, edge_builder):
            if cur_blk.n_stmt > 0 and i < n_stmt - 1:
                new_blk = self._new_blk()
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
                func, build_info = self.build_func(stmt)
                exit_stmt['func'].append(func)
            
            elif isinstance(stmt, ast.AsyncFunctionDef):
                func, build_info = self.build_func(stmt)
                exit_stmt['func'].append(func)

            elif isinstance(stmt, ast.ClassDef):
                cls, build_info = self.build_class(stmt, cur_blk)
                exit_stmt['class'].append(cls)
                extend_info(build_info, include=['raise'])
                cur_blk = build_info['last_block']      
                
            elif isinstance(stmt, ast.Break):
                cur_blk.add(stmt)
                exit_stmt['break'].append((cur_blk, stmt))
                cur_blk = gen_next_blk(cur_blk, NormalEdge)

            elif isinstance(stmt, ast.Continue):
                cur_blk.add(stmt)
                exit_stmt['continue'].append((cur_blk, stmt))
                cur_blk = gen_next_blk(cur_blk, NormalEdge)

            elif isinstance(stmt, ast.Return):
                cur_blk.add(stmt)
                exit_stmt['return'].append((cur_blk, stmt))
                cur_blk = gen_next_blk(cur_blk, NormalEdge)

            elif isinstance(stmt, ast.For):
                new_stmt = ast.For(target=stmt.target,
                                   iter=stmt.iter,
                                   body=[],
                                   orelse=[],
                                   type_comment=stmt.type_comment)
                ast.copy_location(new_stmt, stmt)
                cur_blk = gen_next_blk(cur_blk, new_stmt, NormalEdge)
                cur_blk.add(new_stmt)
                loop_blk = gen_next_blk(cur_blk, ForEdge)
                loop_info = self._build(stmt.body, cfg, loop_blk)
                extend_info(loop_info, exclude=['break', 'continue'])
                next_blk = self._new_blk()
                for blk, _ in loop_info['break']:
                    cfg.add_edge(NormalEdge(blk, next_blk))
                for blk, _ in loop_blk['continue']:
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
                cur_blk = gen_next_blk(cur_blk, new_stmt, NormalEdge)
                cur_blk.add(new_stmt)
                loop_blk = gen_next_blk(cur_blk, ForEdge)
                loop_info = self._build(stmt.body, cfg, loop_blk)
                extend_info(loop_info, exclude=['break', 'continue'])
                next_blk = self._new_blk()
                for blk, _ in loop_info['break']:
                    cfg.add_edge(NormalEdge(blk, next_blk))
                for blk, _ in loop_blk['continue']:
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
                cur_blk = gen_next_blk(cur_blk, new_stmt, NormalEdge)
                cur_blk.add(new_stmt)
                loop_blk = gen_next_blk(cur_blk, WhileEdge)
                loop_info = self._build(stmt.body, cfg, loop_blk)
                extend_info(loop_info, exclude=['break', 'continue'])
                next_blk = self._new_blk()
                for blk, _ in loop_info['break']:
                    cfg.add_edge(NormalEdge(blk, next_blk))
                for blk, _ in loop_blk['continue']:
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
                cur_blk.add(new_stmt)
                then_blk = gen_next_blk(cur_blk,
                                        lambda u, v: IfEdge(u, v, True))
                then_info = self._build(stmt.body, cfg, then_blk)
                extend_info(then_info)
                next_blk = self._new_blk()
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
                var = stmt.items[0].optional_vars
                with_blk = gen_next_blk(cur_blk,
                                        lambda u, v: WithEdge(u, v, var))
                with_info = self._build(stmt.body, cfg, with_blk)
                extend_info(with_info)
                cur_blk = gen_next_blk(with_info['last_block'],
                                       lambda u, v: WithEndEdge(u, v, var))
                
            elif isinstance(stmt, ast.Raise):
                exit_stmt['raise'].append((cur_blk, stmt))
                cur_blk.add(stmt)
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
                cur_blk.add(new_stmt)
                try_blk = gen_next_blk(cur_blk, NormalEdge)
                try_info = self._build(stmt.body, cfg, try_blk)
                extend_info(try_info)
                next_blk = self._new_blk()
                if len(stmt.orelse) > 0:
                    else_blk = gen_next_blk(try_info['last_block'], NormalEdge)
                    else_info = self._build(stmt.orelse, cfg, else_blk)
                    extend_info(else_info)
                    cfg.add_edge(NormalEdge(else_info['last_block'], next_blk))
                for expt in stmt.handlers:
                    e_blk = gen_next_blk(try_info['last_block'],
                                         lambda u, v: Exception(u, v, expt))
                    e_info = self._build(expt.body, cfg, e_blk)
                    extend_info(else_info)
                    cfg.add_edge(ExceptionEndEdge(e_info['last_block'], next_blk, expt))
                if len(stmt.finalbody) > 0:
                    final_info = self._build(stmt.finalbody, cfg, next_blk)
                    extend_info(final_info)
                    next_blk = gen_next_blk(final_info['last_block'], NormalEdge)
                cur_blk = next_blk

            else:
                if isinstance(stmt, ast.Assign) and \
                    isinstance(stmt.value, (ast.Yield, ast.YieldFrom)):
                    exit_stmt['yield'].append((cur_blk, stmt))
                    cur_blk.add(stmt)
                    cur_blk = gen_next_blk(cur_blk, NormalEdge)

                elif isinstance(stmt, ast.Assign) and \
                    isinstance(stmt.value, ast.Call):
                    cur_blk = gen_next_blk(cur_blk, NormalEdge)
                    cur_blk.add(stmt)
                    cur_blk = gen_next_blk(cur_blk, NormalEdge)

                else:
                    cur_blk.add(stmt)
        
        ret_info = exit_stmt
        ret_info['last_block'] = cur_blk
        return ret_info
