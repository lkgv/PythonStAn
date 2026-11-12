"""Closed-module analysis through the production pointer-analysis driver.

The returned ModuleSummary is an in-process binding projection, not a certified
replacement for importing a module. Its points-to sets belong to the current
analysis arena and must be consumed before another pointer-analysis run resets
that arena. Use the whole-program Pipeline when imports must be followed.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, override

from pythonstan.analysis.analysis import AnalysisConfig
from pythonstan.ir.ir_statements import IRImport, IRModule, IRScope
from pythonstan.world.world import World

from .config import Config
from .module_summary import ModuleSummary

if TYPE_CHECKING:
    from .analysis import PointerAnalysis

__all__ = ["ModuleAnalyzer"]


class ModuleAnalyzer:
    """Analyze one lowered, closed entry module with the real solver/processors.

    Frontend lowering and scope registration are owned by World/Pipeline. This
    adapter requires their actual IR; an empty lowered body is valid, while a
    missing body is an error. Import summaries cannot yet replace source modules
    because the summary schema omits heap, closure, callback and exception effects.
    """

    config: Config
    last_analysis: PointerAnalysis | None

    def __init__(self, config: Config) -> None:
        if not isinstance(config, Config):
            raise TypeError("ModuleAnalyzer requires a KCFA Config")
        self.config = config
        self.last_analysis = None

    def analyze_module(
        self,
        module_ir: IRModule,
        import_summaries: Mapping[str, ModuleSummary] | None = None,
    ) -> ModuleSummary:
        """Analyze an already lowered closed entry module and project exports.

        Raises ValueError before running when the supplied import summaries are
        incomplete, imports need source resolution, the IR has not been lowered,
        or World contains another module. The existing summary schema is always
        incomplete; no successful result claims whole-program soundness.
        """
        if not isinstance(module_ir, IRModule):
            raise TypeError("analyze_module requires an IRModule")
        if import_summaries:
            details = []
            for name, summary in sorted(import_summaries.items()):
                if not isinstance(summary, ModuleSummary):
                    raise TypeError(f"Import summary {name!r} is not a ModuleSummary")
                import_reasons = (
                    ", ".join(sorted(summary.incomplete_reasons))
                    or "summary schema has no complete module-effect contract"
                )
                details.append(f"{name}: {import_reasons}")
            raise ValueError(
                "Incomplete import summaries cannot replace source modules ("
                + "; ".join(details)
                + "). Use the whole-program Pipeline."
            )
        world = World()
        try:
            manager = world.scope_manager
            _ = manager.scope_ir
        except AttributeError as exc:
            raise ValueError(
                "Module analysis requires an initialized World with lowered IR"
            ) from exc
        if world.get_entry_module() is not module_ir:
            raise ValueError("Module analysis requires the current World entry-module identity")
        statements = manager.get_statement_ir(module_ir)
        if statements is None:
            raise ValueError("Module IR has not been lowered; run the production frontend first")
        tree = module_ir.get_ast()
        if not isinstance(tree, ast.Module):
            raise ValueError("IRModule must retain its module AST for export/import validation")
        if any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree)):
            raise ValueError(
                "Closed-module analysis does not resolve imports; use the whole-program Pipeline"
            )
        modules: set[IRModule] = set()
        try:
            graph = manager.module_graph
        except AttributeError:
            pass
        else:
            if graph is not None:
                modules.update(graph.get_modules())
        modules.update(scope for scope in manager.scopes if isinstance(scope, IRModule))
        if modules - {module_ir}:
            raise ValueError(
                "Closed-module analysis requires a World containing only the requested module"
            )
        for scope in manager.scopes | {module_ir}:
            current: IRScope = scope
            seen: set[IRScope] = set()
            while current is not module_ir and current not in seen:
                seen.add(current)
                parent = manager.father.get(current)
                if parent is None:
                    raise ValueError("World contains scopes outside the requested closed module")
                current = parent
            if current is not module_ir:
                raise ValueError("Scope ancestry is cyclic")
            if any(isinstance(stmt, IRImport) for stmt in (manager.get_statement_ir(scope) or ())):
                raise ValueError("Lowered IR contains imports; use the whole-program Pipeline")

        # This constructs the production translator, processor chain, builtins,
        # query interface and fixed-point solver rather than duplicating them.
        from .analysis import PointerAnalysis

        driver = PointerAnalysis(
            AnalysisConfig(
                name=f"module:{module_ir.get_qualname()}",
                id="PointerAnalysis",
                description="Closed-module binding projection",
                prev_analysis=["ir"],
                options=self.config.to_dict(),
            )
        )
        self.last_analysis = driver
        result = driver.analyze(module_ir, {})
        native_scope = driver.state.get_scope_for_ir(module_ir)
        if native_scope is None:
            raise RuntimeError("Production pointer analysis did not register its entry module")
        names, dynamic_exports = self._get_exported_names(tree)
        summary = ModuleSummary.from_state(driver.state, native_scope, names)
        reasons = set(summary.incomplete_reasons)
        statistics = result.query().get_statistics()
        if not statistics.get("converged", False):
            reasons.add("module_solver_did_not_converge")
        if driver.state.peek_residual_effects():
            reasons.add("module_has_unresolved_effect_obligations")
        if dynamic_exports:
            reasons.add("dynamic___all___not_resolved")
        return replace(summary, incomplete_reasons=frozenset(reasons))

    @staticmethod
    def _get_exported_names(tree: ast.Module) -> tuple[set[str], bool]:
        """Collect lexical module bindings without descending into inner scopes.

        A literal __all__ determines an explicit export list. For dynamic __all__,
        retain all lexical bindings, including private names, and report the
        incomplete export filter instead of claiming it was resolved.
        """

        class Bindings(ast.NodeVisitor):
            def __init__(self) -> None:
                self.names: set[str] = set()

            @override
            def visit_Name(self, node: ast.Name) -> None:
                if isinstance(node.ctx, ast.Store):
                    self.names.add(node.id)

            @override
            def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
                self.names.add(node.name)
                for expression in (
                    *node.decorator_list,
                    *node.args.defaults,
                    *(value for value in node.args.kw_defaults if value is not None),
                ):
                    self.visit(expression)

            visit_AsyncFunctionDef = visit_FunctionDef

            @override
            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self.names.add(node.name)
                for expression in (
                    *node.decorator_list,
                    *node.bases,
                    *(keyword.value for keyword in node.keywords),
                ):
                    self.visit(expression)

            @override
            def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
                if node.name:
                    self.names.add(node.name)
                self.generic_visit(node)

        bindings = Bindings()
        bindings.visit(tree)
        if "__all__" not in bindings.names:
            return {name for name in bindings.names if not name.startswith("_")}, False
        writes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
            and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            )
        ]
        observed_or_deleted = any(
            isinstance(node, ast.Name)
            and node.id == "__all__"
            and not isinstance(node.ctx, ast.Store)
            for node in ast.walk(tree)
        )
        if len(writes) == 1 and writes[0] in tree.body and not observed_or_deleted:
            value = writes[0].value
            if isinstance(value, (ast.List, ast.Tuple)):
                explicit: set[str] = set()
                for item in value.elts:
                    if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                        break
                    explicit.add(item.value)
                else:
                    return explicit, False
        return {name for name in bindings.names if not name.startswith("$")}, True
