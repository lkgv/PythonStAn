import ast
import os
from collections.abc import Mapping, Sequence
from typing import Any, Optional, TypeGuard, override

from pythonstan.analysis.contracts.identity import SourceModule
from pythonstan.ir import IRClass, IRFunc, IRImport, IRModule, IRScope, IRStatement

from .namespace import Namespace

_MISSING = object()


type IRKey = tuple[str, str]


def _is_statement_ir(value: object) -> TypeGuard[Sequence[IRStatement]]:
    if not isinstance(value, (list, tuple)):
        return False
    items: Sequence[object] = value
    return all(isinstance(item, IRStatement) for item in items)


def _is_import_ir(value: object) -> TypeGuard[Sequence[IRImport]]:
    if not isinstance(value, (list, tuple)):
        return False
    items: Sequence[object] = value
    return all(isinstance(item, IRImport) for item in items)


class _QualifiedIRView(dict[IRKey, object]):
    """Compatibility view whose public mutations invalidate identity entries.

    Qualified names are useful for inspection, but are not unique source-scope
    identities: conditional definitions and redefinitions can share one name.
    Internal writes bypass these hooks only after updating the exact cache.
    """

    def __init__(self, manager: "ScopeManager") -> None:
        super().__init__()
        self.manager = manager

    @override
    def __setitem__(self, key: IRKey, value: object) -> None:
        if self.manager.scope_ir is self:
            self.manager._legacy_write(key, value)
        dict.__setitem__(self, key, value)

    @override
    def __delitem__(self, key: IRKey) -> None:
        # An unsuccessful deletion has no side effects.
        if key not in self:
            raise KeyError(key)
        if self.manager.scope_ir is self:
            self.manager._legacy_invalidate(key)
        dict.__delitem__(self, key)

    @override
    def pop(self, key: IRKey, default: object = _MISSING) -> object:
        if key not in self:
            if default is _MISSING:
                raise KeyError(key)
            return default
        value = self[key]
        del self[key]
        return value

    @override
    def popitem(self) -> tuple[IRKey, object]:
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
    preds: dict[IRModule, list[IRModule]]
    succs: dict[IRModule, list[IRModule]]
    succ_module_index: dict[tuple[IRModule, IRImport], IRModule]
    nodes: set[IRModule]

    def __init__(self) -> None:
        self.preds = {}
        self.succs = {}
        self.nodes = {*()}
        self.succ_module_index = {}

    def add_edge(self, src: IRModule, stmt: IRImport, tgt: IRModule) -> None:
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

    def add_node(self, node: IRModule) -> None:
        self.nodes.add(node)
        self.preds[node] = []
        self.succs[node] = []

    def preds_of(self, node: IRModule) -> list[IRModule]:
        return self.preds[node]

    def succs_of(self, node: IRModule) -> list[IRModule]:
        return self.succs[node]

    def get_entries(self) -> list[IRModule]:
        return [u for u in self.nodes if len(self.preds[u]) == 0]

    def get_modules(self) -> frozenset[IRModule]:
        return frozenset(self.nodes)

    def get_succ_module(self, src: IRModule, stmt: IRImport) -> Optional[IRModule]:
        return self.succ_module_index.get((src, stmt), None)


