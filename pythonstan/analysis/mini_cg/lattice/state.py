from typing import Set, Tuple, Optional, List, Dict

from pythonstan.utils.persistent_rb_tree import PersistentMap
from pythonstan.graph.cfg import BaseBlock
from ..solver_interface import SolverInterface
from .context import Context
from .value import Value
from .obj import Obj
from .obj_label import ObjLabel, LabelKind
from .execution_context import ExecutionContext
from .scope_chain import ScopeChain
from .summarized import Summarized



class State:
    is_bottom: bool
    c: SolverInterface
    block: BaseBlock
    context: Context
    store: Dict[ObjLabel, Obj]
    store_default: Obj
    basis_store: Dict[ObjLabel, Obj]
    stacked_obj_labels: Set[ObjLabel]
    stacked_scope_entries: Set[Tuple[BaseBlock, Context]]
    execution_context: ExecutionContext

    writable_store: bool
    writable_execution_context: bool
    writable_stacked: bool

    number_of_status_created = 0
    number_of_makewritable_store = 0

    @classmethod
    def gen_init_state(cls, c, blk) -> 'State':
        s = cls(c, blk)

        return s

    @classmethod
    def reset(cls):
        cls.number_of_makewritable_store = 0
        cls.number_of_status_created = 0

    # TODO add mustequals

    def __init__(self, c=None, blk=None,
                 s: Optional['State'] = None):
        if s is None:
            self.c = c
            self.block = blk
            self.set_to_bottom()
        else:
            self.set_to_state(s)
        self.number_of_status_created += 1

    def set_to_state(self, s: 'State'):
        self.context = s.context
        self.store_default = s.store_default
        self.store = {k: v for k, v in s.store.items()}
        self.writable_store = True
        self.basis_store = s.basis_store
        self.execution_context = s.execution_context.clone()
        self.writable_execution_context = True
        self.stacked_obj_labels = {x for x in s.stacked_obj_labels}
        self.stacked_scope_entries = {x for x in s.stacked_scope_entries}
        self.writable_stacked = True

    def set_context(self, ctx: Context):
        self.context = ctx

    def clone(self) -> 'State':
        ret = State()
        ret.set_to_state(self)
        return ret

    def get_store(self) -> PersistentMap[ObjLabel, Obj]:
        return self.store

    def set_to_bottom(self):
        self.store = {}
        self.basis_store = {}
        self.registers = []
        self.stacked_obj_labels = {*()}
        self.stacked_scope_entries = {*()}
        self.store_default = Obj.make_none()
        self.execution_context = ExecutionContext()

    def set_block(self, block: BaseBlock):
        self.block = block

    def get_block(self) -> BaseBlock:
        return self.block

    def set_context(self, context: Context):
        self.context = context

    def get_context(self) -> Context:
        return self.context

    def get_execution_context(self) -> ExecutionContext:
        return self.execution_context

    def set_execution_context(self, e: ExecutionContext):
        self.execution_context = e
        self.writable_execution_context = True

    def set_stacked(self, so: Set[ObjLabel], sse: Set[Tuple[BaseBlock, Context]]):
        self.stacked_obj_labels = so
        self.stacked_scope_entries = sse
        self.writable_stacked = True

    def get_stacked_objs(self) -> Set[ObjLabel]:
        return self.stacked_obj_labels

    def make_writable_store(self):
        if not self.writable_store:
            backup = self.store.backup()
            self.store = PersistentMap(backup)
            self.writable_store = True
            self.number_of_makewritable_store += 1

    def make_writable_execution_context(self):
        if not self.writable_execution_context:
            self.execution_context = self.execution_context.clone()
            self.writable_execution_context = True

    def make_writable_stacked(self):
        if not self.writable_stacked:
            self.stacked_obj_labels = {x for x in self.stacked_obj_labels}
            self.stacked_scope_entries = {x for x in self.stacked_scope_entries}
            self.writable_stacked = True

    def write_store(self, obj_label: ObjLabel, obj: Obj):
        self.store[obj_label] = obj

    def put_obj(self, obj_label: ObjLabel, obj: Obj):
        self.make_writable_store()
        self.write_store(obj_label, obj)

    def remove_obj(self, obj_label: ObjLabel):
        self.make_writable_store()
        self.store.delete(obj_label)

    def remove_obj(self, obj_label: ObjLabel):
        del self.store[obj_label]

    def get_var(self, var_name: str) -> Optional[Value]:
        return self.execution_context.get_var(var_name)

    def set_var(self, var_name: str, value: Value) -> bool:
        return self.execution_context.set_var(var_name, value)

    def write_obj_scope(self, obj_label: ObjLabel, sc: Optional[ScopeChain]):
        if obj_label.get_kind() == LabelKind.Function and sc is None:
            raise AssertionError("Empty scope chain for function!")
        self.get_obj(obj_label, True).set_scope_chain(sc)

    #########
    # TODO renew obj structure
    # scope_chain, self_scope, base_classes, ...

    def new_obj(self, obj_label: ObjLabel, is_recency_disabled: bool = False):
        from .value_resolver import ValueResolver
        v = ValueResolver()

        if self.basis_store is not None and obj_label in self.basis_store:
            raise AssertionError("Attempt to summary object from basis store")
        assert obj_label.is_singleton(), "Expected singleton object label"
        self.make_writable_store()
        if is_recency_disabled:
            obj = self.get_obj(obj_label, True)
            obj.set_default_other_property(v.get_default_other_property(obj_label, self))
            obj.set_internal_value(v.get_internal_value(obj_label, self).join_absent_modified())
            obj.set_cls(v.get_cls(obj_label, self).join_absent_modified())
        else:
            self.summarize_obj(obj_label, obj_label.make_summary(), Obj.make_absent_modified())

    def summarize_obj(self, singleton: ObjLabel, summary: ObjLabel, new_obj: Obj):
        old_obj = self.get_obj(singleton)
        if not old_obj.is_some_none():
            self.propagate_obj(summary, self, singleton, True, False)
            cache = {}
            for l, _ in self.store.items():
                if self.get_obj(l).contains_obj_label(singleton):
                    self.get_obj(l).replace_obj_label(singleton, summary, cache)
            self.execution_context.replace_obj_label(singleton, summary, cache)
            for i in range(len(self.registers)):
                self.registers[i] = self.registers[i].replace_obj_label(singleton, summary)
            if singleton in self.stacked_obj_labels:
                self.stacked_obj_labels.remove(singleton)
                self.stacked_obj_labels.add(summary)
            if self.get_obj(summary).is_unknown() and self.store_default.is_unknown():
                self.store.delete(summary)
        self.write_store(singleton, new_obj)


    # TODO end
    ###########

    def propagate_obj(self, obj_label_to: ObjLabel, state_from: 'State',
                      obj_label_from: ObjLabel, modified: bool, widen: bool) -> bool:
        obj_from = state_from.get_obj(obj_label_from)
        obj_to = self.get_obj(obj_label_to)
        if obj_to == obj_from and not modified:
            return False
        if obj_from.is_all_none():
            return False
        changed = False
        ...



    def get_obj(self, obj_label: ObjLabel, writable: bool = False):
        if writable:
            self.make_writable_store()
        obj = self.store.get(obj_label)
        if obj is not None and not obj.is_writable() and writable:
            obj = Obj.from_obj(obj)
            self.write_store(obj_label, obj)
        if obj is None and hasattr(self, 'basis_store'):
            obj = self.basis_store.get(obj_label)
            if obj is not None and writable:
                obj = Obj.from_obj(obj)
                self.write_store(obj_label, obj)
        if obj is None:
            obj = self.store_default
            if writable:
                obj = Obj.from_obj(obj)
                self.write_store(obj_label, obj)
        return obj

    def set_store_default(self, obj: Obj):
        self.store_default = obj

    def get_store_default(self) -> Obj:
        return self.store_default

    def remove_default_from_store(self, default_none_at_entry: bool):
        for k, v in self.store.items():
            if v == self.store_default:
                del self.store[k]
            elif default_none_at_entry and self.store_default.is_unknow() and v.is_some_none():
                del self.store[k]

    def get_stacked_scope_entries(self):
        return self.stacked_scope_entries

    def is_bottom(self):
        raise NotImplementedError

    def clone(self) -> 'State':
        return State(s=self)

    def propagate(self, s: 'State', is_entry: bool, widen: bool = False) -> bool:
        ...

    @staticmethod
    def summarize(vs: List[Value], s: Summarized) -> List[Value]:
        return [v.summarize(s) for v in vs]

    def read_property_direct(self, obj_label: ObjLabel, prop_str: Value) -> Value:
        if prop_str.is_maybe_single_str():
            return ValueResolver.get_property(...)


    def read_property_raw(self, obj_labels: Set[ObjLabel], prop_str: Value) -> Value:
        values = []
        for ol in obj_labels:
            ol2 = {*()}
            for l in ol:
                ...


    def read_var(self, name: str) -> Value:
        values = []
        definitely_found = False
        for sc in ScopeChain.iterator(self.execution_context.get_scope_chain()):
            definitely_found = True
            for obj_label in sc.get_obj():
                v: Value = self.read_property_raw({obj_label}, Value.make_str(name))
                if v.is_maybe_present():
                    v2 = v.restrict_to_not_absent()
                    values.append(v2)
                if v.is_maybe_absent():
                    definitely_found = False
            if definitely_found:
                break
        if not definitely_found:
            values.append(Value.make_absent())
        res = Value.join_values(values)
        return res







    # def write_property_raw