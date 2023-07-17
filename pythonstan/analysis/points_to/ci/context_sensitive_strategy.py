from .lattice.context import Context
from .lattice.state import State
from .lattice.obj_label import ObjLabel
from .solver_interface import SolverInterface
from pythonstan.ir import *

class ContextSensitiveStrategy:
    @classmethod
    def make_scope_heap_context(cls, socpe: IRScope, c: SolverInterface) -> Context:
        ...

    def make_activation_and_arguments_heap_context(cls, state: State, fn: ObjLabel, callinfo, c: SolverInterface) -> Context:
        ...

    def make_primitive_heap_context(self, primitive: Value) -> Context:
        ...

    def make_initial_context(self) -> Context:
        ...