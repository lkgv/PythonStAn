from typing import *
from abc import ABC, abstractmethod
import ast
from ast import stmt
from graphviz import Digraph

from pythonstan.utils.var_collector import VarCollector


class BaseBlock:
    idx : int
    stmts: List[stmt]
    store_collector: VarCollector
    load_collector: VarCollector
    del_collector: VarCollector
    cfg: Optional['ControlFlowGraph']

    def __init__(self, idx=-1, cfg=None, stmts=[]):
        self.idx = idx
        self.stmts = [x for x in stmts]
        self.cfg=cfg
        self.store_collector = VarCollector("store")
        self.load_collector = VarCollector("load")
        self.del_collector = VarCollector("del")
        for stmt in stmts:
            self.store_collector.visit(stmt)
            self.load_collector.visit(stmt)
            self.del_collector.visit(stmt)
    
    def set_cfg(self, cfg: 'ControlFlowGraph'):
        self.cfg = cfg

    def get_stmts(self):
        return self.stmts

    def get_cfg(self):
        return self.cfg
    
    def get_idx(self):
        return self.idx
    
    def add(self, stmt: stmt):
        ast.fix_missing_locations(stmt)
        self.stmts.append(stmt)
        self.store_collector.visit(stmt)
        self.load_collector.visit(stmt)
        self.del_collector.visit(stmt)
        return self
    
    def n_stmt(self) -> int:
        return len(self.stmts)
    
    def get_stores(self) -> Set[str]:
        return self.store_collector.get_vars()
    
    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()
    
    def get_dels(self) -> Set[str]:
        return self.del_collector.get_vars()
    
    def _fix_missing_locations(self):
        for stmt in self.stmts:
            ast.fix_missing_locations(stmt)
    
    def __str__(self):
        if self.n_stmt() > 0:
            start = self.stmts[0]
            head = f"[{self.idx}] {start.lineno}:{start.col_offset}"
        else:
            head = f"[{self.idx}] ?:?"
        stmts_str = '\\n'.join([ast.unparse(stmt) for stmt in self.stmts])
        return '\\n'.join([head, stmts_str])
        return head


class Edge(ABC):
    start: BaseBlock
    end: BaseBlock

    @abstractmethod
    def __init__(self, start, end):
        self.start = start
        self.end = end


class NormalEdge(Edge):
    def __init__(self, start, end):
        super().__init__(start, end)


class IfEdge(Edge):
    test: bool

    def __init__(self, start, end, test):
        super().__init__(start, end)
        self.test = test


class CallEdge(Edge):
    def __init__(self, start, end):
        super().__init__(start, end)


class ExceptionEdge(Edge):
    def __init__(self, start, end):
        super().__init__(start, end)


class ForEdge(Edge):
    def __init__(self, start, end):
        super().__init__(start, end)


class ForElseEdge(Edge):
    def __init__(self, start, end):
        super().__init__(start, end)


class WhileEdge(Edge):
    def __init__(self, start, end):
        super().__init__(start, end)


class WhileElseEdge(Edge):
    def __init__(self, start, end):
        super().__init__(start, end)


class WithEdge(Edge):
    def __init__(self, start, end, var):
        super().__init__(start, end)
        self.var = var


class WithEndEdge(Edge):
    def __init__(self, start, end, var):
        super().__init__(start, end)
        self.var = var


class ExceptionEdge(Edge):
    def __init__(self, start, end, e):
        super().__init__(start, end)
        self.e = e


class ExceptionEndEdge(Edge):
    def __init__(self, start, end, e):
        super().__init__(start, end)
        self.e = e


class FinallyEdge(Edge):
    def __init__(self, start, end, stmt):
        super().__init__(start, end)
        self.stmt = stmt


class FinallyEndEdge(Edge):
    def __init__(self, start, end, stmt):
        super().__init__(start, end)
        self.stmt = stmt


class ClassDefEdge(Edge):
    cls: 'CFGClass'

    def __init__(self, start, end, class_cfg):
        super().__init__(start, end)
        self.class_cfg = class_cfg


class ClassEndEdge(Edge):
    cls: 'CFGClass'

    def __init__(self, start, end, cls):
        super().__init__(start, end)
        self.cls = cls


class CFGImport:
    stmt: Union[ast.Import, ast.ImportFrom]

    def __init__(self, stmt):
        self.stmt = stmt
    
    def __str__(self):
        ast.unparse(self.stmt)


