import ast
from typing import Set, List, Dict, Tuple, Any, Optional

from .namespace import Namespace
from pythonstan.ir import IRScope, IRFunc, IRClass, IRModule
from pythonstan.ir.ir_func import IRFunc
from pythonstan.ir.ir_class import IRClass
from pythonstan.utils.persistent_rb_tree import PersistentMap


class ModuleGraph:
    preds: Dict[IRModule, List[IRModule]]
    succs: Dict[IRModule, List[IRModule]]
    nodes = Set[IRModule]

    def __init__(self):
        self.preds = {}
        self.succs = {}
        self.nodes = {*()}

    def add_edge(self, src: IRModule, tgt: IRModule):
        if src not in self.preds:
            self.preds[src] = []
            self.succs[src] = []
            self.nodes.add(src)
        if tgt not in self.preds:
            self.preds[tgt] = []
            self.succs[tgt] = []
            self.nodes.add(tgt)
        self.preds[tgt].append(src)
        self.succs[src].append(tgt)

    def preds_of(self, node: IRModule):
        return self.preds[node]

    def succs_of(self, node: IRModule):
        return self.succs[node]

    def get_entries(self):
        return [u for u in self.nodes if len(self.preds[u]) == 0]


class ScopeManager:
    # imported modules should be founded and added in the SSA-transformation
    module_graph: ModuleGraph
    scopes: Set[IRScope]
    subscopes: Dict[IRScope, List[IRScope]]
    father: Dict[IRScope, IRScope]
    ns2scope: Dict[Namespace, IRScope]
    scope_ir: Dict[Tuple[IRScope, str], Any]

    def get_module_graph(self) -> ModuleGraph:
        return self.module_graph

    def set_ir(self, scope: IRScope, fmt: str, ir: Any):
        self.scope_ir[(scope, fmt)] = ir

    def get_ir(self, scope: IRScope, fmt: str) -> Any:
        return self.scope_ir[(scope, fmt)]

    def add_func(self):
        ...

    def add_class(self):
        ...

    def add_module(self, ns: Namespace, father: Optional[IRScope] = None):
        with open(ns.get_path(), 'r') as f:
            m_ast = ast.parse(f.read())
        mod = IRModule(m_ast, ns.get_name(), ns.get_path())
        self.scopes.add(mod)
        self.ns2scope[ns] = mod
        self.subscopes[mod] = []
        if father is not None:
            self.father[mod] = father
            self.subscopes[father].append(mod)
