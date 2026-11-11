import ast
import os
from typing import Set, List, Dict, Tuple, Any, Optional, FrozenSet

from .namespace import Namespace
from pythonstan.ir import IRScope, IRFunc, IRClass, IRModule, IRImport
from pythonstan.utils.persistent_rb_tree import PersistentMap


_MISSING = object()


class _QualifiedIRView(dict):
    """Compatibility view whose public mutations invalidate identity entries.

    Qualified names are useful for inspection, but are not unique source-scope
    identities: conditional definitions and redefinitions can share one name.
    Internal writes bypass these hooks only after updating the exact cache.
    """

    def __init__(self, manager):
        super().__init__()
        self.manager = manager

    def __setitem__(self, key, value):
        if self.manager.scope_ir is self:
            self.manager._legacy_write(key, value)
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        # An unsuccessful deletion has no side effects.
        if key not in self:
            raise KeyError(key)
        if self.manager.scope_ir is self:
            self.manager._legacy_invalidate(key)
        dict.__delitem__(self, key)

    def pop(self, key, default=_MISSING):
        if key not in self:
            if default is _MISSING:
                raise KeyError(key)
            return default
        value = self[key]
        del self[key]
        return value

    def popitem(self):
        if not self:
            raise KeyError("popitem(): dictionary is empty")
        key = next(reversed(self))
        return key, self.pop(key)

    def clear(self):
        if self.manager.scope_ir is self:
            self.manager._scope_ir_exact.clear()
        dict.clear(self)

    def update(self, *args, **kwargs):
        # dict.update on a dict subclass bypasses __setitem__.
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def __ior__(self, other):
        self.update(other)
        return self


class ModuleGraph:
    preds: Dict[IRModule, List[IRModule]]
    succs: Dict[IRModule, List[IRModule]]
    succ_module_index: Dict[Tuple[IRModule, IRImport], IRModule]
    nodes = Set[IRModule]

    def __init__(self):
        self.preds = {}
        self.succs = {}
        self.nodes = {*()}
        self.succ_module_index = {}

    def add_edge(self, src: IRModule, stmt, tgt: IRModule):
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
        self.succ_module_index[(src, stmt)] = tgt

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

    def get_modules(self) -> FrozenSet[IRModule]:
        return frozenset(self.nodes)
    
    def get_succ_module(self, src: IRModule, stmt: IRImport) -> Optional[IRModule]:
        return self.succ_module_index.get((src, stmt), None)


