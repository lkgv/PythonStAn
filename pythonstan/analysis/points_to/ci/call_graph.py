from typing import Dict, Set, Tuple

from pythonstan.ir import IRFunc, IRCall
from pythonstan.graph.cfg import ControlFlowGraph


class CallSite:
    stmt: IRCall
    func: IRFunc

    def __init__(self, stmt: IRCall, func: IRFunc):
        self.stmt = stmt
        self.func = func

    def __hash__(self):
        return hash(self.stmt) * 17 + hash(self.func)

    def __eq__(self, other):
        return self.stmt == other.stmt and self.func == other.func


class CallGraph:
    edges: Set[Tuple[CallSite, IRFunc]]
    reachable_funcs: Set[IRFunc]

    def __init__(self):
        self.edges = set()
        self.reachable_func = set()

    def add_edge(self, cs: CallSite, func: IRFunc) -> bool:
        if (cs, func) in self.edges:
            return False
        else:
            self.edges.add((cs, func))
            return True

    def get_callers_of(self, callee: IRFunc) -> Set[CallSite]:
        return {cs for cs, fn in self.edges if callee == fn}

    def get_callees_of(self, call_site: CallSite) -> Set[IRFunc]:
        return {fn for cs, fn in self.edges if cs == call_site}

    def get_call_sites_in(self, func: IRFunc) -> Set[CallSite]:
        from pythonstan import world
        cfg: ControlFlowGraph = world.World().scope_manager.get_ir(func, "cfg")
        cs = set()
        for blk in cfg.get_nodes():
            for stmt in blk.get_stmts():
                if isinstance(stmt, IRCall):
                    cs.add(CallSite(stmt, func))
        return cs

    def add_reachable_func(self, func: IRFunc):
        if func in self.reachable_funcs:
            return False
        else:
            self.reachable_funcs.add(func)
            return True

    def get_reachable_funcs(self) -> Set[IRFunc]:
        return self.reachable_funcs

