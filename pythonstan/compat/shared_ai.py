"""Alias-preserving legacy state boundary to the shared abstract engine.

This module converts representations. Python operations execute the ordinary
Transfer/InterproceduralDriver, never a second compatibility interpreter.
Unavailable metadata produces explicit obligations.
"""
from __future__ import annotations

import ast
import time
from dataclasses import replace
from pythonstan.analysis.contracts import SourceModule
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.domains.ints import IntLattice, Parity
from pythonstan.analysis.domains.strings import StrLattice
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver, BudgetStop
from pythonstan.analysis.engines.abstract.state import Store, HeapObject, Frame, Flow, FunctionInfo
from pythonstan.analysis.semantics.names import classify_bindings


class BoundaryUnavailable(ValueError):
    pass


class SharedDriver(InterproceduralDriver):
    def __init__(self, *args, work_ledger=None, **kwargs):
        self.work_ledger = work_ledger
        super().__init__(*args, **kwargs)

    def charge(self, amount, category):
        if self.work_ledger is not None:
            for _ in range(amount):
                if self.work_ledger.consume(kind="abstract_transfer") is False:
                    raise BudgetStop("shared_work_budget")
        super().charge(amount, category)


class LegacyStateBridge:
    """One invocation's complete value, alias and outgoing state conversion."""
    def __init__(self, query, budget, context, scope, initial, driver):
        self.query, self.budget, self.context, self.scope = query, budget, context, scope
        self.initial, self.driver = initial, driver
        self.objects, self.reverse, self.addresses = {}, {}, {}
        self.cells, self.reads, self.reasons = {}, set(), set()
        self.store = Store()

    def value(self, old):
        from pythonstan.analysis.pointer.kcfa.object import ConstantObject
        if old.is_bottom(): return Value.bottom()
        refs, constants = set(), []
        for obj in old.addrs:
            if isinstance(obj, ConstantObject): constants.append(Value.constant(obj.value))
            elif getattr(obj, "is_top_value", False): constants.append(Value.unknown())
            else: refs.add(self.object(obj))
        if old.scalar_kinds is None:
            self.reasons.add("bridge_untagged_scalar_product")
            scalar = Value.unknown()
        else:
            integer, string, boolean = old.get_int_lattice(), old.get_string_lattice(), old.get_bool_values()
            scalar = Value(
                ints=IntLattice(integer.consts,integer.lo,integer.hi,Parity(integer.parity.value),integer.is_bottom,integer.is_top),
                strings=StrLattice(string.consts,string.prefixes,string.suffixes,string.has_unknown,getattr(string,"conjunctions",frozenset())),
                bools=frozenset(x for x in (False,True) if (boolean.may_be_true() if x else boolean.may_be_false())),
                none=old.may_be_none is True,top=old.addr_top)
        return self.driver.ops.join_all([scalar,*constants,Value(refs=frozenset(refs))])

    def field_key(self, field, kind):
        from pythonstan.analysis.pointer.kcfa.heap_model import FieldKind
        if field.kind in {FieldKind.ATTRIBUTE,FieldKind.OWN_ATTRIBUTE}: return field.name
        if field.kind == FieldKind.POSITION: return field.index
        if field.kind == FieldKind.KEY:
            if field.key_type == "number" or kind in {"list","tuple"}:
                try: return int(field.name)
                except (ValueError,TypeError): pass
            return field.name
        if field.kind not in {FieldKind.ELEMENT,FieldKind.VALUE,FieldKind.UNKNOWN,FieldKind.OWN_UNKNOWN,FieldKind.WILDCARD_KEY}:
            self.reasons.add("bridge_field_metadata:"+field.kind.value)
        return None

    def object(self, obj):
        from pythonstan.analysis.pointer.kcfa import object as native
        from pythonstan.analysis.pointer.ai.heap import AIAddr
        from pythonstan.analysis.pointer.kcfa.heap_model import attr, unknown
        if obj in self.objects: return self.objects[obj]
        if isinstance(obj,(native.BuiltinFunctionObject,native.BuiltinClassObject)):
            name=getattr(obj,"builtin_name",None)
            if name is None: name=getattr(obj,"func_name",None)
            identifier="builtin:"+name
            self.objects[obj]=identifier;self.reverse[identifier]=obj
            return identifier
        identifier=f"compat-object:{len(self.objects)}"
        self.objects[obj]=identifier;self.reverse[identifier]=obj
        self.addresses[AIAddr.from_kcfa_obj(obj)]=identifier
        kind=("instance" if isinstance(obj,native.InstanceObject) else "class" if isinstance(obj,native.ClassObject)
              else "function" if isinstance(obj,native.FunctionObject) else "module" if isinstance(obj,native.ModuleObject)
              else "list" if isinstance(obj,native.ListObject) else "tuple" if isinstance(obj,native.TupleObject)
              else "dict" if isinstance(obj,native.DictObject) else "set" if isinstance(obj,native.SetObject)
              else obj.builtin_type if isinstance(obj,native.BuiltinInstanceObject) else "unknown")
        target=self.store.heap[identifier]=HeapObject(kind,singleton=False)
        if kind=="unknown":self.reasons.add("bridge_object_kind:"+type(obj).__name__)
        if isinstance(obj,native.InstanceObject):target.class_ids=frozenset((self.object(obj.class_obj),))
        if isinstance(obj,native.ClassObject):
            mro=self.query.get_class_mro(obj) if self.query is not None else []
            target.metadata["mro"]=tuple(self.object(base) for base in mro if base!=obj)
            target.metadata["bases"]=target.metadata["mro"][:1]
        if isinstance(obj,native.ModuleObject):target.metadata["module"]=obj.ir.get_qualname()
        if isinstance(obj,native.FunctionObject):
            if hasattr(self,"register_function"):self.register_function(obj,identifier)
            else:self.reasons.add("bridge_callable_source_unavailable")
        state=getattr(self.query,"_state",None)
        fields={field for owner,field in tuple(getattr(state,"_field_accesses",())) if owner==obj}
        if kind in {"instance","class"}:
            fields.update(attr(name) for name in ("__getattribute__","__getattr__","__setattr__","__get__","__set__","__delete__"))
            fields.add(unknown())
        for field in fields:
            try: points=frozenset(self.query.field_points_to(obj,field,self.scope,self.context))
            except Exception:
                self.reasons.add("bridge_field_query_failure");target.havoc=True;continue
            self.reads.add((obj,field))
            if not points:continue
            from pythonstan.analysis.pointer.ai.value_domain import AbsVal
            value=self.value(AbsVal.from_addrs(points));key=self.field_key(field,kind)
            if key is None:target.default=self.driver.ops.join(target.default,value)
            else:
                target.fields[key]=self.driver.ops.join(target.fields.get(key,Value.bottom()),value)
                target.maybe_missing.add(key) # Native may-points-to is not a presence proof.
        return identifier
