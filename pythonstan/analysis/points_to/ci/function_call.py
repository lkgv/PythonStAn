from typing import Tuple, Set

from pythonstan.ir import IRScope, IRCall, IRFunc
from pythonstan.graph.cfg import BaseBlock
from .solver_interface import SolverInterface
from .lattice.value import Value
from .lattice.obj_label import ObjLabel, LabelKind
from .lattice.execution_context import ExecutionContext
from .lattice.value_resolver import ValueResolver
from .lattice.call_edge import CallEdge
from .lattice.state import State
from .lattice.context import Context
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


def enter_defined_function(obj_f: ObjLabel, call: CallInfo, c: SolverInterface):
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
                propagate_to_function_entry(call_edge, call.get_call_site(), obj_f, call, c)

    c.with_state(edge_state, enter_function)


def propagate_to_function_entry(edge: CallEdge, call_site: BaseBlock, obj_f: ObjLabel, call: CallInfo,
                                c: SolverInterface):
    scope_ir = call.get_scope_ir()
    callee_entry = c.get_graph().get_cfg(scope_ir).get_entry()
    edge_context = c.get_analysis().get_context_sensitive_strategy() \
        .make_function_entry_context(edge.get_state(), obj_f, call, c)
    c.propagate_to_function_entry(call_site, edge_context, edge, scope_ir, edge_context, callee_entry)


def leave_function(ret_val: Value, scope: IRScope, state: State, c: SolverInterface):
    cg = c.get_analysis_lattice_element().get_call_graph()
    for caller, caller_ctx, edge_ctx in cg.get_sources((state.get_block(), state.get_context())):
        return_to_caller(caller, caller_ctx, edge_ctx, ret_val, scope, state.clone(), c)


def leave_specific_function(ret_val: Value, scope: IRScope, state: State, c: SolverInterface,
                   caller: BaseBlock, caller_ctx: Context, edge_ctx: Context):
    return_to_caller(caller, caller_ctx, edge_ctx, ret_val, scope, state.clone(), c)


def return_to_caller(blk: BaseBlock, caller_ctx: Context, edge_ctx: Context, return_val: Value, scope: IRScope,
                     state: State, c: SolverInterface):
    ir_call = next(iter(blk.get_stmts()))
    if state.is_bottom:
        return
    scope_entry = c.get_graph().get_cfg(scope).get_entry()
    call_edge = c.get_analysis_lattice_element().get_call_graph().get_call_edge(blk, caller_ctx, scope_entry, edge_ctx)
    caller_state = c.get_analysis_lattice_element().get_states(blk).get(caller_ctx)
    return_val = merge_function_return(state, caller_state, call_edge.get_state(),
                                       c.get_analysis_lattice_element().get_state(caller_ctx, blk),
                                       return_val)
    state.set_block(next(iter(c.get_graph().succs_of(blk))))
    state.set_context(caller_ctx)
    if not return_val.is_none():
        c.propagate_to_base_block(state, next(iter(c.get_graph().succs_of(blk))), caller_ctx)


def merge_function_return(return_state: State, caller_state: State, call_edge_state: State, caller_entry_state: State,
                          return_val: Value):
    return_state.make_writable_store()
    store_default = caller_state.get_store_default().freeze()
    return_state.set_store_default(store_default)
    caller_state.set_store_default(store_default)
    for l, _ in return_state.get_store().items():
        obj = return_state.get_obj(l, True)
        call_edge_obj = call_edge_state.get_obj(l, False)
        obj.replace_non_modified_parts(call_edge_obj)
    for l, obj in call_edge_state.get_store().items():
        if l not in return_state.get_store():
            return_state.put_obj(l, obj)
    return_state.set_execution_context(caller_entry_state.get_execution_context().clone())
    return_state.set_stacked({o for o in caller_state.get_stacked_objs()},
                             {sse for sse in caller_state.get_stacked_scope_entries()})
    res = return_val
    return res



