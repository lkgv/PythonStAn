"""Finite store and explicit completions for the independent abstract engine."""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from typing import Any
import ast
from pythonstan.analysis.domains.values import Value, ValueOperations

def semantic_payload_equal(left,right,seen=None):
    from .state_transform import semantic_payload_equal as equal
    return equal(left,right,seen)

Slot = tuple[str, str, Any]

@dataclass
class HeapObject:
    kind: str
    fields: dict[Any, Value] = field(default_factory=dict)
    default: Value = field(default_factory=Value.bottom)
    maybe_missing: set[Any] = field(default_factory=set)
    singleton: bool = True
    class_ids: frozenset[str] = frozenset()
    metadata: dict[str, Any] = field(default_factory=dict)
    havoc: bool = False

    def copy(self):
        from .state_transform import detached_payload
        return detached_payload(self)

    def join(self, other, ops):
        result=self.copy(); result.singleton=self.singleton and other.singleton
        # Observable iterator/frame/length/default metadata belongs to the
        # store order. If distinct metadata cannot be represented jointly, the
        # object becomes a declared conservative havoc object; consumers must
        # not reuse the retained representative as an exact execution state.
        metadata_changed=not semantic_payload_equal(self.metadata,other.metadata)
        incompatible=self.kind!=other.kind or self.kind=="class" and self.class_ids!=other.class_ids
        result.havoc=self.havoc or other.havoc or metadata_changed or incompatible
        if incompatible:result.kind="summary";result.singleton=False
        result.default=ops.join(self.default,other.default)
        if incompatible:result.default=ops.join(result.default,Value.unknown())
        result.class_ids=self.class_ids | other.class_ids
        for key in self.fields.keys()|other.fields.keys():
            result.fields[key]=ops.join(self.fields.get(key,self.default),other.fields.get(key,other.default))
            if key not in self.fields or key not in other.fields or key in self.maybe_missing or key in other.maybe_missing: result.maybe_missing.add(key)
        return result

