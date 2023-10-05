from typing import Tuple, Set

from pythonstan.ir import IRScope, IRCall
from pythonstan.graph.cfg import BaseBlock
from .lattice.value import Value
from .lattice.obj_label import ObjLabel, LabelKind
from .lattice.execution_context import ExecutionContext


class CallInfo:
    def get_call_site(self) -> BaseBlock:
        ...

    def is_constructor(self) -> bool:
        ...

    def get_function_value(self) -> Value:
        ...

    def get_self_value(self) -> Value:
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
