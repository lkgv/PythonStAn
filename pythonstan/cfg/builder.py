from .models import *
import ast
from ast import stmt
from typing import List, Dict

class CFGBuilder:
    cfg: ControlFlowGraph
    funcs: List[CFGFunc]
    classes: List[CFGClass]
    next_idx: int
    container: Optional[Union[CFGFunc, CFGClass]] = None

    def __init__(self, next_idx=0, cfg=None):
        self.next_idx = next_idx
        self.cfg = cfg if cfg is not None else ControlFlowGraph()
        self.funcs = []
        self.classes = []

    def from_stmts(self, stmts: List[stmt]) -> ControlFlowGraph:
        next_blk = self._new_blk()
        edge = NormalEdge(self.cfg.entry_blk, next_blk)
        self.cfg.add_edge(edge)
        self._build(stmts, self.cfg, next_blk)

        # need to build subgraph and combine graphs!
    
    def _new_blk(self) -> BaseBlock:
        blk = BaseBlock(self.next_idx, self.cfg)
        self.next_idx += 1
        return blk

    def from_func(self, *kargs) -> CFGFunc:
        pass

    def from_class(self, *kargs) -> CFGClass:
        pass

    def _build(self, stmts: List[stmt],
               cfg: ControlFlowGraph,
               cur_blk: BaseBlock
               ) -> Tuple[List[ast.Break]]:
        exit_stmt = {
            'break': [],
            'continue': [],
            'return': [],
            'yield': [],
            'raise': []
        }
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
        
        def extend_info(info, exclude=[]):
            for key in exit_stmt.keys():
                if key not in exclude:
                    exit_stmt[key].extend(info[key])
        
        for i, stmt in enumerate(stmts):
            if isinstance(stmt, ast.FunctionDef):
                func_builder = CFGBuilder()
                func = func_builder.from_func(stmt, self.container)
                self.funcs.append(func)
            
            elif isinstance(stmt, ast.AsyncFunctionDef):
                pass

            elif isinstance(stmt, ast.ClassDef):
                class_builder = CFGBuilder(next_idx=self.next_idx)
                cls = class_builder.from_class(stmt, self.container)
                self.classes.append(cls)
                
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

            # | Try(stmt* body, excepthandler* handlers, stmt* orelse, stmt* finalbody)
            # class ast.ExceptHandler(type, name, body)

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