@dataclass
class Store:
    cells: dict[str, Value] = field(default_factory=dict)
    heap: dict[str, HeapObject] = field(default_factory=dict)
    initialized: dict[str,str] = field(default_factory=dict)
    writes: set[Slot] = field(default_factory=set)
    events: tuple[tuple[str,str],...] = ()
    obligations: frozenset[str] = frozenset()
    havoc_namespaces: frozenset[str] = frozenset()
    havoc_imports: bool = False
    guards: dict[str, tuple] = field(default_factory=dict)
    identity_epoch: int = 0

    def effective_cell(self,cell):
        if cell in self.cells:return self.cells[cell]
        module=cell[len("global:"):].rsplit(":",1)[0] if cell.startswith("global:") else None
        return Value(top=True,unbound=True) if module in self.havoc_namespaces else Value(unbound=True)

    def copy(self):
        from .state_transform import detached_payload
        return detached_payload(self)

    def ensure_current(self,ops):
        arena=getattr(ops,"arena",None)
        if arena is not None and self.identity_epoch!=arena.epoch:
            from .state_transform import remap_store
            mapped=remap_store(self,arena.aliases,ops,epoch=arena.epoch)
            self.__dict__.update(mapped.__dict__)
        return self

    def invalidate_guards(self,owner=None):
        if owner is None:self.guards.clear()
        else:self.guards={key:fact for key,fact in self.guards.items() if not fact or fact[0]!=owner}


    def clear_effects(self):
        result=self.copy();result.writes=set();result.events=();return result

    def write_cell(self, cell, value, *, weak=False, ops=None):
        if ops is not None:
            self.ensure_current(ops);value=ops.normalize(value);cell=ops.arena.resolve(cell)
        self.guards.pop(cell,None)
        if weak and cell in self.cells: value=ops.join(self.cells[cell],value)
        self.cells[cell]=value;self.writes.add(("cell",cell,None))

    def read_slot(self, objid, key, ops):
        self.ensure_current(ops);objid=ops.arena.resolve(objid)
        obj=self.heap.get(objid)
        if obj is None: return Value.unknown(),True
        value=obj.fields.get(key,obj.default)
        if obj.havoc: value=ops.join(value,Value.unknown())
        return value,key in obj.maybe_missing or key not in obj.fields

    def write_slot(self, objid, key, value, ops, *, weak=False):
        self.ensure_current(ops);objid=ops.arena.resolve(objid);value=ops.normalize(value)
        obj=self.heap[objid];weak=weak or not obj.singleton
        was_missing=key not in obj.fields or key in obj.maybe_missing
        if weak:value=ops.join(obj.fields.get(key,obj.default),value)
        obj.fields[key]=value
        if weak and was_missing:obj.maybe_missing.add(key)
        elif not weak:obj.maybe_missing.discard(key)
        self.invalidate_guards(objid);self.writes.add(("heap",objid,key))

    def join(self, other, ops, *, widen=False):
        self.ensure_current(ops);other.ensure_current(ops)
        result=self.copy();join=ops.widen if widen else ops.join
        for cell in self.cells.keys()|other.cells.keys():
            a=self.effective_cell(cell);b=other.effective_cell(cell)
            result.cells[cell]=join(a,b)
        for key,obj in other.heap.items():
            result.heap[key]=result.heap[key].join(obj,ops) if key in result.heap else obj.copy()
        result.havoc_imports=self.havoc_imports or other.havoc_imports
        for module in self.initialized.keys()|other.initialized.keys():
            a=self.initialized.get(module,"maybe" if self.havoc_imports else "unseen")
            b=other.initialized.get(module,"maybe" if other.havoc_imports else "unseen")
            result.initialized[module]=a if a==b else "maybe"
        result.writes |= other.writes
        result.guards={key:fact for key,fact in self.guards.items() if key in other.guards and semantic_payload_equal(fact,other.guards[key])}
        result.obligations |= other.obligations
        result.havoc_namespaces |= other.havoc_namespaces
        # Events are may-prefix observations, not an order proof.
        result.events=tuple(dict.fromkeys((*self.events,*other.events)))
        return result

    def leq(self, other, ops):
        self.ensure_current(ops);other.ensure_current(ops)
        if not self.writes<=other.writes:return False
        from .state_transform import payload_signature
        if not {payload_signature(x) for x in self.events}<={payload_signature(x) for x in other.events}:return False
        if any(key not in self.guards or not semantic_payload_equal(self.guards[key],fact) for key,fact in other.guards.items()):return False
        for cell,value in self.cells.items():
            if not ops.leq(value,other.effective_cell(cell)): return False
        for key,obj in self.heap.items():
            if key not in other.heap: return False
            rhs=other.heap[key]
            if obj.havoc and not rhs.havoc: return False
            if not obj.singleton and rhs.singleton:return False
            if not rhs.havoc and not semantic_payload_equal(obj.metadata,rhs.metadata):return False
            if not ops.leq(obj.default,rhs.default): return False
            for field,value in obj.fields.items():
                if not ops.leq(value,rhs.fields.get(field,rhs.default)): return False
            if not obj.maybe_missing<=rhs.maybe_missing: return False
        if self.havoc_imports and not other.havoc_imports:return False
        for module in self.initialized.keys()|other.initialized.keys():
            a=self.initialized.get(module,"maybe" if self.havoc_imports else "unseen")
            b=other.initialized.get(module,"maybe" if other.havoc_imports else "unseen")
            if b!="maybe" and a!=b:return False
        return self.obligations<=other.obligations and self.havoc_namespaces<=other.havoc_namespaces

    def remap(self,mapping,ops):
        from .state_transform import remap_store
        return remap_store(self,mapping,ops,epoch=ops.arena.epoch)

    def apply(self,outcome,ops):
        """Apply only callee writes; preserve caller frame and untouched slots."""
        self.ensure_current(ops);outcome.store.ensure_current(ops)
        result=self.copy();callee=outcome.store
        if callee.writes or callee.havoc_namespaces or callee.havoc_imports:result.invalidate_guards()
        for objid,obj in callee.heap.items():
            if objid not in result.heap: result.heap[objid]=obj.copy()
            else:
                # Cardinality and unknown-object covers are abstraction facts,
                # not source writes: losing either can enable an invalid strong
                # update after a reused fresh allocation summary.
                result.heap[objid].singleton &= obj.singleton
                result.heap[objid].class_ids |= obj.class_ids
        # Escaping closures need their allocated cells even if never reassigned.
        for cell,value in callee.cells.items():
            if cell not in result.cells: result.cells[cell]=value
        for kind,objid,key in callee.writes:
            if kind=="cell": result.cells[objid]=callee.cells.get(objid,Value(unbound=True))
            elif objid in callee.heap:
                source=callee.heap[objid];target=result.heap[objid]
                from .state_transform import detached_payload
                target.metadata=detached_payload(source.metadata)
                if kind=="heap_default": target.default=ops.join(target.default,source.default);target.havoc|=source.havoc
                elif kind=="heap_meta":target.havoc|=source.havoc
                elif key in source.fields:
                    target.fields[key]=source.fields[key]
                    if key in source.maybe_missing: target.maybe_missing.add(key)
                    else: target.maybe_missing.discard(key)
                else: target.fields.pop(key,None);target.maybe_missing.add(key)
        result.writes |= callee.writes
        result.initialized.update(callee.initialized)
        result.havoc_imports |= callee.havoc_imports
        result.obligations |= callee.obligations
        result.havoc_namespaces |= callee.havoc_namespaces
        result.events=tuple(dict.fromkeys((*result.events,*callee.events)))
        return result

@dataclass(frozen=True)
class Frame:
    module: str
    qualname: str
    context: tuple[str,...]
    identity: str
    locals: frozenset[str] = frozenset()
    closure: tuple[tuple[str,str],...] = ()
    globals: frozenset[str] = frozenset()
    nonlocals: frozenset[str] = frozenset()
    is_module: bool = False
    is_class: bool = False

    def cell(self,name):
        if self.is_module or name in self.globals: return f"global:{self.module}:{name}"
        closure=dict(self.closure)
        if name in self.nonlocals: return closure.get(name,f"unbound-nonlocal:{self.identity}:{name}")
        if name in self.locals: return f"local:{self.identity}:{name}"
        return closure.get(name,f"global:{self.module}:{name}")

@dataclass
class Flow:
    store: Store
    kind: str = "normal"
    value: Value = field(default_factory=Value.bottom)
    exception: str | None = None
    continuation: tuple = ()
    cleanups: tuple = ()
    resume_target: Any = None
    def with_value(self,value): return Flow(self.store,self.kind,value,self.exception)

@dataclass
class FunctionInfo:
    identifier: str
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
    module: str
    qualname: str
    closure: tuple[tuple[str,str],...]
    defaults: tuple[Value,...]
    kw_defaults: tuple[tuple[str,Value],...]
    locals: frozenset[str]
    globals: frozenset[str]
    nonlocals: frozenset[str]
    is_generator: bool = False
    is_async: bool = False

@dataclass
class Unit:
    key: str
    frame: Frame
    body: tuple[ast.stmt,...]
    entry: Store
    outcomes: list[Flow] = field(default_factory=list)
    consumers: set[str] = field(default_factory=set)
    revision: int = 0
    visits: int = 0
    is_module: bool = False
