from typing import Tuple, Set

from pythonstan.ir import IRScope, IRCall
from pythonstan.graph.cfg import BaseBlock
from .solver_interface import SolverInterface
from .lattice.value import Value
from .lattice.obj_label import ObjLabel, LabelKind
from .lattice.execution_context import ExecutionContext
from .lattice.value_resolver import ValueResolver


class CallInfo:
    def get_call_site(self) -> BaseBlock:
        ...

    def is_constructor(self) -> bool:
        ...

    def get_function_value(self) -> Value:
        ...

    def get_scope_value(self) -> Value:
        ...

    def get_arg(self, i: int) -> Tuple[Value, bool]:
        ...

    def get_keywords(self, key: str) -> Value:
        ...

    def get_packed_keywords(self) -> Set[ObjLabel]:
        ...

    def get_result(self) -> Value:
        ...

    def get_execution_context(self) -> ExecutionContext:
        ...

    def get_scope_ir(self) -> IRScope:
        ...

    def get_call_ir(self) -> IRCall:
        ...


def call_function(call: CallInfo, c: SolverInterface):
    caller_state = c.get_state()
    fun_val = call.get_function_value()
    for l in fun_val.get_obj_labels():
        if l.get_kind() == LabelKind.Function:
            ...
    if len(fun_val.get_obj_labels()) == 0:
        new_state = caller_state.clone()
        if call.get_call_ir().get_target() is not None:
            tgt_name = call.get_call_ir().get_target()
            ValueResolver().set_var(tgt_name, Value.make_none(), new_state)
        c.propagate_to_base_block(new_state, c.get_graph().succs_of(call.get_call_site())[0], new_state.get_context())

def enter_defined_function(obj_f: ObjLabel, call: CallInfo, implicit: bool, c: SolverInterface):
    caller_state = c.get_state()
    


