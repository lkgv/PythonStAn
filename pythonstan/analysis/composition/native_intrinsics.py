"""Optional legacy intrinsic bridge, injected outside either concrete engine.

It only publishes additional may-facts after a complete compatible summary.
Persistent Native builtin models still run and retain late-fact replay.
"""

from pythonstan.analysis.pointer.kcfa.object import BuiltinFunctionObject, ConstantObject


class LegacyIntrinsicProvider:
    def __init__(self, *, engine=None, adapter=None):
        self.engine = engine
        self.adapter = adapter

    def supports(self, value):
        return isinstance(value, BuiltinFunctionObject) and value.function_name in {
            "getattr",
            "setattr",
            "hasattr",
        }

    def apply(self, host, scope, context, call, builtin, work_ledger):
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
        from pythonstan.analysis.pointer.ai.api import AISummaryEngine, AnalysisBudget, ArgBinding
        from pythonstan.analysis.pointer.ai.state import AbsVal

        options = host.intrinsic_budget_options()
        options["max_iterations"] = min(options.get("max_iterations", 100), work_ledger.remaining)
        budget = AnalysisBudget(**options)
        if self.adapter is None:
            self.adapter = host.make_intrinsic_adapter()
        if self.engine is None:
            self.engine = AISummaryEngine(self.adapter, budget, work_ledger=work_ledger)
        else:
            self.engine.budget = budget
            self.engine.work_ledger = work_ledger
        parameter_names = (
            ["obj", "name", "value"]
            if name == "setattr"
            else ["obj", "name", "default"]
            if name == "getattr"
            else ["obj", "name"]
        )
        bindings = []
        for parameter, values in zip(parameter_names, points):
            if (
                parameter == "name"
                and values
                and all(
                    isinstance(obj, ConstantObject) and isinstance(obj.value, str) for obj in values
                )
            ):
                value = AbsVal.from_strs(frozenset(obj.value for obj in values))
            else:
                value = AbsVal.from_addrs(frozenset(values))
            bindings.append(ArgBinding(parameter, frozenset(values), value))
        target = call.target.name if call.target is not None else None
        self.adapter.on_field_read = lambda variable: host.watch_intrinsic_read(
            invocation, variable
        )
        self.adapter.on_attribute_names_read = lambda obj: host.watch_intrinsic_object(
            invocation, obj
        )
        try:
            host.intrinsic_counter("attempts")
            summary = self.engine.analyze_intrinsic(
                builtin_name=name,
                call_context=context,
                arg_bindings=bindings,
                caller_scope=scope,
                target_name=target,
            )
            if not getattr(summary, "is_complete", False):
                reasons = (
                    ", ".join(getattr(summary, "incomplete_reasons", ())) or "incomplete summary"
                )
                host.intrinsic_fallback(scope, call, reasons)
                return False
            if (
                getattr(summary, "calls", ())
                or getattr(summary, "unknown_effects", ())
                or getattr(summary, "may_raise", ())
            ):
                host.intrinsic_fallback(
                    scope, call, "summary has unrepresentable call/unknown/exception effects"
                )
                return False
            for objects, field in getattr(summary, "reads", ()):
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
            if options.get("strict_exceptions", False):
                raise
            host.intrinsic_fallback(
                scope, call, f"intrinsic provider error: {type(error).__name__}: {error}"
            )
            return False
        finally:
            self.adapter.on_field_read = None
            self.adapter.on_attribute_names_read = None
