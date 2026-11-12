"""Actual Native projections distinguish statement completion from returned None."""

from __future__ import annotations

import subprocess
import sys

from pythonstan.analysis.contracts import SourceModule
from pythonstan.analysis.contracts.budgets import AnalysisBudget
from pythonstan.analysis.contracts.identity import ContextId, FrameId, ObjectId
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.pointer.kcfa.context import CallStringContext
from pythonstan.analysis.pointer.kcfa.object import UnknownValue
from pythonstan.analysis.state.flow import ExceptionValue, NormalExprFlow, NormalStmtFlow, RaiseFlow
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import Frame, HeapObject, Store
from pythonstan.compat.legacy_ai.state import AIState
from pythonstan.compat.shared_ai import LegacyStateBridge, SharedDriver, options


def boundary() -> tuple[LegacyStateBridge, Store, Frame]:
    budget = AnalysisBudget()
    driver = SharedDriver((SourceModule("subject", "subject.py", ""),), options(budget))
    bridge = LegacyStateBridge(
        None, budget, CallStringContext(), None, AIState(budget=budget), driver
    )
    frame = Frame(
        "subject", "subject", ContextId(()), FrameId("legacy-entry:subject"), is_module=True
    )
    return bridge, Store(), frame


def test_statement_completion_retains_state_without_inventing_a_return_value() -> None:
    bridge, store, frame = boundary()
    store.write_cell(frame.cell("retained"), Value.constant("kept"))
    summary = bridge.summary((NormalStmtFlow(StateHandle.capture(store)),), frame)
    result = summary.ret_value
    assert summary.normal_exit and result is not None and result.is_bottom()
    assert summary.exit_state is not None
    assert summary.exit_state.env["retained"].get_const_strs() == frozenset(("kept",))


def test_returned_none_and_raised_value_remain_distinct_from_no_value() -> None:
    bridge, store, frame = boundary()
    normal = NormalExprFlow(StateHandle.capture(store), Value.constant(None))
    raised = RaiseFlow(
        StateHandle.capture(store.copy()), ExceptionValue(Value.constant("fault"), "TypeError")
    )
    summary = bridge.summary((normal, raised), frame)
    result = summary.ret_value
    assert result is not None and result.may_be_none and not result.is_bottom()
    assert summary.may_raise == {"TypeError"}
    assert summary.exceptional_exit_states[0][0] == "TypeError"


def test_standalone_projection_reports_unavailable_source_prototype() -> None:
    bridge, store, _ = boundary()
    identifier = ObjectId("unavailable-source-function")
    store.heap[identifier] = HeapObject("function")
    assert isinstance(bridge.old_object(identifier, store), UnknownValue)
    assert "bridge_outgoing_function_prototype" in bridge.reasons


def test_native_global_projection_skips_flow_nodes_and_preserves_variable_facts() -> None:
    # Exercise the real legacy producer in a fresh process: its accepted node
    # keys are broader than the source-global projection's Variable subset.
    fixture = """
import ast
from pythonstan.analysis.contracts import SourceModule
from pythonstan.analysis.contracts.budgets import AnalysisBudget
from pythonstan.analysis.contracts.identity import CellId
from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Ctx, Scope
from pythonstan.analysis.pointer.kcfa.heap_model import attr
from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite, ConstantObject, ModuleObject
from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
from pythonstan.analysis.pointer.kcfa.pta_query_adapter import PtaQueryAdapter
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.variable import FieldAccess, Variable, VariableKind
from pythonstan.compat.legacy_ai.state import AIState
from pythonstan.compat.shared_ai import CodeBridge, SharedDriver, options
from pythonstan.ir.ir_statements import IRModule
from pythonstan.world import World
World.setup()
World().scope_manager.build()
source = SourceModule('subject', 'subject.py', "retained = 'source'\\n")
context = CallStringContext()
module = IRModule('subject', ast.parse(source.code), filename=source.filename, source_snapshot=source)
owner = ModuleObject(context, AllocSite(module, AllocKind.MODULE), module)
scope = Scope(module, owner, context, None, None)
state = PointerAnalysisState()
slot = Ctx(context, scope, Variable('retained', VariableKind.GLOBAL))
constant = ConstantObject(None, AllocSite(None, AllocKind.CONSTANT), 'native')
points = PointsToSet.singleton(constant)
state.set_points_to(NormalNode(slot), points)
state.set_points_to(Ctx(context, None, FieldAccess(owner, attr('ignored'))), points)
state.set_points_to(slot, points)
budget = AnalysisBudget()
driver = SharedDriver((source,), options(budget))
bridge = CodeBridge(PtaQueryAdapter(state), budget, context, scope, AIState(budget=budget), driver)
bridge.seed_native_globals('subject')
assert bridge.store.cells[CellId('global:subject:retained')].exact_constants() == ['native']
assert len(state._env) == 3
print('preserved')
"""
    completed = subprocess.run(
        [sys.executable, "-c", fixture], capture_output=True, text=True, timeout=10, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "preserved"
