"""Independent AI analysis driver.

Consumes KCFA results to run AI on a configured plan of scopes without
invoking KCFA again.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from pythonstan.analysis import AnalysisDriver, AnalysisConfig
from pythonstan.ir import IRFunc, IRScope
from pythonstan.world import World

from pythonstan.analysis.pointer.kcfa.object import ConstantObject
from pythonstan.analysis.pointer.kcfa.pta_query_adapter import PtaQueryAdapter
from pythonstan.analysis.pointer.kcfa.variable import VariableKind
from pythonstan.analysis.pointer.kcfa.context_selector import ContextSelector, parse_policy

from .api import AISummary, AISummaryEngine, AnalysisBudget, ArgBinding
from .state import AbsVal
from .config import Config
from .plan import AIPlan, build_plan

__all__ = ["AIAnalysisDriver", "AIAnalysisResult"]

logger = logging.getLogger(__name__)


class AIAnalysisResult:
    def __init__(self, summaries: Dict[str, AISummary], plan: AIPlan, config: Config):
        self.summaries = summaries
        self.plan = plan
        self.config = config

    def get_summary(self, qualname: str) -> Optional[AISummary]:
        return self.summaries.get(qualname)


class AIAnalysisDriver(AnalysisDriver):
    """AI analysis driver that consumes existing KCFA results."""

    def __init__(self, analysis_config: AnalysisConfig):
        super().__init__(analysis_config)
        self.ai_config = Config.from_dict(self.config.options or {})
        self._setup_logging()
        self.results: Optional[AIAnalysisResult] = None

    def analyze(self, scope: IRScope, prev_results: Dict[str, Any]) -> AIAnalysisResult:
        logger.info("Starting AI analysis driver")
        pta_results = self._get_pta_results(prev_results)
        solver_query = self._get_solver_query(pta_results)
        state = getattr(solver_query, "_state", None)
        if state is None:
            raise RuntimeError("KCFA solver query does not expose analysis state")

        class_hierarchy, context_selector = self._get_kcfa_helpers()
        pta_query = PtaQueryAdapter(state, class_hierarchy, context_selector)

        budget = AnalysisBudget(
            max_iterations=self.ai_config.max_iterations,
            max_stmt_visits=self.ai_config.max_stmt_visits,
            max_recursion_depth=self.ai_config.max_recursion_depth,
            max_addr_set_size=self.ai_config.max_addr_set_size,
            max_field_keys=self.ai_config.max_field_keys,
            max_const_strings=self.ai_config.max_const_strings,
            max_ho_tokens=self.ai_config.max_ho_tokens,
            enable_narrowing=self.ai_config.enable_narrowing,
            narrowing_iterations=self.ai_config.narrowing_iterations,
            enable_value_partition=self.ai_config.enable_value_partition,
            max_value_partitions=self.ai_config.max_value_partitions,
            value_partition_str_cap=self.ai_config.value_partition_str_cap,
        )
        engine = AISummaryEngine(pta_query, budget)

        plan = build_plan(self.ai_config)
        summaries: Dict[str, AISummary] = {}
        if not plan.scopes:
            logger.warning("AI analysis plan is empty")
            self.results = AIAnalysisResult(summaries, plan, self.ai_config)
            return self.results

        entry_context = self._get_entry_context(context_selector)

        for scope_ir in plan.scopes:
            scope_qualname = scope_ir.get_qualname()
            contexts = self._get_scope_contexts(state, scope_ir)
            caller_scope = self._select_caller_scope(contexts)
            call_context = caller_scope.context if caller_scope is not None else entry_context
            
            # Build arg bindings from parameters (for functions)
            arg_bindings = self._build_arg_bindings(state, scope_ir, contexts)
            
            # For modules/classes, also seed from KCFA variables in empty context
            if not isinstance(scope_ir, IRFunc) or len(arg_bindings) == 0:
                var_bindings = self._build_var_bindings_from_kcfa(state, scope_ir, contexts)
                arg_bindings.extend(var_bindings)

            logger.info(
                "AI analyze scope=%s contexts=%d args=%d",
                scope_qualname,
                len(contexts),
                len(arg_bindings),
            )

            summary = engine.analyze_callee(
                scope_ir=scope_ir,
                call_context=call_context,
                arg_bindings=arg_bindings,
                caller_scope=caller_scope,
            )
            summaries[scope_qualname] = summary

        self.results = AIAnalysisResult(summaries, plan, self.ai_config)
        logger.info("AI analysis driver complete")
        return self.results

    def _setup_logging(self) -> None:
        logging.basicConfig(
            level=getattr(logging, self.ai_config.log_level),
            format="%(levelname)s: %(message)s",
        )

    def _get_pta_results(self, prev_results: Dict[str, Any]) -> Any:
        pta_name = self.ai_config.pta_analysis_name
        pta_results = prev_results.get(pta_name)
        if pta_results is not None:
            return pta_results

        world = World()
        analysis_manager = getattr(world, "analysis_manager", None)
        if analysis_manager is None:
            raise RuntimeError(f"Missing KCFA results for analysis '{pta_name}'")
        try:
            return analysis_manager.get_results(pta_name)
        except Exception as exc:
            raise RuntimeError(f"Missing KCFA results for analysis '{pta_name}'") from exc

    def _get_solver_query(self, pta_results: Any) -> Any:
        if hasattr(pta_results, "query"):
            return pta_results.query()
        return pta_results

    def _get_kcfa_helpers(self) -> Tuple[Optional[Any], Optional[Any]]:
        world = World()
        analysis_manager = getattr(world, "analysis_manager", None)
        if analysis_manager is None:
            return None, None
        try:
            analyzer = analysis_manager.get_analyzer(self.ai_config.pta_analysis_name)
        except Exception:
            return None, None
        class_hierarchy = getattr(analyzer, "class_hierarchy", None)
        context_selector = getattr(analyzer, "context_selector", None)
        return class_hierarchy, context_selector

    def _get_entry_context(self, context_selector: Optional[Any]):
        if context_selector is not None:
            return context_selector.empty_context()
        policy = parse_policy(self.ai_config.context_policy)
        return ContextSelector(policy=policy).empty_context()

    def _get_scope_contexts(self, state: Any, scope_ir: IRScope) -> List[Any]:
        contexts: List[Any] = []
        internal = getattr(state, "_internal_scope", {})
        for _, ctx_scope in internal.items():
            if ctx_scope is None:
                continue
            if getattr(ctx_scope, "stmt", None) is scope_ir:
                contexts.append(ctx_scope)
        return contexts

    def _select_caller_scope(self, contexts: List[Any]) -> Optional[Any]:
        if not contexts:
            return None
        for ctx_scope in contexts:
            try:
                if ctx_scope.context.is_empty():
                    return ctx_scope
            except Exception:
                continue
        return contexts[0]

    def _get_param_names(self, scope_ir: IRFunc) -> List[str]:
        args = scope_ir.args
        names: List[str] = []
        names.extend([a.arg for a in getattr(args, "posonlyargs", [])])
        names.extend([a.arg for a in getattr(args, "args", [])])
        if getattr(args, "vararg", None):
            names.append(args.vararg.arg)
        names.extend([a.arg for a in getattr(args, "kwonlyargs", [])])
        if getattr(args, "kwarg", None):
            names.append(args.kwarg.arg)
        return names

    def _build_arg_bindings(self, state: Any, scope_ir: IRScope, contexts: List[Any]) -> List[ArgBinding]:
        if not isinstance(scope_ir, IRFunc):
            return []

        param_names = self._get_param_names(scope_ir)
        if not param_names:
            return []

        bindings: List[ArgBinding] = []
        for name in param_names:
            pts_union = set()
            const_strs = set()
            for ctx_scope in contexts:
                ctx_var = state._get_variable_direct(ctx_scope, ctx_scope.context, name, VariableKind.LOCAL)
                if ctx_var is None:
                    continue
                pts = state.get_points_to(ctx_var)
                for obj in pts:
                    if isinstance(obj, ConstantObject) and isinstance(obj.value, str):
                        const_strs.add(obj.value)
                pts_union.update(pts)

            abs_val = AbsVal.from_addrs(frozenset(pts_union))
            if const_strs:
                abs_val = AbsVal.from_strs(frozenset(const_strs))
                if pts_union and len(const_strs) < len(pts_union):
                    abs_val = abs_val.with_addrs(frozenset(pts_union))

            bindings.append(ArgBinding(
                param_name=name,
                points_to=frozenset(pts_union),
                value=abs_val,
            ))

        return bindings

    def _build_var_bindings_from_kcfa(self, state: Any, scope_ir: IRScope, contexts: List[Any]) -> List[ArgBinding]:
        """Build variable bindings from KCFA points-to for non-parameter variables.
        
        This seeds the AI initial state with KCFA results for module/class-level variables.
        """
        from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind
        from pythonstan.analysis.pointer.kcfa.object import ConstantObject
        
        bindings: List[ArgBinding] = []
        
        # If we have contexts, find the primary one (empty context preferred)
        ctx_scope = None
        if contexts:
            for c in contexts:
                if hasattr(c, 'context') and c.context.is_empty():
                    ctx_scope = c
                    break
            if ctx_scope is None and contexts:
                ctx_scope = contexts[0]
        
        if ctx_scope is None:
            return bindings
        
        var_names_seen = set()
        # Iterate through all context-variable pairs in KCFA state
        for ctx_var in state._env.keys():
            if not hasattr(ctx_var, 'content') or not hasattr(ctx_var, 'context'):
                continue
            
            # Check if this variable belongs to our scope
            content = ctx_var.content
            if not isinstance(content, Variable):
                continue
            
            # Match scope
            var_scope = getattr(content, 'scope', None)
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
            if const_strs:
                abs_val = AbsVal.from_strs(frozenset(const_strs))
                if pts and len(const_strs) < len(pts):
                    abs_val = abs_val.with_addrs(frozenset(pts))
            
            bindings.append(ArgBinding(
                param_name=var_name,
                points_to=frozenset(pts),
                value=abs_val,
            ))
        
        return bindings
