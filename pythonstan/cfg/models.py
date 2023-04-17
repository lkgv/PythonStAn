from typing import *
from abc import ABC
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
        self.stmts.append(stmt)
        return self
    
    def n_stmt(self):
        return len(self.stmts)
    

class Edge(ABC):
    start: BaseBlock
    end: BaseBlock


class NormalEdge(Edge):
    def __init__(self, start, end):
        self.start = start
        self.end = end


class IfEdge(Edge):
    test: bool

    def __init__(self, start, end, test):
        self.test = test
        self.start = start
        self.end = end


class CallEdge(Edge):
    def __init__(self, start, end):
        self.start = start
        self.end = end


class ExceptionEdge(Edge):
    def __init__(self, start, end):
        self.start = start
        self.end = end


class ForEdge(Edge):
    def __init__(self, start, end):
        self.start = start
        self.end = end


class ForElseEdge(Edge):
    def __init__(self, start, end):
        self.start = start
        self.end = end


class WhileEdge(Edge):
    def __init__(self, start, end):
        self.start = start
        self.end = end


class WhileElseEdge(Edge):
    def __init__(self, start, end):
        self.start = start
        self.end = end


class ClassDefEdge(Edge):
    class_cfg: 'CFGClass'

    def __init__(self, start, end, class_cfg):
        self.start = start
        self.end = end
        self.class_cfg = class_cfg


class ClassEndEdge(Edge):
    class_cfg: 'CFGClass'

    def __init__(self, start, end, class_cfg):
        self.start = start
        self.end = end
        self.class_cfg = class_cfg


class CFGClass:
    class_def: ast.ClassDef
    prev_level: Optional[Union['CFGFunc', 'CFGClass']]
    funcs: List['CFGFunc']
    classess: List['CFGClass']

    def __init__(self, class_def, prev_level=None, funcs=[], classes=[]):
        self.class_def = class_def
        self.prev_level = prev_level
        self.funcs = funcs
        self.classes = classes


class CFGFunc:
    func_def: ast.FunctionDef
    cfg: 'ControlFlowGraph'
    prev_level: Optional[Union['CFGFunc', 'CFGClass']]
    funcs: List['CFGFunc']
    classes: List['CFGClass']
    
    def __init__(self, func_def, cfg, prev_level=None, funcs=[], classes=[]):
        self.func_def = func_def
        self.cfg = cfg
        self.prev_level = prev_level
        self.funcs = funcs
        self.classes = classes


class ControlFlowGraph:
    entry_blk: BaseBlock
    exit_blks: List[BaseBlock]
    super_exit_blk: BaseBlock
    container: Optional[Union['CFGFunc', 'CFGClass']]
    in_edges: Dict[BaseBlock, List[Edge]]
    out_edges: Dict[BaseBlock, List[Edge]]
    blks: Set[BaseBlock]

    def __init__(self, entry_blk=None, container=None):
        self.blks = {*()}
        self.stmts = {*()}
        self.container = container
        self.entry_blk = entry_blk if entry_blk is not None else BaseBlock(self)
        if entry_blk is not None:
            self.entry_blk = entry_blk
        else:
            self.entry_blk = BaseBlock(idx=0, cfg=self)
        self.exit_blk = None

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
    
    def set_container(self, container: Union['CFGFunc', 'CFGClass']):
        self.container = container
    
    def add_exit(self, blk: BaseBlock):
        self.exit_blks.append(blk)
    
    def gen_super_exit(self):
        pass