class CFGScope(ABC):
    funcs: List['CFGFunc']
    classes: List['CFGClass']
    imports: List[CFGImport]
    cfg: Optional['ControlFlowGraph']

    @abstractmethod
    def __init__(self, cfg, funcs, classes, imports):
        self.set_cfg(cfg)
        self.set_funcs(funcs)
        self.set_classes(classes)
        self.set_imports(imports)

    def set_funcs(self, funcs: List['CFGFunc']):
        self.funcs = funcs

    def set_classes(self, classes: List['CFGClass']):
        self.classes = classes
    
    def set_imports(self, imports: List['CFGImport']):
        self.imports = imports

    def set_cfg(self, cfg:'ControlFlowGraph'):
        self.cfg = cfg
    
    def add_func(self, func: 'CFGFunc'):
        self.funcs.append(func)
    
    def add_class(self, cls: 'CFGClass'):
        self.classes.append(cls)
    
    def add_import(self, imp: 'CFGImport'):
        self.imports.append(imp)
    
    @abstractmethod
    def get_name(self) -> str:
        raise NotImplementedError

    def gen_graph(self, s: Digraph):
        with s.subgraph(name=self.get_name()) as subs:
            self.gen_subgraph(subs)
    
    def gen_subgraph(self, s: Digraph):
        self.cfg.gen_graph(s)
        '''
        for cls in self.classes:
            cls.gen_graph(s)
        for fn in self.funcs:
            fn.gen_graph(s)
        '''


class CFGClassDef:
    name: str
    bases: List[ast.expr]
    keywords: List[ast.keyword]
    decorator_list: List[ast.expr]
    ast_repr: ast.ClassDef

    cell_vars: List[ast.Name]
    
    def __init__(self, cls: ast.ClassDef, cell_vars=[]):
        self.name = cls.name
        self.bases = cls.bases
        self.keywords = cls.keywords
        self.decorator_list = cls.decorator_list
        self.ast_repr = ast.ClassDef(
            name=self.name,
            bases=self.bases,
            keywords=self.keywords,
            body=[],
            decorator_list=self.decorator_list)
        ast.copy_location(self.ast_repr, cls)

        self.cell_vars = cell_vars
    
    def to_ast(self) -> ast.ClassDef:
        return self.ast_repr
    
    def set_cell_vars(self, cell_vars):
        self.cell_vars = cell_vars

    def add_cell_var(self, cell_var):
        self.cell_vars.append(cell_var)
    
    def __str__(self):
        names = list(map(lambda x: x.id, self.cell_vars))
        cell_comment = "# closure: (" + ', '.join(names) + ")\n"
        return cell_comment + ast.unparse(self.to_ast())


class CFGClass(CFGScope):
    class_def: CFGClassDef
    scope: Optional[CFGScope]

    def __init__(self, class_def, cfg=None, scope=None,
                 funcs=[], classes=[], imports=[]):
        super().__init__(cfg, funcs, classes, imports)
        self.class_def = class_def
        self.scope = scope
    
    def set_scope(self, scope):
        self.scope = scope
    
    def get_name(self) -> str:
        return f'cls:{self.class_def.name}'

    def __repr__(self) -> str:
        return str(self.class_def)


class CFGFuncDef:
    name: str
    args: List[ast.arguments]
    decorator_list: List[ast.expr]
    returns: ast.expr
    type_comment: str
    ast_repr: ast.FunctionDef

    cell_vars: List[ast.Name]
    
    def __init__(self, fn: ast.FunctionDef, cell_vars=[]):
        self.name = fn.name
        self.args = fn.args
        self.decorator_list = fn.decorator_list
        self.returns = fn.returns
        self.type_comment = fn.type_comment
        self.ast_repr = ast.FunctionDef(
            name=self.name,
            args=self.args,
            body=[],
            decorator_list=self.decorator_list,
            returns=self.returns,
            type_comment=self.type_comment)
        ast.copy_location(self.ast_repr, fn)

        self.cell_vars = cell_vars
    
    def to_ast(self) -> ast.FunctionDef:
        return self.ast_repr
    
    def set_cell_vars(self, cell_vars):
        self.cell_vars = cell_vars

    def add_cell_var(self, cell_var):
        self.cell_vars.append(cell_var)
    
    def __str__(self):
        names = list(map(lambda x: x.id, self.cell_vars))
        cell_comment = "# closure: (" + ', '.join(names) + ")\n"
        return cell_comment + ast.unparse(self.to_ast())


class CFGAsyncFuncDef(CFGFuncDef):
    ast_repr: ast.AsyncFunctionDef

    def __init__(self, fn: ast.AsyncFunctionDef, cell_vars=[]):
        self.name = fn.name
        self.args = fn.args
        self.decorator_list = fn.decorator_list
        self.returns = fn.returns
        self.type_comment = fn.type_comment
        self.ast_repr = ast.AsyncFunctionDef(
            name=self.name,
            args=self.args,
            body=[],
            decorator_list=self.decorator_list,
            returns=self.returns,
            type_comment=self.type_comment)
        ast.copy_location(self.ast_repr, fn)

        self.cell_vars = cell_vars


