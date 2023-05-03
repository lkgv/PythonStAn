import copy
from queue import Queue
from typing import Dict, Set, List
import ast

from pythonstan.graph.cfg.models import ControlFlowGraph, BaseBlock
from pythonstan.graph.dominator_tree import DominatorTree


class PhiFunction(ast.stmt):
    var: str
    loads: List[str]
    store: str

    def __init__(self, var, store, loads, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.var = var
        self.store = store
        self.loads = loads

    def set_store(self, name: str):
        self.store = name

    def set_load(self, idx: int, load: str):
        if 0 < idx < len(self.loads):
            self.loads[idx] = load
        else:
            raise ValueError("invalid index")


class SSATransformer:
    cfg: ControlFlowGraph
    dt: DominatorTree
    counter: Dict[str, int]
    stores_map: Dict[str, Set[BaseBlock]]

    def build(self, cfg: ControlFlowGraph):
        self.cfg = cfg
        self.dt = DominatorTree(cfg)
        self.counter = {}
        self.stores_map = {}
        for blk in self.cfg.get_nodes():
            stores = blk.get_stores()
            for store in stores:
                if store in self.stores_map:
                    self.stores_map[store].add(blk)
                else:
                    self.stores_map[store] = {blk}

    def put_phi(self, node: BaseBlock, var: str):
        has_phi = False
        for stmt in node.stmts:
            if isinstance(stmt, PhiFunction) and stmt.var == var:
                has_phi = True
                break
        if not has_phi:
            num = self.cfg.in_degree_of(node)
            phi = PhiFunction(var, var, [var for _ in range(num)])
            node.add_front(phi)

    def add_phi_function(self):
        for store, blks in self.stores_map.items():
            wl = Queue()
            for blk in blks:
                wl.put(blk)
            visited = {*()}
            placed = {*()}
            while not wl.empty():
                cur = wl.get()
                for v in self.dt.dominance_frontier(cur):
                    assert isinstance(v, BaseBlock)
                    if v not in placed:
                        placed.add(v)
                        self.put_phi(v, store)
                        if v not in visited:
                            visited.add(v)
                            wl.put(v)

    def search(self, node: BaseBlock):
        for stmt in node.stmts:
            ...

