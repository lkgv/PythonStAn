import ast
import os
from typing import Set, List, Dict, Tuple, Any, Optional

from .namespace import Namespace
from pythonstan.ir import IRScope, IRFunc, IRClass, IRModule
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

    def add_node(self, node: IRModule):
        self.nodes.add(node)
        self.preds[node] = []
        self.succs[node] = []

    def preds_of(self, node: IRModule):
        return self.preds[node]

    def succs_of(self, node: IRModule):
        return self.succs[node]

    def get_entries(self):
        return [u for u in self.nodes if len(self.preds[u]) == 0]


class ScopeManager:
    module_graph: ModuleGraph
    scopes: Set[IRScope]
    subscope_idx: Dict[Tuple[IRScope, str], IRScope]
    subscopes: Dict[IRScope, List[IRScope]]
    father: Dict[IRScope, IRScope]
    names2scope: Dict[str, IRScope]
    scope_ir: Dict[Tuple[str, str], Any]

    def build(self):
        self.scopes = {*()}
        self.subscope_idx = {}
        self.subscopes = {}
        self.father = {}
        self.names2scope = {}
        self.scope_ir = {}

    def get_module_graph(self) -> ModuleGraph:
        return self.module_graph

    def set_module_graph(self, graph: ModuleGraph):
        self.module_graph = graph

    def set_ir(self, scope: IRScope, fmt: str, ir: Any):
        self.scope_ir[(scope.qualname, fmt)] = ir

    def get_ir(self, scope: IRScope, fmt: str) -> Any:
        return self.scope_ir.get((scope.qualname, fmt), None)

    def check_analysis_done(self, scope: IRScope, analysis_name: str) -> bool:
        return (scope, analysis_name) in self.scope_ir

    def add_func(self, scope: IRScope, func: IRFunc):
        self.names2scope[func.get_qualname()] = func
        self.scopes.add(func)
        self.father[func] = scope
        self.subscope_idx[(scope, func.name)] = func
        if scope in self.subscopes:
            self.subscopes[scope].append(func)
        else:
            self.subscopes[scope] = [func]

    def add_class(self, scope: IRScope, cls: IRClass):
        self.names2scope[cls.get_qualname()] = cls
        self.scopes.add(cls)
        self.father[cls] = scope
        self.subscope_idx[(scope, cls.name)] = cls
        if scope in self.subscopes:
            self.subscopes[scope].append(cls)
        else:
            self.subscopes[scope] = [cls]

    def add_module(self, ns: Namespace, filename: str) -> Optional[IRModule]:
        if not os.path.isfile(filename):
            return None
        with open(filename, 'r') as f:
            m_ast = ast.parse(f.read())
        mod = IRModule(ns.to_str(), m_ast, ns.get_name(), filename)
        self.scopes.add(mod)
        self.names2scope[mod.get_qualname()] = mod
        return mod

    def get_module(self, names: str) -> IRScope:
        return self.names2scope.get(names, None)

    def get_subscope(self, scope: IRScope, name: str) -> IRScope:
        return self.subscope_idx.get((scope, name), None)

    def get_subscopes(self, scope: IRScope) -> List[IRScope]:
        return self.subscopes.get(scope, [])

    def get_scopes(self) -> Set[IRScope]:
        return self.scopes