class CFGFunc(CFGScope):
    func_def: CFGFuncDef
    scope: Optional[CFGScope]
    
    def __init__(self, func_def, cfg=None, scope=None,
                 funcs=[], classes=[], imports=[]):
        super().__init__(cfg, funcs, classes, imports)
        self.func_def = func_def
        self.scope = scope
    
    def set_scope(self, scope):
        self.scope = scope
    
    def get_name(self) -> str:
        return f'fn:{self.func_def.name}'

    def __repr__(self) -> str:
        return ast.unparse(self.func_def.to_ast())


class CFGModule(CFGScope):
    def __init__(self, cfg=None, funcs=[], classes=[], imports=[]):
        super().__init__(cfg, funcs, classes, imports)
    
    def get_name(self) -> str:
        return 'mod'

    def __str__(self):
        return '\n'.join([str(self.cfg), '\n\n'.join([str(c) for c in self.classes]), str(self.funcs)])

# fix a bug: idx should be maintained by CFG rather than CFG builder
# TODO super exit block
class ControlFlowGraph:
    entry_blk: BaseBlock
    exit_blks: Set[BaseBlock]
    super_exit_blk: Optional[BaseBlock]
    scope: Optional[CFGScope]
    in_edges: Dict[BaseBlock, List[Edge]]
    out_edges: Dict[BaseBlock, List[Edge]]
    blks: Set[BaseBlock]
    stmts: Set[stmt]
    var_collector: VarCollector

    def __init__(self, entry_blk=None, scope=None):
        self.blks = {*()}
        self.stmts = {*()}
        self.scope = scope
        self.in_edges = {}
        self.out_edges = {}
        self.entry_blk = entry_blk if entry_blk is not None else BaseBlock(self)
        if entry_blk is not None:
            self.entry_blk = entry_blk
        else:
            self.entry_blk = BaseBlock(idx=0, cfg=self)
        self.exit_blks = {*()}
        self.super_exit_blk = None
        self.var_collector = VarCollector()

    def preds_of(self, blk: BaseBlock):
        return [e.start for e in self.in_edges_of(blk)]
    
    def succs_of(self, blk: BaseBlock):
        return [e.end for e in self.out_edges_of(blk)]
    
    def in_edges_of(self, blk: BaseBlock):
        return self.in_edges[blk]
    
    def out_edges_of(self, blk: BaseBlock):
        return self.out_edges[blk]
    
    def in_degree_of(self, blk: BaseBlock) -> int:
        return len(self.in_edges_of(blk))
    
    def out_degree_of(self, blk: BaseBlock) -> int:
        return len(self.out_edges_of(blk))
    
    def add_blk(self, blk: BaseBlock):
        if blk not in self.blks:
            self.blks.add(blk)
            self.in_edges[blk] = []
            self.out_edges[blk] = []
    
    def add_stmt(self, blk: BaseBlock, stmt: stmt):
        blk.add(stmt)
        self.stmts.add(stmt)
        self.var_collector.visit(stmt)
    
    def add_edge(self, edge: Edge):
        if edge.start not in self.blks:
            self.add_blk(edge.start)
        if edge.end not in self.blks:
            self.add_blk(edge.end)
        self.in_edges[edge.end].append(edge)
        self.out_edges[edge.start].append(edge)
    
    def set_scope(self, scope: CFGScope):
        self.scope = scope
    
    def add_exit(self, blk: BaseBlock):
        self.exit_blks.add(blk)

    def find_var(self, var):
        return self.var_collector.find(var)
    
    def get_var_map(self):
        return self.var_collector.get_vars()
    
    def get_var_num(self):
        return self.var_collector.size()
    
    def add_super_exit_blk(self, blk):
        for cur_blk in self.exit_blks:
            if self.out_degree_of(cur_blk) == 0:
                self.exit_blks.add(cur_blk)
        self.add_blk(blk)
        self.super_exit_blk = blk
        for exit_blk in self.exit_blks:
            pass
            self.add_edge(NormalEdge(exit_blk, blk))
    
    def gen_graph(self, s: Digraph):
        gen_id = lambda blk: f'{subg_name}_{blk.idx}'
        subg_name = self.scope.get_name()
        for blk in self.blks:
            if blk == self.entry_blk:
                descr = "ENTRY"
            elif blk == self.super_exit_blk:
                descr = "EXIT"
            else:
                descr = str(blk)
            if blk in self.exit_blks:
                s.node(gen_id(blk), descr, style='filled', fillcolor='pink')
            else:
                s.node(gen_id(blk), descr)
        for blk in self.blks:
            for e in self.out_edges_of(blk):
                if e.start == e.end:
                    continue
                src = gen_id(e.start)
                tgt = gen_id(e.end)
                s.edge(src, tgt)
