from typing import Tuple, Set

from pythonstan.ir import IRScope, IRCall, IRFunc
from pythonstan.graph.cfg import BaseBlock
from .solver_interface import SolverInterface
from .lattice.value import Value
from .lattice.obj_label import ObjLabel, LabelKind
from .lattice.execution_context import ExecutionContext
from .lattice.value_resolver import ValueResolver
from .lattice.call_edge import CallEdge
from .call_info import CallInfo
from .predefined_functions import evaluate_predefined_function


def call_function(call: CallInfo, c: SolverInterface):
    caller_state = c.get_state()
    fun_val = call.get_function_value()
    for l in fun_val.get_obj_labels():
        if l.get_kind() == LabelKind.Function:
            if l.is_predefined():
                def deal_with_predefined_function():
                    if not call.is_constructor():
                        old_ec = c.get_state().get_execution_context()
                        c.get_state().set_execution_context(ExecutionContext(old_ec.scope_chain, call.get_scope_value()))
                    assert l.get_scope() is not None, "Predefined Function Label shoud have predefined IRScope!"
                    res_val: Value = evaluate_predefined_function(l.get_scope(), call, c)
                    if not res_val.is_none() and not c.get_state().is_bottom:
                        c.get_state().set_execution_context(call.get_execution_context())
                        next_block = c.get_graph().succs_of(call.get_call_site())
                        c.propagate_to_base_block(c.get_state(), next_block, c.get_state().get_context())

                c.with_state(caller_state.clone(), deal_with_predefined_function)
            else:
                enter_defined_function(l, call, False, c)
    if len(fun_val.get_obj_labels()) == 0:
        new_state = caller_state.clone()
        if call.get_call_ir().get_target() is not None:
            tgt_name = call.get_call_ir().get_target()
            ValueResolver().set_var(tgt_name, Value.make_none(), new_state)
        c.propagate_to_base_block(new_state, c.get_graph().succs_of(call.get_call_site())[0], new_state.get_context())

def enter_defined_function(obj_f: ObjLabel, call: CallInfo, implicit: bool, c: SolverInterface):
    caller_state = c.get_state()
    edge_state = caller_state.clone()

    def enter_function():
        if call.is_constructor():
            obj_heap_ctx = c.get_analysis().get_context_sensitive_strategy() \
                .make_constructor_heap_context(edge_state, obj_f, call, c)
            new_obj = ObjLabel.make(call.get_call_site(), LabelKind.Object, obj_heap_ctx)
            edge_state.new_obj(new_obj)
            obj = edge_state.get_obj(new_obj, True)
            obj.set_cls_label(obj_f)
            self_val = Value.make_obj([new_obj])
        else:
            self_val = call.get_self_value()
        new_ec = ExecutionContext(edge_state.get_execution_context().scope_chain, self_val)
        edge_state.set_execution_context(new_ec)
        heap_ctx = c.get_analysis().get_context_sensitive_strategy()\
            .make_activation_and_arguments_heap_context(edge_state, obj_f, call, c)

        # add args

        if len(self_val.get_obj_labels()) > 1:
            for i, self_obj in enumerate(self_val.get_obj_labels()):
                cur_edge_state = edge_state if i == 0 else edge_state.clone()
                cur_edge_state.get_execution_context().set_self_var(Value.make_obj([self_obj]))
                call_edge = CallEdge(cur_edge_state)
                propagate_to_function_entry(call_edge, call.get_call_ir(), obj_f, call, implicit, c)

    c.with_state(edge_state, enter_function)


def propagate_to_function_entry(edge: CallEdge, call_ir: IRCall, obj_f: ObjLabel, call: CallInfo,
                                implicit: bool, c: SolverInterface):
    scope_ir = call.get_scope_ir()
    edge_context = c.get_analysis().get_context_sensitive_strategy() \
        .make_function_entry_context(edge.get_state(), obj_f, call, c)
    c.propagate_to_function_entry(call_ir, edge_context)



