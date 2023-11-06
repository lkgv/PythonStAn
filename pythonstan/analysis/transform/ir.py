from ast import *
from queue import Queue
import copy
from typing import List, Dict, Optional, Tuple, Any

from ..analysis import AnalysisConfig
from .transform import Transform
from pythonstan.graph.cfg import *
from pythonstan.world import World
from pythonstan.ir import *


class IR(Transform):
    transformer: 'IRTransformer'

    def __init__(self, config: AnalysisConfig):
        super().__init__(config)
        self.transformer = BlockCFGBuilder()

    def transform(self, module: IRModule):
        three_address_form = World().scope_manager.get_ir(module, "three address form")
        imports = self.transformer.build_module(module, three_address_form)
        self.results = imports


class LabelGenerator:
    next_idx: int

    def __init__(self):
        self.next_idx = 0

    def gen(self) -> Label:
        label = Label(self.next_idx)
        self.next_idx += 1
        return label


class BlockCFG:
    stmts: List[IRStatement]
    funcs: List[IRFunc]
    classes: List[IRClass]
    next_idx: int
    scope: Optional[IRScope]
    label_gen: LabelGenerator
p
    def __init__(self, scope=None, next_idx=1, cfg=None):
        self.next_idx = next_idx
        self.cfg = cfg if cfg is not None else ControlFlowGraph()
        self.scope = scope
        self.funcs = []
        self.classes = []
        self.label_gen = LabelGenerator()

    def set_scope(self, scope: IRScope):
        self.scope = scope

    def new_blk(self) -> BaseBlock:
        blk = BaseBlock(self.next_idx)
        self.next_idx += 1
        return blk

    def build_module(self, module: IRModule, stmts: List[Statement]) -> List[IRImport]:
        builder = BlockCFGBuilder(scope=module)
        new_blk = builder.new_blk()
        edge = NormalEdge(builder.cfg.entry_blk, new_blk)
        builder.cfg.add_edge(edge)
        mod_info = builder._build(stmts, builder.cfg, new_blk)
        builder.cfg.add_exit(mod_info['last_block'])
        builder.cfg.add_super_exit_blk(builder.new_blk())
        World().scope_manager.set_ir(module, "block cfg", builder.cfg)
        return [ir_stmt for _, ir_stmt in mod_info['import']]

    def build_func(self, func_def) -> Tuple[IRFunc, List[Tuple[BaseBlock, IRImport]]]:
        qualname = f"{self.scope.get_qualname()}.{func_def.name}"
        func = IRFunc(qualname, func_def, under_class=isinstance(self.scope, IRClass))
        builder = BlockCFGBuilder(scope=func)
        new_blk = builder.new_blk()
        edge = NormalEdge(builder.cfg.entry_blk, new_blk)
        builder.cfg.add_edge(edge)
        func_info = builder._build(func_def.body, builder.cfg, new_blk)
        for ret_blk, _ in func_info['return']:
            builder.cfg.add_exit(ret_blk)
        for yield_blk, _ in func_info['yield']:
            builder.cfg.add_exit(yield_blk)
        for raise_blk, _ in func_info['raise']:
            builder.cfg.add_exit(raise_blk)
        builder.cfg.add_exit(func_info['last_block'])
        builder.cfg.add_super_exit_blk(builder.new_blk())
        World().scope_manager.add_func(self.scope, func)
        World().scope_manager.set_ir(func, "block cfg", builder.cfg)
        return func, func_info['import']

    def build_class(self, cls_def: ast.ClassDef) -> Tuple[IRClass, List[Tuple[BaseBlock, IRImport]]]:
        qualname = f"{self.scope.get_qualname()}.{cls_def.name}"
        cls = IRClass(qualname, cls_def)
        builder = BlockCFGBuilder(scope=cls)
        new_blk = builder.new_blk()
        edge = NormalEdge(builder.cfg.entry_blk, new_blk)
        builder.cfg.add_edge(edge)
        cls_info = builder._build(cls_def.body, builder.cfg, new_blk)
        for raise_blk, _ in cls_info['raise']:
            builder.cfg.add_exit(raise_blk)
        builder.cfg.add_exit(cls_info['last_block'])
        builder.cfg.add_super_exit_blk(builder.new_blk())
        World().scope_manager.add_class(self.scope, cls)
        World().scope_manager.set_ir(cls, "block cfg", builder.cfg)
        return cls, cls_info['import']

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
                func, imports = self.build_func(stmt)
                cfg.add_stmt(cur_blk, func)
                exit_stmt['import'].extend(imports)

            elif isinstance(stmt, ast.AsyncFunctionDef):
                func, imports = self.build_func(stmt)
                cfg.add_stmt(cur_blk, func)
                exit_stmt['import'].extend(imports)

            elif isinstance(stmt, ast.ClassDef):
                cls, imports = self.build_class(stmt)
                cfg.add_stmt(cur_blk, cls)
                exit_stmt['import'].extend(imports)
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
                ir_stmt = IRReturn(stmt)
                cfg.add_stmt(cur_blk, ir_stmt)
                exit_stmt['return'].append((cur_blk, ir_stmt))
                cur_blk = self.new_blk()
                self.cfg.add_blk(cur_blk)

            elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
                ir_stmt = IRImport(stmt)
                cfg.add_stmt(cur_blk, ir_stmt)
                exit_stmt['import'].append((cur_blk, ir_stmt))

            elif isinstance(stmt, ast.While):
                new_stmt = JumpIfFalse(test=stmt.test)
                cur_blk = gen_next_blk(i, cur_blk, NormalEdge, True)
                cfg.add_stmt(cur_blk, new_stmt)
                cur_label = self.label_gen.gen()
                cfg.add_label(cur_label, cur_blk)
                loop_blk = gen_next_blk(i, cur_blk, WhileEdge, True)
                loop_info = self._build(stmt.body, cfg, loop_blk)
                extend_info(loop_info, exclude=['break', 'continue'])
                next_blk = self.new_blk()
                next_label = self.label_gen.gen()
                cfg.add_label(next_label, next_blk)
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
                    else_label = self.label_gen.gen()
                    cfg.add_label(else_label, else_blk)
                    new_stmt.set_label(else_label)
                else:
                    cfg.add_edge(NormalEdge(cur_blk, next_blk))
                    new_stmt.set_label(next_label)
                cur_blk = next_blk

            elif isinstance(stmt, ast.If):
                test_expr = stmt.test
                if_false_jump = JumpIfFalse(test=test_expr)
                cfg.add_stmt(cur_blk, if_false_jump)
                then_blk = gen_next_blk(i, cur_blk,
                                        lambda u, v: IfEdge(u, v, test_expr, True),
                                        True)
                then_info = self._build(stmt.body, cfg, then_blk)
                extend_info(then_info)
                next_blk = self.new_blk()
                cfg.add_edge(NormalEdge(then_info['last_block'], next_blk))
                if len(stmt.orelse) > 0:
                    else_blk = gen_next_blk(i, cur_blk,
                                            lambda u, v: IfEdge(u, v, test_expr, False),
                                            True)
                    else_info = self._build(stmt.orelse, cfg, else_blk)
                    extend_info(else_info)
                    cfg.add_edge(NormalEdge(else_info['last_block'], next_blk))
                    next_label = self.label_gen.gen()
                    cfg.add_label(next_label, next_blk)
                    then_end_goto = Goto(next_label)
                    cfg.add_stmt(then_info['last_block'],then_end_goto)
                    else_label = self.label_gen.gen()
                    cfg.add_label(else_label, else_blk)
                else:
                    cfg.add_edge(IfEdge(cur_blk, next_blk, test_expr, False))
                    else_label = self.label_gen.gen()
                    cfg.add_label(else_label, next_blk)
                if_false_jump.set_label(else_label)
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
                ir_stmt = IRRaise(stmt)
                exit_stmt['raise'].append((cur_blk, ir_stmt))
                cfg.add_stmt(cur_blk, ir_stmt)
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

            elif isinstance(stmt, ast.Assign):
                    if isinstance(stmt.value, (ast.Yield, ast.YieldFrom)):
                        ir_stmt = IRYield(stmt)
                        exit_stmt['yield'].append((cur_blk, ir_stmt))
                        cfg.add_stmt(cur_blk, ir_stmt)
                        cur_blk = gen_next_blk(i, cur_blk, NormalEdge)
                    elif isinstance(stmt.value, ast.Call):
                        ir_stmt = IRCall(stmt)
                        cur_blk = gen_next_blk(i, cur_blk, NormalEdge, True)
                        cfg.add_stmt(cur_blk, ir_stmt)
                        cur_blk = gen_next_blk(i, cur_blk, CallToReturnEdge, True)
                    elif isinstance(stmt.targets[0], ast.Subscript):
                        cfg.add_stmt(cur_blk, IRStoreSubscr(stmt))
                    elif isinstance(stmt.value, ast.Subscript):
                        cfg.add_stmt(cur_blk, IRLoadSubscr(stmt))
                    elif isinstance(stmt.targets[0], ast.Attribute):
                        cfg.add_stmt(cur_blk, IRStoreAttr(stmt))
                    elif isinstance(stmt.value, ast.Attribute):
                        cfg.add_stmt(cur_blk, IRLoadAttr(stmt))
                    else:
                        cfg.add_stmt(cur_blk, IRAssign(stmt))

            elif isinstance(stmt, ast.AnnAssign):
                cfg.add_stmt(cur_blk, IRAnno(stmt))

            elif isinstance(stmt, ast.Delete):
                cfg.add_stmt(cur_blk, IRDel(stmt))

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