class ScopeManager:
    module_graph: ModuleGraph
    scopes: set[IRScope]
    subscope_idx: dict[tuple[IRScope, str], IRScope]
    subscopes: dict[IRScope, list[IRScope]]
    father: dict[IRScope, IRScope]
    names2scope: dict[str, IRScope]
    file2mod: dict[str, IRModule]
    _scope_ir_view: _QualifiedIRView | None = None
    _scope_ir_exact: dict[tuple[IRScope, str], object]
    _scope_ir_owners: dict[IRKey, list[IRScope]]
    _source_snapshots: dict[str, SourceModule]

    @property
    def scope_ir(self) -> _QualifiedIRView:
        if self._scope_ir_view is None:
            raise AttributeError("ScopeManager has not been built")
        return self._scope_ir_view

    @scope_ir.setter
    def scope_ir(self, value: Mapping[IRKey, object]) -> None:
        # Attribute replacement is another public mutation path. In-place |=
        # invokes this setter after its item hooks have already done the work.
        if value is self._scope_ir_view:
            return
        replacement = dict(value)
        self._scope_ir_exact.clear()
        self._scope_ir_view = _QualifiedIRView(self)
        for key, item in replacement.items():
            self._scope_ir_view[key] = item

    def build(self) -> None:
        self.scopes = {*()}
        self.subscope_idx = {}
        self.subscopes = {}
        self.father = {}
        self.names2scope = {}
        self._scope_ir_exact = {}
        self._scope_ir_owners = {}
        self.scope_ir = {}
        self.file2mod = {}
        self._source_snapshots = {}

    def install_source_snapshots(self, snapshots: Mapping[str, SourceModule]) -> None:
        """Fix immutable decoded source identity before any module is lowered."""
        if self.scopes or self.file2mod:
            raise ValueError("Source snapshots must be installed before building scopes")
        copied = dict(snapshots)
        if any(
            type(name) is not str or not isinstance(source, SourceModule) or source.module != name
            for name, source in copied.items()
        ):
            raise TypeError("Source snapshots map exact module names to SourceModule")
        self._source_snapshots = copied

    def get_module_graph(self) -> ModuleGraph:
        return self.module_graph

    def set_module_graph(self, graph: ModuleGraph) -> None:
        self.module_graph = graph

    def set_ir(self, scope: IRScope, fmt: str, ir: object) -> None:
        key = (scope.qualname, fmt)
        owners = self._scope_ir_owners.setdefault(key, [])
        if scope not in owners:
            owners.append(scope)
        self._scope_ir_exact[(scope, fmt)] = ir
        dict.__setitem__(self.scope_ir, key, ir)

    def get_ir(self, scope: IRScope, fmt: str) -> Any:
        return self._scope_ir_exact.get((scope, fmt), None)

    def get_statement_ir(self, scope: IRScope) -> Sequence[IRStatement] | None:
        """Read canonical statement IR while validating an open registry entry."""
        value = self._scope_ir_exact.get((scope, "ir"))
        if value is None:
            return None
        if not _is_statement_ir(value):
            raise TypeError("Stored ir must be a list or tuple of IRStatement objects")
        return value

    def get_import_ir(self, scope: IRScope) -> Sequence[IRImport] | None:
        """Read canonical imports without cloning the sequence or its rows."""
        value = self._scope_ir_exact.get((scope, "imports"))
        if value is None:
            return None
        if not _is_import_ir(value):
            raise TypeError("Stored imports must be a list or tuple of IRImport objects")
        return value

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

    def _legacy_invalidate(self, key: IRKey) -> None:
        for owner in self._scope_ir_owners.get(key, []):
            self._scope_ir_exact.pop((owner, key[1]), None)

    def _legacy_write(self, key: IRKey, value: object) -> None:
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
            return self.file2mod.get(filename, self.names2scope.get(ns.to_str()))
        if not os.path.isfile(filename):
            return None
        # Capture bytes and decoded source before any in-place TAC lowering.
        snapshot = self._source_snapshots.get(ns.to_str())
        if snapshot is None:
            mod = IRModule.load_module(ns.to_str(), filename)
        else:
            tree = ast.parse(
                snapshot.source, snapshot.filename, type_comments=True, feature_version=(3, 12)
            )
            mod = IRModule(
                ns.to_str(), tree, name=ns.names[-1], filename=filename, source_snapshot=snapshot
            )
        self.scopes.add(mod)
        self.names2scope[mod.get_qualname()] = mod
        self.file2mod[filename] = mod
        return mod

    def get_module(self, names: str) -> IRScope | None:
        return self.names2scope.get(names, None)

    def get_subscope(self, scope: IRScope, name: str) -> IRScope | None:
        return self.subscope_idx.get((scope, name), None)

    def get_subscopes(self, scope: IRScope) -> list[IRScope]:
        return self.subscopes.get(scope, [])

    def get_scopes(self) -> set[IRScope]:
        return self.scopes
