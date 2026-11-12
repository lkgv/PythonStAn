"""Optional legacy intrinsic bridge, injected outside either concrete engine.

It only publishes additional may-facts after a complete compatible summary.
Persistent Native builtin models still run and retain late-fact replay.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, TypeGuard

from pythonstan.analysis.contracts.budgets import AnalysisBudget
from pythonstan.analysis.contracts.work import WorkLedgerPort
from pythonstan.analysis.pointer.kcfa.context import NativeContext, Scope
from pythonstan.analysis.pointer.kcfa.object import (
    AbstractObject,
    BuiltinFunctionObject,
    ConstantObject,
)
from pythonstan.analysis.pointer.kcfa.pta_query_adapter import PtaQueryAdapter
from pythonstan.compat.intrinsic_boundary import IntrinsicEnginePort, checked_intrinsic_summary
from pythonstan.compat.legacy_ai.api import AISummaryEngine, ArgBinding
from pythonstan.compat.legacy_ai.value_domain import AbsVal

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint
    from pythonstan.analysis.pointer.kcfa.solver import PointerSolver


class LegacyIntrinsicProvider:
    def __init__(
        self,
        *,
        engine: IntrinsicEnginePort | None = None,
        adapter: PtaQueryAdapter | None = None,
    ) -> None:
        self.engine = engine
        self.adapter = adapter

    def supports(self, value: AbstractObject) -> TypeGuard[BuiltinFunctionObject]:
        return isinstance(value, BuiltinFunctionObject) and value.function_name in {
            "getattr",
            "setattr",
            "hasattr",
        }

    def apply(
        self,
        host: PointerSolver,
        scope: Scope,
        context: NativeContext,
        call: CallConstraint,
        builtin: AbstractObject,
        work_ledger: WorkLedgerPort,
    ) -> bool:
        if not self.supports(builtin):
            return False
        if not work_ledger.consume(kind="intrinsic_bridge"):
            host.intrinsic_fallback(scope, call, "shared_work_budget")
            return False
        name = builtin.function_name
        invocation = host.begin_intrinsic(scope, context, call, builtin)
        points = [host.intrinsic_points(scope, context, argument) for argument in call.args]
        required = 3 if name == "setattr" else 2
        if len(points) >= required and any(not values for values in points):
            host.intrinsic_counter("deferred")
            return False
        if work_ledger.remaining <= 0:
            host.intrinsic_fallback(scope, call, "shared_work_budget")
            return False
        budget = AnalysisBudget.from_options(host.intrinsic_budget_options())
        budget = replace(budget, max_iterations=min(budget.max_iterations, work_ledger.remaining))
        adapter = self.adapter
        if adapter is None:
            adapter = host.make_intrinsic_adapter()
            self.adapter = adapter
        engine = self.engine
        if engine is None:
            engine = AISummaryEngine(adapter, budget, work_ledger=work_ledger)
            self.engine = engine
        else:
            engine.budget = budget
            engine.work_ledger = work_ledger
        parameter_names = (
            ["obj", "name", "value"]
            if name == "setattr"
            else ["obj", "name", "default"]
            if name == "getattr"
            else ["obj", "name"]
        )
        bindings: list[ArgBinding] = []
        for parameter, values in zip(parameter_names, points, strict=False):
            if parameter == "name" and values:
                names: set[str] = set()
                only_names = True
                for obj in values:
                    if not isinstance(obj, ConstantObject) or not isinstance(obj.value, str):
                        only_names = False
                        break
                    names.add(obj.value)
                value = (
                    AbsVal.from_strs(frozenset(names))
                    if only_names
                    else AbsVal.from_addrs(frozenset(values))
                )
            else:
                value = AbsVal.from_addrs(frozenset(values))
            bindings.append(ArgBinding(parameter, frozenset(values), value))
        target = call.target.name if call.target is not None else None
        adapter.on_field_read = lambda variable: host.watch_intrinsic_read(invocation, variable)
        adapter.on_attribute_names_read = lambda obj: host.watch_intrinsic_object(invocation, obj)
        try:
            host.intrinsic_counter("attempts")
            summary = checked_intrinsic_summary(
                engine.analyze_intrinsic(
                    builtin_name=name,
                    call_context=context,
                    arg_bindings=bindings,
                    caller_scope=scope,
                    target_name=target,
                )
            )
            if not summary.is_complete:
                reasons = ", ".join(summary.incomplete_reasons) or "incomplete summary"
                host.intrinsic_fallback(scope, call, reasons)
                return False
            if summary.calls or summary.unknown_effects or summary.may_raise:
                host.intrinsic_fallback(
                    scope, call, "summary has unrepresentable call/unknown/exception effects"
                )
                return False
            for objects, field in summary.reads:
                for obj in objects:
                    host.watch_intrinsic_field(invocation, scope, context, obj, field)
            changed = False
            if call.target is not None and summary.ret:
                host.publish_intrinsic_return(scope, context, call.target, summary.ret)
                changed = True
            for write in summary.writes:
                for obj in write.obj_addrs:
                    host.publish_intrinsic_write(
                        scope, context, obj, write.field_key, write.value_addrs
                    )
                    changed = True
            if changed or summary.ret or summary.writes:
                host.intrinsic_counter("applied")
                return True
            return False
        except Exception as error:
            if budget.strict_exceptions:
                raise
            host.intrinsic_fallback(
                scope, call, f"intrinsic provider error: {type(error).__name__}: {error}"
            )
            return False
        finally:
            adapter.on_field_read = None
            adapter.on_attribute_names_read = None