class ScopeManager:
    module_graph: ModuleGraph
    scopes: Set[IRScope]
    subscope_idx: Dict[Tuple[IRScope, str], IRScope]
    subscopes: Dict[IRScope, List[IRScope]]
    father: Dict[IRScope, IRScope]
    names2scope: Dict[str, IRScope]
    scope_ir: Dict[Tuple[str, str], Any]
    file2mod: Dict[str, IRModule]

    @property
    def scope_ir(self):
        return self._scope_ir_view

    @scope_ir.setter
    def scope_ir(self, value):
        # Attribute replacement is another public mutation path. In-place |=
        # invokes this setter after its item hooks have already done the work.
        if value is getattr(self, "_scope_ir_view", None):
            return
        replacement = dict(value)
        self._scope_ir_exact.clear()
        self._scope_ir_view = _QualifiedIRView(self)
        self._scope_ir_view.update(replacement)

    def build(self):
        self.scopes = {*()}
        self.subscope_idx = {}
        self.subscopes = {}
        self.father = {}
        self.names2scope = {}
        self._scope_ir_exact = {}
        self._scope_ir_owners = {}
        self.scope_ir = {}
        self.file2mod = {}
        
    def get_module_graph(self) -> ModuleGraph:
        return self.module_graph

    def set_module_graph(self, graph: ModuleGraph):
        self.module_graph = graph

    def set_ir(self, scope: IRScope, fmt: str, ir: Any):
        key = (scope.qualname, fmt)
        owners = self._scope_ir_owners.setdefault(key, [])
        if scope not in owners:
            owners.append(scope)
        self._scope_ir_exact[(scope, fmt)] = ir
        dict.__setitem__(self.scope_ir, key, ir)

    def get_ir(self, scope: IRScope, fmt: str) -> Any:
        return self._scope_ir_exact.get((scope, fmt), None)

    def retire_ir(self, scope: IRScope, fmt: str, expected: Any) -> bool:
        """Release this scope's representation only under an identity guard."""
        exact_key = (scope, fmt)
        current = self._scope_ir_exact.get(exact_key, _MISSING)
        if current is _MISSING or current is not expected:
            return False
        del self._scope_ir_exact[exact_key]
        key = (scope.qualname, fmt)
        for owner in reversed(self._scope_ir_owners.get(key, [])):
            surviving = self._scope_ir_exact.get((owner, fmt), _MISSING)
            if surviving is not _MISSING:
                dict.__setitem__(self.scope_ir, key, surviving)
                break
        else:
            if key in self.scope_ir:
                dict.__delitem__(self.scope_ir, key)
        return True

    def _legacy_invalidate(self, key):
        for owner in self._scope_ir_owners.get(key, []):
            self._scope_ir_exact.pop((owner, key[1]), None)

    def _legacy_write(self, key, value):
        self._legacy_invalidate(key)
        owners = self._scope_ir_owners.get(key, [])
        # A name-only write cannot identify one of several distinct bodies.
        if len(owners) == 1:
            self._scope_ir_exact[(owners[0], key[1])] = value

    def check_analysis_done(self, scope: IRScope, analysis_name: str) -> bool:
        return (scope, analysis_name) in self._scope_ir_exact

    def add_func(self, scope: IRScope, func: IRFunc):
        self._attach_source_module(scope, func)
        self.names2scope[func.get_qualname()] = func
        self.scopes.add(func)
        self.father[func] = scope
        self.subscope_idx[(scope, func.name)] = func
        if scope in self.subscopes:
            self.subscopes[scope].append(func)
        else:
            self.subscopes[scope] = [func]

    def add_class(self, scope: IRScope, cls: IRClass):
        self._attach_source_module(scope, cls)
        self.names2scope[cls.get_qualname()] = cls
        self.scopes.add(cls)
        self.father[cls] = scope
        self.subscope_idx[(scope, cls.name)] = cls
        if scope in self.subscopes:
            self.subscopes[scope].append(cls)
        else:
            self.subscopes[scope] = [cls]

    def _attach_source_module(self, parent: IRScope, child: IRScope):
        """Bind nested scopes even when lowering registered children first."""
        module = getattr(parent, "source_module", None)
        pending = [child]
        seen = set()
        while pending:
            scope = pending.pop()
            if scope in seen:
                continue
            seen.add(scope)
            scope.source_module = module
            pending.extend(self.subscopes.get(scope, ()))

    def add_module(self, ns: Namespace, filename: str) -> Optional[IRModule]:
        if filename in self.file2mod or ns.to_str() in self.names2scope:
            return self.file2mod[filename]        
        if not os.path.isfile(filename):
            return None
        # Capture bytes and decoded source before any in-place TAC lowering.
        mod = IRModule.load_module(ns.to_str(), filename)
        self.scopes.add(mod)
        self.names2scope[mod.get_qualname()] = mod
        self.file2mod[filename] = mod
        return mod

    def get_module(self, names: str) -> IRScope:
        return self.names2scope.get(names, None)

    def get_subscope(self, scope: IRScope, name: str) -> IRScope:
        return self.subscope_idx.get((scope, name), None)

    def get_subscopes(self, scope: IRScope) -> List[IRScope]:
        return self.subscopes.get(scope, [])

    def get_scopes(self) -> Set[IRScope]:
        return self.scopes