class IRTransformer(NodeVisitor):
    imports: List[IRImport]
    stmts: List[IRStatement]
    funcs: List[IRFunc]
    classes: List[IRClass]
    next_idx: int
    scope: IRScope
    label_gen: LabelGenerator

    def __init__(self, scope: IRScope):
        self.scope = scope

    def visit_stmts(self, stmts: List[stmt]):
        for stmt in stmts:
            self.visit(stmt)
        World().scope_manager.set_ir(self.scope, "ir", self.stmts)

    def get_imports(self) -> List[IRImport]:
        return self.imports

    def visit_FunctionDef(self, func_def: FunctionDef):
        qualname = f"{self.scope.get_qualname()}.{func_def.name}"
        func = IRFunc(qualname, func_def, is_method=isinstance(self.scope, IRClass))
        trans = IRTransformer(func)
        trans.visit_stmts(func_def.body)
        builder = BlockCFGBuilder(scope=func)
        new_blk = builder.new_blk()
        edge = NormalEdge(builder.cfg.entry_blk, new_blk)
        builder.cfg.add_edge(edge)
        func_info = builder._build(func_def.body, builder.cfg, new_blk)
        for ret_blk, _ in func_info['return']:
            builder.cfg.add_exit(ret_blk)
        for yield_blk, _ in func_info['yield']:
            builder.cfg.add_exit(yield_blk)
        for raise_blk, _ in func_info['raise']:
            builder.cfg.add_exit(raise_blk)
        builder.cfg.add_exit(func_info['last_block'])
        builder.cfg.add_super_exit_blk(builder.new_blk())
        World().scope_manager.add_func(self.scope, func)
        World().scope_manager.set_ir(func, "block cfg", builder.cfg)
        return func, func_info['import']


    def build_class(self, cls_def: ast.ClassDef) -> Tuple[IRClass, List[Tuple[BaseBlock, IRImport]]]:
        qualname = f"{self.scope.get_qualname()}.{cls_def.name}"
        cls = IRClass(qualname, cls_def)
        builder = BlockCFGBuilder(scope=cls)
        new_blk = builder.new_blk()
        edge = NormalEdge(builder.cfg.entry_blk, new_blk)
        builder.cfg.add_edge(edge)
        cls_info = builder._build(cls_def.body, builder.cfg, new_blk)
        for raise_blk, _ in cls_info['raise']:
            builder.cfg.add_exit(raise_blk)
        builder.cfg.add_exit(cls_info['last_block'])
        builder.cfg.add_super_exit_blk(builder.new_blk())
        World().scope_manager.add_class(self.scope, cls)
        World().scope_manager.set_ir(cls, "block cfg", builder.cfg)
        return cls, cls_info['import']


    def visit_F
        if isinstance(stmt, ast.FunctionDef):
            func, imports = self.build_func(stmt)
            cfg.add_stmt(cur_blk, func)
            exit_stmt['import'].extend(imports)

        elif isinstance(stmt, ast.AsyncFunctionDef):
            func, imports = self.build_func(stmt)
            cfg.add_stmt(cur_blk, func)
            exit_stmt['import'].extend(imports)

        elif isinstance(stmt, ast.ClassDef):
            cls, imports = self.build_class(stmt)
            cfg.add_stmt(cur_blk, cls)
            exit_stmt['import'].extend(imports)
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
            ir_stmt = IRReturn(stmt)
            cfg.add_stmt(cur_blk, ir_stmt)
            exit_stmt['return'].append((cur_blk, ir_stmt))
            cur_blk = self.new_blk()
            self.cfg.add_blk(cur_blk)

        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            ir_stmt = IRImport(stmt)
            cfg.add_stmt(cur_blk, ir_stmt)
            exit_stmt['import'].append((cur_blk, ir_stmt))

        elif isinstance(stmt, ast.While):
            new_stmt = JumpIfFalse(test=stmt.test)
            cur_blk = gen_next_blk(i, cur_blk, NormalEdge, True)
            cfg.add_stmt(cur_blk, new_stmt)
            cur_label = self.label_gen.gen()
            cfg.add_label(cur_label, cur_blk)
            loop_blk = gen_next_blk(i, cur_blk, WhileEdge, True)
            loop_info = self._build(stmt.body, cfg, loop_blk)
            extend_info(loop_info, exclude=['break', 'continue'])
            next_blk = self.new_blk()
            next_label = self.label_gen.gen()
            cfg.add_label(next_label, next_blk)
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
                else_label = self.label_gen.gen()
                cfg.add_label(else_label, else_blk)
                new_stmt.set_label(else_label)
            else:
                cfg.add_edge(NormalEdge(cur_blk, next_blk))
                new_stmt.set_label(next_label)
            cur_blk = next_blk

        elif isinstance(stmt, ast.If):
            test_expr = stmt.test
            if_false_jump = JumpIfFalse(test=test_expr)
            cfg.add_stmt(cur_blk, if_false_jump)
            then_blk = gen_next_blk(i, cur_blk,
                                    lambda u, v: IfEdge(u, v, test_expr, True),
                                    True)
            then_info = self._build(stmt.body, cfg, then_blk)
            extend_info(then_info)
            next_blk = self.new_blk()
            cfg.add_edge(NormalEdge(then_info['last_block'], next_blk))
            if len(stmt.orelse) > 0:
                else_blk = gen_next_blk(i, cur_blk,
                                        lambda u, v: IfEdge(u, v, test_expr, False),
                                        True)
                else_info = self._build(stmt.orelse, cfg, else_blk)
                extend_info(else_info)
                cfg.add_edge(NormalEdge(else_info['last_block'], next_blk))
                next_label = self.label_gen.gen()
                cfg.add_label(next_label, next_blk)
                then_end_goto = Goto(next_label)
                cfg.add_stmt(then_info['last_block'], then_end_goto)
                else_label = self.label_gen.gen()
                cfg.add_label(else_label, else_blk)
            else:
                cfg.add_edge(IfEdge(cur_blk, next_blk, test_expr, False))
                else_label = self.label_gen.gen()
                cfg.add_label(else_label, next_blk)
            if_false_jump.set_label(else_label)
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
            ir_stmt = IRRaise(stmt)
            exit_stmt['raise'].append((cur_blk, ir_stmt))
            cfg.add_stmt(cur_blk, ir_stmt)
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

        elif isinstance(stmt, ast.Assign):
            if isinstance(stmt.value, (ast.Yield, ast.YieldFrom)):
                ir_stmt = IRYield(stmt)
                exit_stmt['yield'].append((cur_blk, ir_stmt))
                cfg.add_stmt(cur_blk, ir_stmt)
                cur_blk = gen_next_blk(i, cur_blk, NormalEdge)
            elif isinstance(stmt.value, ast.Call):
                ir_stmt = IRCall(stmt)
                cur_blk = gen_next_blk(i, cur_blk, NormalEdge, True)
                cfg.add_stmt(cur_blk, ir_stmt)
                cur_blk = gen_next_blk(i, cur_blk, CallToReturnEdge, True)
            elif isinstance(stmt.targets[0], ast.Subscript):
                cfg.add_stmt(cur_blk, IRStoreSubscr(stmt))
            elif isinstance(stmt.value, ast.Subscript):
                cfg.add_stmt(cur_blk, IRLoadSubscr(stmt))
            elif isinstance(stmt.targets[0], ast.Attribute):
                cfg.add_stmt(cur_blk, IRStoreAttr(stmt))
            elif isinstance(stmt.value, ast.Attribute):
                cfg.add_stmt(cur_blk, IRLoadAttr(stmt))
            else:
                cfg.add_stmt(cur_blk, IRAssign(stmt))

        elif isinstance(stmt, ast.AnnAssign):
            cfg.add_stmt(cur_blk, IRAnno(stmt))

        elif isinstance(stmt, ast.Delete):
            cfg.add_stmt(cur_blk, IRDel(stmt))

        else:
            cfg.add_stmt(cur_blk, IRAstStmt(stmt))