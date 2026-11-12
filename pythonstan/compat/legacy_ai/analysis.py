"""Independent AI analysis driver.

Consumes KCFA results to run AI on a configured plan of scopes without
invoking KCFA again.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, override

from pythonstan.analysis.analysis import AnalysisConfig, AnalysisDriver
from pythonstan.analysis.pointer.kcfa.class_hierarchy import ClassHierarchyManager
from pythonstan.analysis.pointer.kcfa.context import NativeContext, Scope
from pythonstan.analysis.pointer.kcfa.context_selector import ContextSelector, parse_policy
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, ConstantObject
from pythonstan.analysis.pointer.kcfa.pta_query_adapter import PtaQueryAdapter
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.variable import VariableKind
from pythonstan.compat.legacy_ai.api import AISummary, AISummaryEngine, ArgBinding
from pythonstan.compat.legacy_ai.config import Config
from pythonstan.compat.legacy_ai.plan import AIPlan, build_plan
from pythonstan.compat.legacy_ai.state import AbsVal
from pythonstan.compat.legacy_ai.upstream import LegacyProvider, QueryState
from pythonstan.ir import IRFunc, IRScope
from pythonstan.world import World

__all__ = ["AIAnalysisDriver", "AIAnalysisResult"]

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pythonstan.world.analysis_manager import AnalysisManager


def _analysis_manager() -> AnalysisManager | None:
    """Observe the existing optional World service without creating one."""
    world = World()
    try:
        return world.analysis_manager
    except AttributeError:
        return None


class AIAnalysisResult:
    summaries: dict[str, AISummary]
    plan: AIPlan
    config: Config
    context_summaries: dict[str, tuple[tuple[NativeContext, AISummary], ...]]

    def __init__(
        self,
        summaries: dict[str, AISummary],
        plan: AIPlan,
        config: Config,
        context_summaries: dict[str, tuple[tuple[NativeContext, AISummary], ...]] | None = None,
    ) -> None:
        self.summaries = summaries
        self.plan = plan
        self.config = config
        self.context_summaries = context_summaries or {}

    def get_summary(self, qualname: str) -> AISummary | None:
        return self.summaries.get(qualname)


class AIAnalysisDriver(AnalysisDriver):
    """AI analysis driver that consumes existing KCFA results."""

    ai_config: Config

    def __init__(self, analysis_config: AnalysisConfig) -> None:
        super().__init__(analysis_config)
        self.ai_config = Config.from_dict(self.config.options or {})
        self._setup_logging()
        self.results = None

    @override
    def analyze(self, scope: IRScope, prev_results: dict[str, object]) -> AIAnalysisResult:
        logger.info("Starting AI analysis driver")
        pta_results = self._get_pta_results(prev_results)
        statistics = pta_results.get_statistics() if hasattr(pta_results, "get_statistics") else {}
        # KCFA publishes an integer 0/1.  Missing or malformed evidence cannot
        # establish that upstream propagation reached its constraint fixpoint.
        converged = statistics.get("converged")
        upstream_incomplete = not (type(converged) in (bool, int) and converged == 1)
        solver_query = self._get_solver_query(pta_results)
        state = solver_query._state if hasattr(solver_query, "_state") else None
        if state is None:
            raise RuntimeError("KCFA solver query does not expose analysis state")

        class_hierarchy, context_selector = self._get_kcfa_helpers()
        pta_query = PtaQueryAdapter(state, class_hierarchy, context_selector)

        budget = self.ai_config.to_budget()
        self._validate_context_policy(context_selector)
        engine = AISummaryEngine(pta_query, budget, solver_config=self.ai_config.to_solver_config())

        plan = build_plan(self.ai_config)
        summaries: dict[str, AISummary] = {}
        if not plan.scopes:
            logger.warning("AI analysis plan is empty")
            self.results = AIAnalysisResult(summaries, plan, self.ai_config)
            return self.results

        entry_context = self._get_entry_context(context_selector)

        context_summaries: dict[str, tuple[tuple[NativeContext, AISummary], ...]] = {}
        for scope_ir in plan.scopes:
            scope_qualname = scope_ir.get_qualname()
            contexts = self._get_scope_contexts(state, scope_ir)
            parts: list[tuple[NativeContext, AISummary]] = []
            # Keep the argument/heap context relationship until the summary
            # boundary. Joining all arguments then querying one context loses
            # effects found only in the other callers.
            callers: Sequence[Scope | None] = contexts or [None]
            for caller_scope in callers:
                selected = [caller_scope] if caller_scope is not None else []
                call_context = caller_scope.context if caller_scope is not None else entry_context
                arg_bindings = self._build_arg_bindings(state, scope_ir, selected)
                if not isinstance(scope_ir, IRFunc) or not arg_bindings:
                    arg_bindings.extend(
                        self._build_var_bindings_from_kcfa(state, scope_ir, selected)
                    )
                part = engine.analyze_callee(
                    scope_ir=scope_ir,
                    call_context=call_context,
                    arg_bindings=arg_bindings,
                    caller_scope=caller_scope,
                )
                if upstream_incomplete:
                    part = part.merge(
                        AISummary(
                            is_complete=False, incomplete_reasons=("upstream_pta_incomplete",)
                        )
                    )
                if not contexts:
                    part = part.merge(
                        AISummary(
                            is_complete=False, incomplete_reasons=("missing_upstream_context",)
                        )
                    )
                parts.append((call_context, part))
            summary = parts[0][1]
            for _, part in parts[1:]:
                summary = summary.merge(part)
            summaries[scope_qualname] = summary
            context_summaries[scope_qualname] = tuple(parts)

        self.results = AIAnalysisResult(summaries, plan, self.ai_config, context_summaries)
        logger.info("AI analysis driver complete")
        return self.results

    def _setup_logging(self) -> None:
        level: object = getattr(logging, self.ai_config.log_level)
        if not isinstance(level, (int, str)):
            raise TypeError("Configured logging level must be an integer or text")
        logging.basicConfig(
            level=level,
            format="%(levelname)s: %(message)s",
        )

    def _get_pta_results(self, prev_results: dict[str, object]) -> LegacyProvider:
        pta_name = self.ai_config.pta_analysis_name
        pta_results = prev_results.get(pta_name)
        if pta_results is not None:
            return LegacyProvider(pta_results)

        analysis_manager = _analysis_manager()
        if analysis_manager is None:
            raise RuntimeError(f"Missing KCFA results for analysis '{pta_name}'")
        try:
            return LegacyProvider(analysis_manager.get_results(pta_name))
        except Exception as exc:
            raise RuntimeError(f"Missing KCFA results for analysis '{pta_name}'") from exc

    def _get_solver_query(self, pta_results: LegacyProvider | QueryState) -> QueryState:
        if isinstance(pta_results, LegacyProvider):
            return pta_results.query()
        return pta_results

    def _get_kcfa_helpers(self) -> tuple[ClassHierarchyManager | None, ContextSelector | None]:
        analysis_manager = _analysis_manager()
        if analysis_manager is None:
            return None, None
        try:
            analyzer = analysis_manager.get_analyzer(self.ai_config.pta_analysis_name)
        except Exception:
            return None, None
        class_hierarchy: object = getattr(analyzer, "class_hierarchy", None)
        context_selector: object = getattr(analyzer, "context_selector", None)
        if class_hierarchy is not None and not isinstance(class_hierarchy, ClassHierarchyManager):
            raise TypeError("KCFA class hierarchy must implement the Native hierarchy contract")
        if context_selector is not None and not isinstance(context_selector, ContextSelector):
            raise TypeError("KCFA context selector must implement the Native selection contract")
        return class_hierarchy, context_selector

    def _validate_context_policy(self, context_selector: ContextSelector | None) -> None:
        requested = self.ai_config.context_policy
        if requested == "inherit":
            return
        expected = parse_policy(requested)
        if context_selector is not None and context_selector.policy != expected:
            raise ValueError(
                "AI context_policy must match its KCFA predecessor; use inherit or rerun KCFA"
            )

    def _get_entry_context(self, context_selector: ContextSelector | None) -> NativeContext:
        if context_selector is not None:
            return context_selector.empty_context()
        policy = parse_policy(
            "2-cfa" if self.ai_config.context_policy == "inherit" else self.ai_config.context_policy
        )
        return ContextSelector(policy=policy).empty_context()

    def _get_scope_contexts(self, state: PointerAnalysisState, scope_ir: IRScope) -> list[Scope]:
        if hasattr(state, "get_scopes_for_ir"):
            return state.get_scopes_for_ir(scope_ir)
        contexts: list[Scope] = []
        internal: Mapping[AbstractObject, Scope | None]
        try:
            internal = state._internal_scope
        except AttributeError:
            internal = {}
        for _, ctx_scope in internal.items():
            if ctx_scope is None:
                continue
            owner: object = getattr(ctx_scope, "stmt", None)
            if owner is scope_ir:
                contexts.append(ctx_scope)
        return contexts

    def _select_caller_scope(self, contexts: list[Scope]) -> Scope | None:
        if not contexts:
            return None
        for ctx_scope in contexts:
            try:
                if ctx_scope.context.is_empty():
                    return ctx_scope
            except Exception:
                continue
        return contexts[0]

    def _get_param_names(self, scope_ir: IRFunc) -> list[str]:
        args = scope_ir.args
        names: list[str] = []
        try:
            positional_only = args.posonlyargs
        except AttributeError:
            positional_only = []
        names.extend([a.arg for a in positional_only])
        try:
            positional = args.args
        except AttributeError:
            positional = []
        names.extend([a.arg for a in positional])
        try:
            vararg = args.vararg
        except AttributeError:
            vararg = None
        if vararg:
            selected_vararg = args.vararg
            if selected_vararg is None:
                raise AttributeError("'NoneType' object has no attribute 'arg'")
            names.append(selected_vararg.arg)
        try:
            keyword_only = args.kwonlyargs
        except AttributeError:
            keyword_only = []
        names.extend([a.arg for a in keyword_only])
        try:
            kwarg = args.kwarg
        except AttributeError:
            kwarg = None
        if kwarg:
            selected_kwarg = args.kwarg
            if selected_kwarg is None:
                raise AttributeError("'NoneType' object has no attribute 'arg'")
            names.append(selected_kwarg.arg)
        return names

    def _build_arg_bindings(
        self, state: PointerAnalysisState, scope_ir: IRScope, contexts: list[Scope]
    ) -> list[ArgBinding]:
        if not isinstance(scope_ir, IRFunc):
            return []

        param_names = self._get_param_names(scope_ir)
        if not param_names:
            return []

        bindings: list[ArgBinding] = []
        for name in param_names:
            pts_union: set[AbstractObject] = set()
            const_strs = set()
            for ctx_scope in contexts:
                ctx_var = state._get_variable_direct(
                    ctx_scope, ctx_scope.context, name, VariableKind.LOCAL
                )
                if ctx_var is None:
                    continue
                pts = state.get_points_to(ctx_var)
                for obj in pts:
                    if isinstance(obj, ConstantObject) and isinstance(obj.value, str):
                        const_strs.add(obj.value)
                pts_union.update(pts)

            abs_val = AbsVal.from_addrs(frozenset(pts_union))
            if pts_union and all(
                isinstance(obj, ConstantObject) and type(obj.value) is str for obj in pts_union
            ):
                abs_val = AbsVal.from_strs(frozenset(const_strs))

            bindings.append(
                ArgBinding(
                    param_name=name,
                    points_to=frozenset(pts_union),
                    value=abs_val,
                )
            )

        return bindings

    def _build_var_bindings_from_kcfa(
        self, state: PointerAnalysisState, scope_ir: IRScope, contexts: list[Scope]
    ) -> list[ArgBinding]:
        """Build variable bindings from KCFA points-to for non-parameter variables.

        This seeds the AI initial state with KCFA results for module/class-level variables.
        """
        from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
        from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind

        bindings: list[ArgBinding] = []

        # If we have contexts, find the primary one (empty context preferred)
        ctx_scope = None
        if contexts:
            for c in contexts:
                if hasattr(c, "context") and c.context.is_empty():
                    ctx_scope = c
                    break
            if ctx_scope is None and contexts:
                ctx_scope = contexts[0]

        if ctx_scope is None:
            return bindings

        var_names_seen = set()
        # Iterate through all context-variable pairs in KCFA state
        for node in state._env:
            # The environment is indexed by flow-graph nodes, not bare Ctxs.
            # Field/selector nodes must not become lexical variable bindings.
            if not isinstance(node, NormalNode):
                continue
            ctx_var = node.var
            if not hasattr(ctx_var, "content") or not hasattr(ctx_var, "context"):
                continue

            # Check if this variable belongs to our scope
            content = ctx_var.content
            if not isinstance(content, Variable):
                continue

            # Match scope
            var_scope: object = getattr(ctx_var, "scope", None)
            if var_scope != ctx_scope:
                continue

            var_name = content.name

            # Skip temporaries, duplicates, and parameters (already handled)
            if var_name.startswith("$") or var_name in var_names_seen:
                continue
            if content.kind == VariableKind.PARAMETER:
                continue

            var_names_seen.add(var_name)

            # Get points-to set
            pts = state.get_points_to(ctx_var)
            if not pts:
                continue

            const_strs = set()
            for obj in pts:
                if isinstance(obj, ConstantObject) and isinstance(obj.value, str):
                    const_strs.add(obj.value)

            abs_val = AbsVal.from_addrs(frozenset(pts))
            if pts and all(
                isinstance(obj, ConstantObject) and type(obj.value) is str for obj in pts
            ):
                abs_val = AbsVal.from_strs(frozenset(const_strs))

            bindings.append(
                ArgBinding(
                    param_name=var_name,
                    points_to=frozenset(pts),
                    value=abs_val,
                )
            )

        return bindings
