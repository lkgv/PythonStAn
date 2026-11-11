"""Iterator and generator protocol semantics for AI abstract interpretation."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Tuple

from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite, AbstractObject
from pythonstan.analysis.pointer.kcfa.context import CallStringContext

from ..api import ArgBinding, AnalysisBudget, AISummary
from ..heap import AIAddr, AbsObj, ObjKind
from ..state import AIState, AbsVal
from .mapslots import Presence, SlotMap, keysig_kind

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext, Scope
    from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
    from pythonstan.analysis.pointer.ai.domains.attributes import AttributeSemantics
    from pythonstan.analysis.pointer.ai.domains.calls import CallSemantics
    from pythonstan.analysis.pointer.ai.api import PtaQuery
    from pythonstan.analysis.pointer.ai.solver import AISolver
    from pythonstan.ir.ir_statements import IRFunc, IRStatement

__all__ = [
    "IteratorBuiltinsSemantics",
    "GeneratorSemantics",
    "is_generator_function",
]

GEN_FUNC_KEY = "__gen_func__"
GEN_YIELDED_KEY = "__yielded__"
GEN_CLOSED_KEY = "__closed__"
GEN_ARG_PREFIX = "__gen_arg__:"

_GENERATOR_FUNC_CACHE: Dict[int, bool] = {}


def is_generator_function(scope_ir: "IRFunc") -> bool:
    """Check if a scope IR contains IRYield statements."""
    cache_key = id(scope_ir)
    cached = _GENERATOR_FUNC_CACHE.get(cache_key)
    if cached is not None:
        return cached

    ir_stmts = _get_ir_statements(scope_ir)
    if ir_stmts is None:
        _GENERATOR_FUNC_CACHE[cache_key] = False
        return False

    from pythonstan.ir.ir_statements import IRYield

    for stmt in ir_stmts:
        if isinstance(stmt, IRYield):
            _GENERATOR_FUNC_CACHE[cache_key] = True
            return True

    _GENERATOR_FUNC_CACHE[cache_key] = False
    return False


def _get_ir_statements(scope_ir: "IRFunc") -> Optional[List["IRStatement"]]:
    from pythonstan.world import World

    world = World()
    if not hasattr(world, "scope_manager") or world.scope_manager is None:
        return None
    ir_stmts = world.scope_manager.get_ir(scope_ir, "ir")
    if ir_stmts is None or not isinstance(ir_stmts, list):
        return None
    return ir_stmts


def _get_call_context(
    call_context: Optional["AbstractContext"],
) -> "AbstractContext":
    if call_context is not None:
        return call_context
    return CallStringContext()


@dataclass(frozen=True)
class _DispatchResult:
    state: AIState
    result_val: AbsVal


class IteratorBuiltinsSemantics:
    """Semantics for iter/next builtins and iterator protocol."""

    def __init__(
        self,
        attr_semantics: "AttributeSemantics",
        call_semantics: "CallSemantics",
        pta_query: Optional["PtaQuery"],
        caller_scope: Optional["Scope"],
        call_context: Optional["AbstractContext"],
        budget: Optional[AnalysisBudget],
        recursion_depth: int = 0,
    ) -> None:
        self.attr_semantics = attr_semantics
        self.call_semantics = call_semantics
        self.pta_query = pta_query
        self.caller_scope = caller_scope
        self.call_context = call_context
        self.budget = budget
        self.recursion_depth = recursion_depth
        self.gen_semantics = GeneratorSemantics(
            pta_query=pta_query,
            budget=budget,
            caller_scope=caller_scope,
            call_context=call_context,
            recursion_depth=recursion_depth,
        )

    def eval_iter(
        self,
        obj_val: AbsVal,
        state: AIState,
        summary: "SummaryAccumulator",
        callsite_id: str,
    ) -> Tuple[AIState, AbsVal]:
        result_val = AbsVal.bottom()
        new_state = state

        for obj in obj_val.addrs:
            ai_obj = self._get_ai_obj(state, obj)
            if ai_obj is not None and ai_obj.kind == ObjKind.ITERATOR:
                result_val = result_val.join(AbsVal.from_addrs(frozenset({obj})))
                continue

            iter_val = self.attr_semantics.read_attr(
                frozenset({obj}),
                "__iter__",
                new_state,
                summary,
            )
            if iter_val.is_bottom():
                summary.add_unknown_effect("may_call", frozenset({obj}))
                result_val = result_val.join(AbsVal.top())
                continue

            dispatch = self._dispatch_call(
                iter_val,
                [],
                {},
                callsite_id,
                new_state,
                summary,
            )
            new_state = dispatch.state
            result_val = result_val.join(dispatch.result_val)

        if not obj_val.addrs:
            result_val = result_val.join(AbsVal.top())

        return new_state, result_val

    def eval_next(
        self,
        it_val: AbsVal,
        state: AIState,
        summary: "SummaryAccumulator",
        callsite_id: str,
        default_val: Optional[AbsVal] = None,
    ) -> Tuple[AIState, AbsVal]:
        result_val = AbsVal.bottom()
        new_state = state
        send_val = AbsVal.none_val()

        for obj in it_val.addrs:
            ai_obj = self._get_ai_obj(state, obj)
            if ai_obj is not None and self.gen_semantics.is_generator_obj(ai_obj):
                new_state, gen_result, _updates = self.gen_semantics.eval_next(
                    AbsVal.from_addrs(frozenset({obj})),
                    new_state,
                    summary,
                    send_val=send_val,
                    default_val=default_val,
                    allow_cache=True,
                )
                result_val = result_val.join(gen_result)
                continue

            next_val = self.attr_semantics.read_attr(
                frozenset({obj}),
                "__next__",
                new_state,
                summary,
            )
            if next_val.is_bottom():
                summary.add_unknown_effect("may_call", frozenset({obj}))
                result_val = result_val.join(AbsVal.top())
                continue

            dispatch = self._dispatch_call(
                next_val,
                [],
                {},
                callsite_id,
                new_state,
                summary,
            )
            new_state = dispatch.state
            result_val = result_val.join(dispatch.result_val)

        if default_val is None:
            summary.add_may_raise("StopIteration")
        else:
            result_val = result_val.join(default_val)

        if not it_val.addrs:
            result_val = result_val.join(AbsVal.top())

        return new_state, result_val

    def _dispatch_call(
        self,
        callee_val: AbsVal,
        pos_args: List[AbsVal],
        kw_args: Dict[str, AbsVal],
        callsite_id: str,
        state: AIState,
        summary: "SummaryAccumulator",
    ) -> _DispatchResult:
        dispatch = self.call_semantics.dispatch_call(
            callee_val=callee_val,
            pos_arg_vals=pos_args,
            kw_arg_vals=kw_args,
            callsite_id=callsite_id,
            state=state,
            summary=summary,
        )

        result_val = dispatch.result_val
        new_state = self._apply_heap_updates(state, dispatch.heap_updates)

        if dispatch.callee_addrs:
            summary.add_call(callsite_id, frozenset(dispatch.callee_addrs))

        if dispatch.unresolved_addrs or not dispatch.resolved_any:
            if dispatch.unresolved_addrs:
                summary.add_unknown_effect("may_call", frozenset(dispatch.unresolved_addrs))
            elif callee_val.addrs:
                summary.add_unknown_effect("may_call", frozenset(callee_val.addrs))
            result_val = result_val.join(AbsVal.top())

        return _DispatchResult(state=new_state, result_val=result_val)

    @staticmethod
    def _apply_heap_updates(
        state: AIState,
        heap_updates: Iterable[Tuple[AIAddr, AbsObj]],
    ) -> AIState:
        new_state = state
        for ai_addr, ai_obj in heap_updates:
            new_state = new_state.update_obj(ai_addr, ai_obj)
        return new_state

    @staticmethod
    def _get_ai_obj(state: AIState, obj: AbstractObject) -> Optional[AbsObj]:
        ai_addr = AIAddr.from_kcfa_obj(obj)
        return state.get_obj(ai_addr)


class GeneratorSemantics:
    """Semantics for generator objects and lazy iteration."""

    def __init__(
        self,
        pta_query: Optional["PtaQuery"],
        budget: Optional[AnalysisBudget],
        caller_scope: Optional["Scope"],
        call_context: Optional["AbstractContext"],
        recursion_depth: int = 0,
    ) -> None:
        self.pta_query = pta_query
        self.budget = budget or AnalysisBudget()
        self.caller_scope = caller_scope
        self.call_context = call_context
        self.recursion_depth = recursion_depth

    def alloc_generator_obj(
        self,
        callsite_id: str,
        call_context: Optional["AbstractContext"],
        func_val: AbsVal,
        arg_bindings: List[Tuple[str, AbsVal]],
        state: AIState,
    ) -> Tuple[AIState, AbsVal, AIAddr, AbsObj]:
        context = _get_call_context(call_context)
        alloc_site = AllocSite(stmt=callsite_id, kind=AllocKind.OBJECT)
        kcfa_obj = AbstractObject(context=context, alloc_site=alloc_site)
        ai_addr = AIAddr.from_kcfa_obj(kcfa_obj)

        max_parts = getattr(self.budget, "max_field_keys", 20)
        slots = SlotMap.for_namespace(max_partitions=max_parts)
        slots = slots.strong_update_str(GEN_FUNC_KEY, func_val, Presence.PRESENT)

        for param_name, arg_val in arg_bindings:
            if not param_name:
                continue
            slots = slots.strong_update_str(
                f"{GEN_ARG_PREFIX}{param_name}",
                arg_val,
                Presence.PRESENT,
            )

        ai_obj = AbsObj(
            addr=ai_addr,
            kind=ObjKind.ITERATOR,
            slots=slots,
        )
        new_state = state.set_obj(ai_addr, ai_obj)
        gen_val = AbsVal.from_addrs(frozenset({kcfa_obj}))
        return new_state, gen_val, ai_addr, ai_obj

    def is_generator_obj(self, ai_obj: AbsObj) -> bool:
        if ai_obj.kind != ObjKind.ITERATOR:
            return False
        slot = ai_obj.slots.get_str(GEN_FUNC_KEY)
        return slot.pres.may_be_present() and not slot.val.is_bottom()

    def eval_next(
        self,
        gen_val: AbsVal,
        state: AIState,
        summary: "SummaryAccumulator",
        send_val: Optional[AbsVal] = None,
        default_val: Optional[AbsVal] = None,
        allow_cache: bool = True,
    ) -> Tuple[AIState, AbsVal, Tuple[Tuple[AIAddr, AbsObj], ...]]:
        self._record_continuation_residual(summary, gen_val, "generator_resume_continuation_unmodeled")
        send_val = send_val or AbsVal.none_val()
        result_val = AbsVal.bottom()
        new_state = state
        heap_updates: List[Tuple[AIAddr, AbsObj]] = []

        for obj in gen_val.addrs:
            ai_addr = AIAddr.from_kcfa_obj(obj)
            ai_obj = state.get_obj(ai_addr)
            if ai_obj is None or not self.is_generator_obj(ai_obj):
                summary.add_unknown_effect("may_call", frozenset({obj}))
                result_val = result_val.join(AbsVal.top())
                continue

            cached_val = self._read_cached_yield(ai_obj)
            if allow_cache and not cached_val.is_bottom():
                # A cached yielded value carries no continuation or effect
                # summary. Retain it as an observation, then revisit effects.
                result_val = result_val.join(cached_val)

            func_val = ai_obj.slots.get_str(GEN_FUNC_KEY).val
            if func_val.is_bottom() or not func_val.addrs:
                summary.add_unknown_effect("may_call", frozenset({obj}))
                result_val = result_val.join(AbsVal.top())
                continue
            yielded_val = AbsVal.bottom()
            merged_summary = AISummary()
            for func_obj in func_val.addrs:
                partial_yield, gen_summary = self._analyze_generator_body(
                    func_obj,
                    obj,
                    ai_obj,
                    send_val,
                    state,
                )
                yielded_val = yielded_val.join(partial_yield)
                merged_summary = merged_summary.merge(gen_summary)
            self._merge_summary(summary, merged_summary)

            updated_state, update = self._cache_yielded(
                new_state,
                ai_addr,
                ai_obj,
                yielded_val,
            )
            new_state = updated_state
            if update is not None:
                heap_updates.append(update)

            result_val = result_val.join(yielded_val)

        if default_val is None:
            summary.add_may_raise("StopIteration")
        else:
            result_val = result_val.join(default_val)

        if not gen_val.addrs:
            result_val = result_val.join(AbsVal.top())

        return new_state, result_val, tuple(heap_updates)

    def eval_send(
        self,
        gen_val: AbsVal,
        send_val: AbsVal,
        state: AIState,
        summary: "SummaryAccumulator",
    ) -> Tuple[AIState, AbsVal, Tuple[Tuple[AIAddr, AbsObj], ...]]:
        return self.eval_next(
            gen_val=gen_val,
            state=state,
            summary=summary,
            send_val=send_val,
            default_val=None,
            allow_cache=False,
        )

    def eval_throw(
        self,
        gen_val: AbsVal,
        state: AIState,
        summary: "SummaryAccumulator",
    ) -> Tuple[AIState, AbsVal, Tuple[Tuple[AIAddr, AbsObj], ...]]:
        self._record_continuation_residual(summary, gen_val, "generator_throw_continuation_unmodeled")
        summary.add_may_raise("Exception")
        # A caught injected exception can yield again or return a value. There
        # is no basis for declaring the receiver closed without its continuation.
        return state, AbsVal.top(), ()

    def eval_close(
        self,
        gen_val: AbsVal,
        state: AIState,
        summary: "SummaryAccumulator",
    ) -> Tuple[AIState, AbsVal, Tuple[Tuple[AIAddr, AbsObj], ...]]:
        self._record_continuation_residual(summary, gen_val, "generator_close_cleanup_unmodeled")
        # finally code may call/write/raise, and yielding after GeneratorExit
        # raises RuntimeError without proving the generator is closed.
        summary.add_may_raise("Exception")
        return state, AbsVal.none_val(), ()

    @staticmethod
    def _record_continuation_residual(summary, gen_val, reason):
        """Record missing protocol effects; this ledger is not heap havoc."""
        summary.mark_incomplete(reason)
        summary.add_unknown_effect("may_call", frozenset(gen_val.addrs))
        summary.add_unknown_effect("may_write", frozenset(gen_val.addrs))

    def _read_cached_yield(self, ai_obj: AbsObj) -> AbsVal:
        if GEN_YIELDED_KEY in ai_obj.slots.partitions:
            slot = ai_obj.slots.get_str(GEN_YIELDED_KEY)
            return slot.val
        return AbsVal.bottom()

    def _cache_yielded(
        self,
        state: AIState,
        ai_addr: AIAddr,
        ai_obj: AbsObj,
        yielded_val: AbsVal,
    ) -> Tuple[AIState, Optional[Tuple[AIAddr, AbsObj]]]:
        if yielded_val.is_bottom():
            return state, None
        new_slots = ai_obj.slots.weak_update_str(
            GEN_YIELDED_KEY,
            yielded_val,
            Presence.PRESENT,
        )
        new_obj = ai_obj.with_slots(new_slots)
        new_state = state.set_obj(ai_addr, new_obj)
        return new_state, (ai_addr, new_obj)

    def _mark_closed(
        self,
        gen_val: AbsVal,
        state: AIState,
    ) -> Tuple[AIState, Tuple[Tuple[AIAddr, AbsObj], ...]]:
        updates: List[Tuple[AIAddr, AbsObj]] = []
        new_state = state
        closed_val = AbsVal.bool_true()

        for obj in gen_val.addrs:
            ai_addr = AIAddr.from_kcfa_obj(obj)
            ai_obj = state.get_obj(ai_addr)
            if ai_obj is None or not self.is_generator_obj(ai_obj):
                continue
            new_slots = ai_obj.slots.weak_update_str(
                GEN_CLOSED_KEY,
                closed_val,
                Presence.PRESENT,
            )
            new_obj = ai_obj.with_slots(new_slots)
            new_state = new_state.set_obj(ai_addr, new_obj)
            updates.append((ai_addr, new_obj))

        return new_state, tuple(updates)

    def _analyze_generator_body(
        self,
        func_obj: AbstractObject,
        gen_addr_obj: AbstractObject,
        gen_obj: AbsObj,
        send_val: AbsVal,
        state: AIState,
    ) -> Tuple[AbsVal, AISummary]:
        scope_ir = self._get_scope_ir(func_obj)
        if scope_ir is None:
            return AbsVal.top(), AISummary(is_complete=False,
                                          incomplete_reasons=("missing_generator_body",))

        gen_val = AbsVal.from_addrs(frozenset({gen_addr_obj}))
        arg_bindings = self._build_arg_bindings(gen_obj, gen_val, send_val)

        initial_state = AIState.from_arg_bindings(
            arg_bindings,
            self.pta_query,
            self.budget,
        )
        initial_state = initial_state.set_obj(gen_obj.addr, gen_obj)

        from ..solver import AISolver

        call_context = _get_call_context(getattr(func_obj, "context", None))
        solver = AISolver(
            scope_ir=scope_ir,
            initial_state=initial_state,
            pta_query=self.pta_query,
            budget=self.budget,
            caller_scope=self.caller_scope,
            call_context=call_context,
            recursion_depth=self.recursion_depth,
        )
        ai_summary = solver.solve()
        yielded_val = self._collect_yielded_value(solver, gen_obj.addr)
        return yielded_val, ai_summary

    def _build_arg_bindings(
        self,
        gen_obj: AbsObj,
        gen_val: AbsVal,
        send_val: AbsVal,
    ) -> List[ArgBinding]:
        bindings: Dict[str, AbsVal] = {}

        for sig, slot in gen_obj.slots.partitions.items():
            if keysig_kind(sig) != "str":
                continue
            key = sig[1]
            if not key.startswith(GEN_ARG_PREFIX):
                continue
            if slot.pres.may_be_present():
                param_name = key[len(GEN_ARG_PREFIX):]
                existing = bindings.get(param_name)
                bindings[param_name] = slot.val if existing is None else existing.join(slot.val)

        bindings["$__gen"] = gen_val
        bindings["$__send"] = send_val

        return [
            ArgBinding(
                param_name=name,
                points_to=frozenset(val.addrs),
                value=val,
            )
            for name, val in bindings.items()
        ]

    @staticmethod
    def _merge_summary(summary: "SummaryAccumulator", ai_summary: AISummary) -> None:
        for write in ai_summary.writes:
            summary.add_write(write.obj_addrs, write.field_key, write.value_addrs)
        for read_obj, read_field in ai_summary.reads:
            summary.add_read(read_obj, read_field)
        for callsite_id, callee_addrs in ai_summary.calls:
            summary.add_call(callsite_id, callee_addrs)
        for effect in ai_summary.unknown_effects:
            summary.unknown_effects.append(effect)
        summary.may_raise.update(ai_summary.may_raise)
        for reason in ai_summary.incomplete_reasons:
            summary.mark_incomplete(reason)
        if not ai_summary.is_complete and not ai_summary.incomplete_reasons:
            summary.mark_incomplete("nested_generator_summary_incomplete")

    @staticmethod
    def _collect_yielded_value(solver: "AISolver", gen_addr: AIAddr) -> AbsVal:
        collected = AbsVal.bottom()
        states: List[AIState] = []
        states.extend(solver.block_states.values())
        states.extend(solver.block_out_states.values())
        states.extend(solver.edge_out_states.values())

        for st in states:
            ai_obj = st.get_obj(gen_addr)
            if ai_obj is None:
                continue
            slot = ai_obj.slots.get_str(GEN_YIELDED_KEY)
            if slot.pres.may_be_present():
                collected = collected.join(slot.val)
            elif GEN_YIELDED_KEY not in ai_obj.slots.partitions:
                collected = collected.join(ai_obj.slots.default.val)

        return collected

    def _get_scope_ir(self, func_obj: AbstractObject) -> Optional["IRFunc"]:
        from pythonstan.ir.ir_statements import IRFunc
        if hasattr(func_obj, "ir"):
            ir = getattr(func_obj, "ir")
            if isinstance(ir, IRFunc):
                return ir
        if hasattr(func_obj, "alloc_site"):
            alloc_site = getattr(func_obj, "alloc_site")
            if hasattr(alloc_site, "stmt") and isinstance(alloc_site.stmt, IRFunc):
                return alloc_site.stmt
        return None
