import copy
from queue import Queue
from typing import Dict, Set, List
import ast

from pythonstan.graph.cfg import ControlFlowGraph, BaseBlock, Phi
from pythonstan.graph.dominator_tree import DominatorTree


class SSATransformer:
    cfg: ControlFlowGraph
    dt: DominatorTree
    counter: Dict[str, int]
    stores_map: Dict[str, Set[BaseBlock]]

    def build(self, cfg: ControlFlowGraph) -> ControlFlowGraph:
        self.cfg = copy.deepcopy(cfg)
        self.dt = DominatorTree(self.cfg)
        self.counter = {}
        self.stores_map = {}
        for blk in self.cfg.get_nodes():
            stores = blk.get_stores()
            for store in stores:
                if store in self.stores_map:
                    self.stores_map[store].add(blk)
                else:
                    self.stores_map[store] = {blk}
        self.add_phi_function()
        self.rename()
        return self.cfg

    def put_phi(self, node: BaseBlock, var: str):
        has_phi = False
        for stmt in node.stmts:
            if isinstance(stmt, Phi) and stmt.var == var:
                has_phi = True
                break
        if not has_phi:
            num = self.cfg.in_degree_of(node)
            phi = Phi(var, var, [var for _ in range(num)])
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

    def rename(self):
        def search(node: BaseBlock):
            old_stores = []
            for stmt in node.stmts:
                if not isinstance(stmt, Phi):
                    for load in stmt.get_nostores():
                        if load in stacks and len(stacks[load]) > 0:
                            load_i = stacks[load][-1]
                            stmt.rename(load, load_i, (ast.Load, ast.Del))
                for store in stmt.get_stores():
                    if store not in counters:
                        counters[store] = 0
                        stacks[store] = []
                    idx = counters[store]
                    if idx > 0:
                        store_i = f"{store}_{idx}"
                        stmt.rename(store, store_i, (ast.Store,))
                    else:
                        store_i = store
                    stacks[store].append(store_i)
                    counters[store] += 1
                    old_stores.append(store)
            for succ in self.cfg.succs_of(node):
                idx = -1
                for i, pred in enumerate(self.cfg.preds_of(succ)):
                    if pred == node:
                        idx = i
                if idx >= 0:
                    for stmt in succ.stmts:
                        if isinstance(stmt, Phi) and stmt.var in stacks:
                            if len(stacks[stmt.var]) > 0:
                                load_i = stacks[stmt.var][-1]
                                stmt.set_load(idx, load_i)
                if succ not in visited:
                    visited.add(succ)
                    search(succ)
            for old_store in old_stores:
                stacks[old_store].pop()

        visited = {self.cfg.get_entry()}
        stacks = {}
        counters = {}
        search(self.cfg.get_entry())
