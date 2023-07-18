from .lattice.context import Context
from .lattice.state import State
from .lattice.value import Value
from .lattice.obj_label import ObjLabel
from .solver_interface import SolverInterface
from .call_info import CallInfo
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

    def make_constructor_heap_context(self, state: State, fn_label: ObjLabel, call_info: CallInfo, c: SolverInterface):
        ...