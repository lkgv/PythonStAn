from typing import *
from abc import ABC, abstractmethod
import ast
from ast import stmt


class BaseBlock:
    idx : int
    stmts: List[stmt]
    cfg: Optional['ControlFlowGraph']

    def __init__(self, idx=-1, cfg=None, stmts=[]):
        self.idx = idx
        self.stmts = stmts
        self.cfg=cfg
    
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
        return self
    
    def n_stmt(self):
        return len(self.stmts)
    
    def _fix_missing_locations(self):
        for stmt in self.stmts:
            ast.fix_missing_locations(stmt)
    
    def __str__(self):
        if self.n_stmt > 0:
            start = self.stmts[0]
            head = f"[{self.idx}] {start.lineno}:{start.col_offset}"
        else:
            head = f"[{self.idx}] ?:?"
        stmts_str = '\n'.join([ast.unparse(stmt) for stmt in self.stmts])
        return '\n'.join([head, stmts_str])


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


class CFGScope(ABC):
    funcs: List['CFGFunc']
    classes: List['CFGClass']
    imports: List[CFGImport]

    @abstractmethod
    def __init__(self, funcs, classes, imports):
        self.set_funcs(funcs)
        self.set_classes(classes)
        self.set_imports(imports)

    def set_funcs(self, funcs):
        self.funcs = funcs

    def set_classes(self, classes):
        self.classes = classes
    
    def set_imports(self, imports):
        self.imports = imports
    
    def add_func(self, func):
        self.funcs.append(func)
    
    def add_class(self, cls):
        self.classes.append(cls)
    
    def add_import(self, imp):
        self.imports.append(imp)


class CFGClass(CFGScope):
    class_def: ast.ClassDef
    scope: Optional[CFGScope]

    def __init__(self, class_def, scope=None,
                 funcs=[], classes=[], imports=[]):
        super().__init__(funcs, classes, imports)
        self.class_def = class_def
        self.scope = scope
    
    def set_scope(self, scope):
        self.scope = scope

    def __repr__(self) -> str:
        return ast.unparse(self.class_def)


class CFGFunc(CFGScope):
    func_def: ast.FunctionDef
    cfg: Optional['ControlFlowGraph']
    scope: Optional[CFGScope]
    
    def __init__(self, func_def, cfg=None, scope=None,
                 funcs=[], classes=[], imports=[]):
        super().__init__(funcs, classes, imports)
        self.func_def = func_def
        self.cfg = cfg
        self.scope = scope
    
    def set_cfg(self, cfg):
        self.cfg = cfg
    
    def set_scope(self, scope):
        self.scope = scope

    def __repr__(self) -> str:
        return ast.unparse(self.func_def)


class CFGModule(CFGScope):
    cfg: Optional['ControlFlowGraph']

    def __init__(self, cfg=None, funcs=[], classes=[], imports=[]):
        super().__init__(funcs, classes, imports)
        self.cfg = cfg
    
    def set_cfg(self, cfg):
        self.cfg = cfg

    def __str__(self):
        return '\n'.join([str(self.cfg), '\n\n'.join([str(c) for c in self.classes]), str(self.funcs)])


class ControlFlowGraph:
    entry_blk: BaseBlock
    exit_blks: List[BaseBlock]
    super_exit_blk: BaseBlock
    scope: Optional[CFGScope]
    in_edges: Dict[BaseBlock, List[Edge]]
    out_edges: Dict[BaseBlock, List[Edge]]
    blks: Set[BaseBlock]

    def __init__(self, entry_blk=None, scope=None):
        self.blks = {*()}
        self.scope = scope
        self.in_edges = {}
        self.out_edges = {}
        self.entry_blk = entry_blk if entry_blk is not None else BaseBlock(self)
        if entry_blk is not None:
            self.entry_blk = entry_blk
        else:
            self.entry_blk = BaseBlock(idx=0, cfg=self)
        self.exit_blks = []

    def preds_of(self, blk: BaseBlock):
        return [e.start for e in self.in_edges_of(blk)]
    
    def succs_of(self, blk: BaseBlock):
        return [e.end for e in self.out_edges_of(blk)]
    
    def in_edges_of(self, blk: BaseBlock):
        return self.in_edges[blk]
    
    def out_edges_of(self, blk: BaseBlock):
        return self.out_edges[blk]
    
    def add_blk(self, blk: BaseBlock):
        self.blks.add(blk)
        self.in_edges[blk] = []
        self.out_edges[blk] = []
    
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
        self.exit_blks.append(blk)
    
    def gen_super_exit(self):
        pass