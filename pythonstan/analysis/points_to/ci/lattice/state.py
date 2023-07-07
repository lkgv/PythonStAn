from typing import Set, Tuple, Optional, List

from pythonstan.utils.persistent_rb_tree import PersistentMap
from pythonstan.graph.cfg import BaseBlock
from ..solver import SolverInterface
from pythonstan.analysis.points_to.ci.lattice.context import Context
from .value import Value
from .obj import Obj
from .obj_label import ObjLabel



class State:
    is_bottom: bool
    c: SolverInterface
    block: BaseBlock
    context: Context
    store: PersistentMap[ObjLabel, Obj]
    store_default: Obj
    basis_store: PersistentMap[ObjLabel, Obj]
    registers: List[Value]
    stacked_obj_labels: Set[ObjLabel]
    stacked_scope_entries: Set[Tuple[BaseBlock, Context]]
    store_default: Obj

    memory: PersistentMap[str, Value]

    # TODO add mustequals

    def __init__(self, c=None, blk=None, s: Optional['State'] = None):
        if s is None:
            self.c = c
            self.block = blk
            self.set_to_bottom()
        else:
            self.c = s.c
            self.memory = PersistentMap()
            self.block = s.block
            self.context = s.context
            self.set_to_state(s)

    def set_to_state(self, s: 'State'):
        self.store_default = s.store_default
        self.store = PersistentMap()
        self.basis_store = s.basis_store
        self.registers = [x for x in s.registers]
        self.stacked_obj_labels = {x for x in s.stacked_obj_labels}
        self.stacked_scope_entries = {x for x in s.stacked_scope_entries}
        self.memory.recover(s.memory.backup())


    def set_to_bottom(self):
        self.store = PersistentMap()
        self.registers = []
        self.stacked_obj_labels = {*()}
        self.stacked_scope_entries = {*()}
        self.store_default = Obj.make_none()

    def set_block(self, block: BaseBlock):
        self.block = block

    def get_block(self) -> BaseBlock:
        return self.block

    def set_context(self, context: Context):
        self.context = context

    def get_context(self) -> Context:
        return self.context

    def write_store(self, obj_label: ObjLabel, obj: Obj):
        self.store[obj_label] = obj

    def remove_obj(self, obj_label: ObjLabel):
        del self.store[obj_label]

    def get_obj(self, obj_label: ObjLabel):
        obj = self.store.get[obj_label]
        if obj is not None:
            obj = Obj(obj)
            self.write_store(obj_label, obj)
        if obj is None and hasattr(self, 'basis_store'):
            obj = self.basis_store.get(obj_label)
            if obj is not None:
                obj = obj(obj)
                self.write_store(obj_label, obj)
        if obj is None:
            obj = Obj(self.store_default)
            self.write_store(obj_label, obj)
        return obj

    def set_store_default(self, obj: Obj):
        self.store_default = obj

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

    def read_memory(self, var: str) -> Optional['Value']:
        return self.memory[var]

    def write_memory(self, var: str, value: Value):
        self.memory[var] = value

