"""Main k-CFA pointer analysis driver.

This module provides the main entry point for running pointer analysis.
"""

from __future__ import annotations

import ast
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, override

from pythonstan.analysis.analysis import AnalysisConfig, AnalysisDriver
from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    require_array,
    require_string,
    validate_json,
)
from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite
from pythonstan.analysis.pointer.kcfa.points_to_set import reset_object_table
from pythonstan.ir import IRScope
from pythonstan.ir.ir_statements import IRCall

from .allocation import canonical_constant
from .processor import (
    AttributeSemanticsProcessor,
    ClassProtocolProcessor,
    ComposeProcessor,
    ContainerProcessor,
    GeneratorProcessor,
    NormalCallProcessor,
    SuperResolveProcessor,
)
from .unknown_tracker import UnknownKind

if TYPE_CHECKING:
    from pythonstan.ir import IRFunc, IRModule, IRScope
    from pythonstan.world.scope_manager import ModuleGraph

    from .constraints import Constraint
    from .context import NativeContext, Scope
    from .points_to_set import PointsToSet
    from .solver_interface import ISolverQuery
    from .variable import Variable

__all__ = ["PointerAnalysis", "AnalysisResult"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _BuiltinSeedTemplates:
    """Run-local immutable requests, never merged module values or boxes."""

    context: NativeContext
    object_table_generation: int
    seeds: tuple[tuple[Variable, PointsToSet], ...]


def _source_scope_key(scope: IRScope) -> tuple[str, str, int, int, str]:
    """Order source seeds without using an allocation address as a tie-breaker.

    This orders initialization only. PFG edge sets, dynamic constraints and
    abstract-object iteration have their own schedules; this is not a claim
    that the whole analysis is deterministic.
    """
    node = scope.get_ast()
    filename: object = getattr(scope, "filename", "") or ""
    line: object = getattr(node, "lineno", -1)
    column: object = getattr(node, "col_offset", -1)
    if not isinstance(filename, str) or not isinstance(line, int) or not isinstance(column, int):
        raise TypeError("Source scope position must contain text and integer coordinates")
    return (
        scope.get_qualname(),
        filename,
        line,
        column,
        type(scope).__name__,
    )


def _module_runtime_metadata(module: IRScope, field: str, default: str) -> str | None:
    """Read the two external project metadata fields as actual scalar values."""
    value: object = getattr(module, field, default)
    if value is not None and not isinstance(value, str):
        raise TypeError("Module runtime metadata must be text or None")
    return value


class PointerAnalysis(AnalysisDriver):
    """Main entry point for k-CFA pointer analysis."""

    def __init__(self, analysis_config: AnalysisConfig) -> None:
        """Initialize pointer analysis.

        Args:
            config: Analysis configuration. If None, uses default.
        """
        from pythonstan.world.world import World

        from .builtin_api_handler import BuiltinSummaryManager
        from .class_hierarchy import ClassHierarchyManager
        from .config import Config
        from .context_selector import ContextSelector, parse_policy
        from .ir_translator import IRTranslator
        from .solver import PointerSolver
        from .state import PointerAnalysisState

        self.config = analysis_config
        if not hasattr(self.config, "options"):
            print(f"Analysis config {self.config} has no options")
        self.kcfa_config = Config.from_dict(self.config.options)
        self._captured_method_targets: frozenset[tuple[str, str]] | None = None
        self._setup_logging()
        self.results: object = None
        self.world = World()

        # Initialize debug monitor if enabled
        self.debug_monitor: object | None = None
        if self.kcfa_config.enable_debug_monitor:
            from .debug_boundary import load_optional_monitor

            self.debug_monitor = load_optional_monitor(
                output_dir=self.kcfa_config.debug_output_dir,
                log_interval=self.kcfa_config.debug_log_interval,
                track_events=True,
                track_object_flow=self.kcfa_config.track_object_flow,
                track_pfg=self.kcfa_config.track_pfg_activation,
                enabled=True,
            )
            logger.info(f"Debug monitoring enabled, output to: {self.kcfa_config.debug_output_dir}")

        self.state = PointerAnalysisState(debug_monitor=self.debug_monitor)
        # Capture this arena's manager now, before a later Pipeline can replace
        # World. The manager is populated by preprocessing after construction.
        self.state._scope_manager = self.world.scope_manager

        # Initialize PFG with debug monitor
        from .pointer_flow_graph import PointerFlowGraph

        self.state._pointer_flow_graph = PointerFlowGraph(debug_monitor=self.debug_monitor)

        policy = parse_policy(self.kcfa_config.context_policy)
        self.context_selector = ContextSelector(
            policy=policy,
            heap_context_policy=self.kcfa_config.heap_context_policy,
            heap_context_depth=self.kcfa_config.heap_context_depth,
            method_context_policies=self.kcfa_config.method_context_policies,
        )
        self.translator = IRTranslator(self.kcfa_config)
        self.class_hierarchy = ClassHierarchyManager()
        self.builtin_manager = BuiltinSummaryManager(self.kcfa_config)

        self.solver = PointerSolver(
            state=self.state,
            config=self.kcfa_config,
            ir_translator=self.translator,
            context_selector=self.context_selector,
            class_hierarchy=self.class_hierarchy,
            builtin_manager=self.builtin_manager,
            debug_monitor=self.debug_monitor,
            processor=ComposeProcessor(
                [
                    GeneratorProcessor(),
                    AttributeSemanticsProcessor(),
                    ClassProtocolProcessor(),
                    NormalCallProcessor(),
                    ContainerProcessor(index_sensitive=self.kcfa_config.index_sensitive),
                    SuperResolveProcessor(),
                ]
            ),
        )

    @override
    def analyze(self, entry_scope: IRScope, prev_results: dict[str, object]) -> AnalysisResult:
        """Run pointer analysis on module.

        Args:
            entry_scope: Scope to analyze
            prev_results: Results of previous analyses

        Returns:
            AnalysisResult containing points-to information and call graph
        """
        self._validate_method_policy_targets()
        # Capture the actual entry identity for passive queries after World reset.
        from pythonstan.ir import IRModule

        self.state.entry_module = (
            entry_scope if isinstance(entry_scope, IRModule) else self.world.get_entry_module()
        )
        # Reset object ID table for clean state each analysis run
        reset_object_table()

        logger.info("Starting pointer analysis")

        # Get empty context for module-level analysis
        empty_context = self.context_selector.empty_context()

        from pythonstan.ir import IRModule

        from .context import Scope
        from .object import ModuleObject

        options = self.config.options
        test_root = options.get("test_root") or options.get("test_dir")
        if test_root is not None and not isinstance(test_root, str):
            raise ValueError("test_root/test_dir must be text or None")
        raw_test_files = options.get("test_files")
        test_files = (
            None
            if raw_test_files is None
            else [require_string(item) for item in require_array(validate_json(raw_test_files))]
        )

        test_funcs = self._collect_test_functions(test_root, test_files)
        entry_names = self.kcfa_config.entry_points
        if test_funcs or entry_names is not None:
            selected_names = frozenset(entry_names or ())

            def selected_entrance(ir_func: IRFunc) -> bool:
                return (
                    ir_func in test_funcs
                    or ir_func.get_qualname() in selected_names
                    or ir_func.name in selected_names
                )

            self.solver.entrance_predicate = selected_entrance
            logger.info(
                "Using configured entry points: %s and %d selected tests",
                sorted(selected_names),
                len(test_funcs),
            )

        # Translate ALL scopes to constraints (not just entry module)
        logger.info("Translating all scopes to constraints...")

        scoped_constraints: list[tuple[Scope, Constraint]] = []
        module_scopes = []
        if self.solver._diagnostics is not None:
            self.solver._diagnostics.phase = "module_translation_started"
            self.solver._diagnostics.last_action = "translate_module_scopes"

        module_graph: ModuleGraph | None
        try:
            module_graph = self.world.scope_manager.module_graph
        except AttributeError:
            module_graph = None
        entry_module = (
            entry_scope if isinstance(entry_scope, IRModule) else self.world.get_entry_module()
        )
        modules = set(module_graph.get_modules()) if module_graph is not None else set[IRModule]()
        if isinstance(entry_module, IRModule):
            modules.add(entry_module)
        entry_module_scope: Scope | None = None
        fallback_scope: Scope | None = None

        for module_ir in sorted(
            (module for module in modules if isinstance(module, IRModule)), key=_source_scope_key
        ):
            alloc_site = AllocSite.from_ir_node(module_ir, AllocKind.MODULE)
            module_context = self.state.select_allocation_context(
                empty_context, alloc_site, identity="canonical"
            )
            module_obj = ModuleObject(module_context, alloc_site, module_ir)
            module_scope = Scope.new(
                obj=module_obj, module=None, context=empty_context, stmt=module_ir, parent=None
            )
            self.state.set_internal_scope(module_obj, module_scope)
            self._initialize_module_metadata(module_scope, empty_context)
            module_scopes.append(module_scope)
            if fallback_scope is None:
                fallback_scope = module_scope
            if module_ir == entry_module:
                entry_module_scope = module_scope

            try:
                scope_name = module_ir.get_qualname()
                logger.debug(f"Translating module: {scope_name}")
                c = self.translator.translate_module(module_ir)
                scoped_constraints.extend((module_scope, constraint) for constraint in c)
            except Exception as e:
                # A diagnostic switch must not erase the failed translation's
                # open semantic obligations from the returned result.
                self.state._residual_effects[(module_scope, "module_translation")] = {
                    "origin": module_ir.get_qualname(),
                    "reason": f"module translation failed: {type(e).__name__}: {e}",
                    "context": str(module_scope.context),
                    "effects": [
                        "untranslated_source",
                        "may_mutate_heap",
                        "may_invoke_callbacks",
                        "may_raise",
                    ],
                }
                self.solver._unknown_tracker.record(
                    UnknownKind.TRANSLATION_ERROR,
                    module_ir.get_qualname(),
                    f"Error translating module body: {e}",
                    context=str(module_scope),
                )
                logger.warning(f"Error translating scope {module_ir.get_qualname()}: {e}")

        logger.info(f"Total constraints generated: {len(scoped_constraints)}")
        if self.solver._diagnostics is not None:
            self.solver._diagnostics.phase = "module_translation_completed"
            self.solver._diagnostics.last_action = "register_constraints"

        # Add all constraints to solver
        target_scope = entry_module_scope or fallback_scope
        if target_scope is None:
            raise RuntimeError("No module scope available for constraint solving")
        for module_scope, constraint in scoped_constraints:
            self.solver.add_constraint(module_scope, empty_context, constraint)

        # Initialize builtin functions (iter, next, len, etc.)
        logger.info("Initializing builtin functions...")
        if entry_module_scope is None:
            entry_module_scope = fallback_scope
        builtin_seed_templates = None
        if self.solver._diagnostics is not None:
            self.solver._diagnostics.phase = "builtin_bootstrap_started"
            self.solver._diagnostics.last_action = "initialize_builtin_seeds"
        for module_scope in module_scopes:
            builtin_seed_templates = self._initialize_builtins(
                module_scope, empty_context, builtin_seed_templates
            )
        if self.solver._diagnostics is not None:
            self.solver._diagnostics.phase = "builtin_bootstrap_completed"
            self.solver._diagnostics.last_action = "prepare_solve"

        # Create synthetic method contexts to enable method-to-method call resolution
        logger.info("Creating synthetic method contexts...")
        if self.kcfa_config.enable_synthetic_method_contexts:
            self._create_synthetic_method_contexts(target_scope, empty_context)

        # Solve to fixpoint
        self.solver.solve_to_fixpoint()

        # Export debug data if enabled
        if self.debug_monitor and self.kcfa_config.export_debug_data:
            logger.info("Exporting debug data...")
            import datetime

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            project_name = (
                self.kcfa_config.project_path.split("/")[-1]
                if self.kcfa_config.project_path
                else "unknown"
            )
            policy_name = self.kcfa_config.context_policy.replace("-", "_")

            from .debug_boundary import export_debug_data

            export_debug_data(
                self.debug_monitor,
                self.state,
                self.state.pointer_flow_graph,
                f"{project_name}_{policy_name}_{timestamp}_debug.json",
                f"{project_name}_{policy_name}_{timestamp}_summary.md",
            )

            logger.info("Debug data exported")

        # Create and return result
        solver_query = self.solver.query()
        result = AnalysisResult(solver_query)
        self.results = result

        logger.info("Analysis complete")
        return result

    def _validate_method_policy_targets(self) -> None:
        if not self.kcfa_config.method_context_policies:
            return
        if self._captured_method_targets is not None:
            self.context_selector.validate_method_targets(self._captured_method_targets)
            return
        from pythonstan.ir import IRFunc

        available: set[tuple[str, str]] = set()
        for source in self.world.scope_manager.scopes:
            if isinstance(source, IRFunc):
                module = self._get_module_for_scope(source)
                if module is not None:
                    available.add((module.get_qualname(), source.get_qualname()))
        self.context_selector.validate_method_targets(available)

    def _initialize_module_metadata(self, module_scope: Scope, context: NativeContext) -> None:
        """Seed import metadata; project clients explicitly declare script mode."""
        from .points_to_set import PointsToSet
        from .variable import Variable, VariableKind

        module = module_scope.stmt
        name = module.get_qualname()
        filename_service: Literal["get_filename"] = "get_filename"
        filename_provider: object = getattr(module, filename_service)
        if not callable(filename_provider):
            raise TypeError("Module filename provider is not callable")
        filename: object = filename_provider()
        if not isinstance(filename, str):
            raise TypeError("Module filename must be text")
        package = name if os.path.basename(filename) == "__init__.py" else name.rpartition(".")[0]
        values: dict[str, str | None] = {
            "__name__": _module_runtime_metadata(module, "_project_runtime_name", name),
            "__package__": _module_runtime_metadata(module, "_project_runtime_package", package),
        }
        if filename and filename != "None" and os.path.isfile(filename):
            values["__file__"] = os.path.abspath(filename)
        # The public Source/File/Project snapshot owns runtime metadata. Its
        # immutable identity differs from the temporary legacy preprocessing
        # copy; do not rewrite IR filenames or import-resolution roots.
        values.update(self.state.source_metadata.get(name, {}))
        from .input_bindings import seed_module_input_bindings

        seed_module_input_bindings(self.solver, module_scope, context)
        for field_name, value in values.items():
            obj = canonical_constant(
                self.state,
                context,
                AllocSite(("module_metadata", module, field_name), AllocKind.CONSTANT),
                value,
            )
            variable = self.state.get_variable(
                module_scope, context, Variable(field_name, VariableKind.GLOBAL)
            )
            self.solver.handle_new_points_to(variable, module_scope, PointsToSet.singleton(obj))

    def _create_synthetic_method_contexts(
        self, module_scope: Scope, empty_context: NativeContext
    ) -> None:
        """Create synthetic contexts for analyzing method bodies.

        This enables method-to-method call resolution by:
        1. Creating synthetic 'self' instances for each method
        2. Binding 'self' in method-specific contexts
        3. Processing method bodies with bound 'self'

        Args:
            module_scope: The module scope
            empty_context: The empty context for module level
        """
        from pythonstan.ir import IRClass, IRFunc, IRModule

        from .context import CallSite, Scope
        from .object import AllocKind, AllocSite, ClassObject, InstanceObject, ModuleObject
        from .variable import Variable, VariableKind

        # Get all scopes from the scope manager
        scope_manager = self.world.scope_manager
        module_scopes = {
            internal.stmt: internal
            for obj, internal in self.state._internal_scope.items()
            if isinstance(obj, ModuleObject)
        }

        method_count = 0
        class_count = 0

        # Iterate through all scopes to find classes and their methods
        for scope_ir in sorted(scope_manager.scopes, key=_source_scope_key):
            if isinstance(scope_ir, IRClass):
                class_count += 1
                class_qualname = scope_ir.get_qualname()
                defining_module = self._get_module_for_scope(scope_ir)
                lexical_module_scope = (
                    module_scopes.get(defining_module) if defining_module is not None else None
                )
                enclosing = scope_manager.father.get(scope_ir)
                has_enclosing_function = False
                while enclosing is not None and not isinstance(enclosing, IRModule):
                    if isinstance(enclosing, IRFunc):
                        has_enclosing_function = True
                        break
                    enclosing = scope_manager.father.get(enclosing)
                if defining_module is None or lexical_module_scope is None:
                    self.state._residual_effects[(scope_ir, "synthetic_method_owner")] = {
                        "origin": class_qualname,
                        "context": str(empty_context),
                        "reason": "Synthetic method has no registered lexical module owner",
                        "effects": [
                            "untranslated_source",
                            "may_invoke_callbacks",
                            "may_mutate_heap",
                        ],
                    }
                    continue

                # Get subscopes (methods) of this class
                methods = scope_manager.subscopes.get(scope_ir, [])

                for method_ir in sorted(methods, key=_source_scope_key):
                    if not isinstance(method_ir, IRFunc):
                        continue

                    # Only process instance methods (not static or class methods)
                    if not method_ir.is_instance_method:
                        continue

                    method_count += 1
                    method_qualname = method_ir.get_qualname()

                    # Create a method-specific synthetic context
                    # Use a special marker to distinguish from regular call contexts
                    synthetic_expression = ast.parse("synthetic_method()").body[0]
                    assert isinstance(synthetic_expression, ast.Expr)
                    assert isinstance(synthetic_expression.value, ast.Call)
                    synthetic_stmt = IRCall(synthetic_expression.value)
                    synthetic_call_site = CallSite(
                        statement=synthetic_stmt,
                        scope_name=f"synthetic_method:{method_qualname}",
                        index=0,
                    )

                    method_context = self.context_selector.select_call_context(
                        synthetic_call_site, empty_context, None, frozenset()
                    )

                    # Map this instance to its class for method lookup
                    # Find the class object allocation
                    class_alloc_site = AllocSite.from_ir_node(scope_ir, AllocKind.CLASS)

                    # ClassObject needs container_scope and ir parameters
                    # Imported classes retain their defining module globals;
                    # the analysis entry module is not their lexical owner.
                    class_obj = ClassObject(
                        context=self.state.select_allocation_context(
                            empty_context, class_alloc_site
                        ),
                        alloc_site=class_alloc_site,
                        container_scope=lexical_module_scope,
                        ir=scope_ir,
                    )

                    # Create and register internal scope for the class
                    # This is needed for field access resolution on instances
                    class_internal_scope = Scope.new(
                        obj=class_obj,
                        module=lexical_module_scope,
                        context=empty_context,
                        stmt=scope_ir,
                        parent=lexical_module_scope,
                    )
                    self.state.set_internal_scope(class_obj, class_internal_scope)

                    # Create a synthetic InstanceObject for 'self'
                    cls_name = scope_ir.get_qualname().split(".")[-1]
                    instance_expression = ast.parse(f"{cls_name}()").body[0]
                    assert isinstance(instance_expression, ast.Expr)
                    assert isinstance(instance_expression.value, ast.Call)
                    synthetic_alloc_site = AllocSite(
                        stmt=IRCall(instance_expression.value),
                        kind=AllocKind.INSTANCE,
                    )

                    # Create the synthetic instance object with the class object
                    # The class_obj is stored in the InstanceObject itself
                    self_instance = InstanceObject(
                        context=self.state.select_allocation_context(
                            method_context, synthetic_alloc_site, class_obj
                        ),
                        alloc_site=synthetic_alloc_site,
                        class_obj=class_obj,
                    )

                    # Create a scope for this method analysis
                    method_scope = Scope.new(
                        obj=self_instance,  # Use the instance as the scope object
                        module=lexical_module_scope,
                        context=method_context,
                        stmt=method_ir,
                        parent=lexical_module_scope,
                    )
                    self.state.set_internal_scope(self_instance, method_scope)

                    # The closure pass represents module free names as CELL.
                    # A synthetic self has no FunctionObject capture map, so
                    # retain the corresponding defining-module GLOBAL flow.
                    # Classes inside functions require actual closure objects;
                    # their cells must not be guessed from module globals.
                    if not has_enclosing_function:
                        for name in sorted(method_ir.get_cell_vars() - {"__class__"}):
                            source = self.state.get_variable(
                                lexical_module_scope,
                                empty_context,
                                Variable(name, VariableKind.GLOBAL),
                            )
                            target = self.state.get_variable(
                                method_scope, method_context, Variable(name, VariableKind.CELL)
                            )
                            self.state._add_var_points_flow(source, target)

                    # Bind 'self' variable to point to the synthetic instance
                    self_var = self.solver.variable_factory.make_variable(
                        "self", VariableKind.LOCAL
                    )
                    from .pointer_flow_graph import NormalNode
                    from .points_to_set import PointsToSet

                    self_pts = PointsToSet.singleton(self_instance)

                    # Get contextualized variable and add to worklist for propagation
                    ctx_self_var = self.state.get_variable(method_scope, method_context, self_var)
                    self.state._worklist.add((method_scope, NormalNode(ctx_self_var), self_pts))

                    # These optional roots are real analysis work, not ordinary
                    # module initialization. Export their bounded input contract.
                    root_record: JsonObject = {
                        "module": defining_module.get_qualname(),
                        "qualname": method_qualname,
                        "context": str(method_context),
                        "kind": "synthetic_instance_method",
                        "argument_contract": "synthetic_self_only; other external arguments unmodeled",
                        "translation_status": "registered",
                    }
                    self.state.synthetic_method_roots.append(root_record)
                    # Translate the method body and add constraints
                    try:
                        method_constraints = self.translator.translate_function(method_ir)
                        for constraint in method_constraints:
                            self.solver.add_constraint(method_scope, method_context, constraint)
                    except Exception as e:
                        root_record["translation_status"] = "failed_open"
                        self.state._residual_effects[(method_scope, "synthetic_translation")] = {
                            "origin": method_qualname,
                            "reason": f"synthetic method translation failed: {type(e).__name__}: {e}",
                            "effects": [
                                "untranslated_source",
                                "may_mutate_heap",
                                "may_invoke_callbacks",
                                "may_raise",
                            ],
                        }
                        from .object import UnknownValue

                        unknown_site = AllocSite(
                            ("synthetic_translation", method_ir), AllocKind.UNKNOWN
                        )
                        top = UnknownValue(
                            self.state.select_allocation_context(method_context, unknown_site),
                            unknown_site,
                        )
                        self.state._heap.set_obj(method_scope, method_context, unknown_site, top)
                        self.state.obj_scope[top] = method_scope
                        self.state.apply_unknown_effects(method_scope, top, method_qualname)
                        ret = self.solver.variable_factory.make_variable(
                            "$return", VariableKind.TEMPORARY
                        )
                        self.solver.handle_new_points_to(
                            self.state.get_variable(method_scope, method_context, ret),
                            method_scope,
                            PointsToSet.singleton(top),
                        )
                        logger.warning(f"Error translating method {method_qualname}: {e}")

        logger.info(
            f"Created synthetic contexts for {method_count} methods in {class_count} classes"
        )

    def _collect_test_functions(
        self, test_root: str | None, test_files: list[str] | None
    ) -> set[IRFunc]:
        from pythonstan.ir import IRFunc

        if not test_root and not test_files:
            return set()

        scope_manager = self.world.scope_manager
        normalized_files = set()
        if test_files:
            for path in test_files:
                if path:
                    normalized_files.add(os.path.abspath(path))
        print(">>> test_files", test_files, "normalized_files", normalized_files)

        normalized_root = os.path.abspath(test_root) if test_root else None

        test_funcs: set[IRFunc] = set()
        for scope_ir in scope_manager.scopes:
            if not isinstance(scope_ir, IRFunc):
                continue
            parent = scope_manager.father.get(scope_ir)
            module = self._get_module_for_scope(scope_ir)
            if module is None:
                continue
            filename = os.path.abspath(module.get_filename())

            if normalized_files and filename not in normalized_files:
                continue
            if normalized_root and not filename.startswith(normalized_root + os.sep):
                continue
            if not self._is_test_function(scope_ir, parent):
                continue
            test_funcs.add(scope_ir)

        return test_funcs

    def _get_module_for_scope(self, scope_ir: IRScope) -> IRModule | None:
        from pythonstan.ir import IRModule

        scope_manager = self.world.scope_manager
        cur: IRScope | None = scope_ir
        while cur is not None and not isinstance(cur, IRModule):
            cur = scope_manager.father.get(cur)
        if isinstance(cur, IRModule):
            return cur
        return None

    def _is_test_function(self, func_ir: IRFunc, parent: IRScope | None) -> bool:
        name = func_ir.name
        if not name.startswith("test"):
            return False
        from pythonstan.ir import IRClass, IRModule

        if isinstance(parent, IRModule):
            return True
        return isinstance(parent, IRClass) and parent.name.startswith("Test")

    def _initialize_builtins(
        self,
        module_scope: Scope,
        context: NativeContext,
        seed_templates: _BuiltinSeedTemplates | None = None,
    ) -> _BuiltinSeedTemplates | None:
        """Initialize common builtin functions in the global scope.

        Creates builtin function objects for commonly used Python builtins like
        iter, next, len, etc., so they're available when referenced in code.

        Args:
            module_scope: The module scope
            context: The context to use for builtin allocations
            seed_templates: Pure requests from an earlier module in this run.
        """
        from . import points_to_set
        from .bootstrap import (
            try_publish_builtin_seed,
            valid_seed_rows,
            validate_bootstrap_contract,
        )
        from .context import (
            CallStringContext,
            HybridContext,
            ObjectContext,
            ParamContext,
            ReceiverContext,
            SummaryContext,
            TypeContext,
        )
        from .object import ObjectFactory
        from .pointer_flow_graph import NormalNode
        from .points_to_set import PointsToSet
        from .variable import Variable, VariableKind

        # A bare mask cannot survive reset_object_table: the same bits may then
        # identify different objects. Require the same arena and context object;
        # custom context implementations retain the ordinary construction path.
        generation = points_to_set._object_table_generation
        template_context: object = context
        while type(template_context) is SummaryContext:
            template_context = template_context.inner
        cacheable = type(template_context) in (
            CallStringContext,
            ObjectContext,
            TypeContext,
            ReceiverContext,
            ParamContext,
            HybridContext,
        )
        reusable = (
            cacheable
            and type(seed_templates) is _BuiltinSeedTemplates
            and seed_templates.context is context
            and seed_templates.object_table_generation == generation
        )

        # List of common builtin functions to initialize
        BUILTIN_FUNCTIONS = [
            "iter",
            "next",
            "aiter",
            "anext",
            "len",
            "enumerate",
            "zip",
            "map",
            "filter",
            "range",
            "reversed",
            "sorted",
            "sum",
            "min",
            "max",
            "all",
            "any",
            "list",
            "dict",
            "tuple",
            "set",
            "frozenset",
            "str",
            "int",
            "float",
            "bool",
            "bytes",
            "bytearray",
            "memoryview",
            "slice",
            "complex",
            "isinstance",
            "issubclass",
            "type",
            "hasattr",
            "getattr",
            "setattr",
            "delattr",
            "vars",
            "dir",
            "globals",
            "locals",
            "callable",
            "super",
            "print",
            "input",
            "open",
            "object",
            "abs",
            "round",
            "divmod",
            "pow",
            "repr",
            "ascii",
            "format",
            "bin",
            "hex",
            "oct",
            "chr",
            "ord",
            "hash",
            "id",
            "property",
            "classmethod",
            "staticmethod",
        ]

        cached_rows = seed_templates.seeds if seed_templates is not None else ()
        if reusable and not valid_seed_rows(cached_rows, BUILTIN_FUNCTIONS, context):
            reusable = False
        templates: list[tuple[Variable, PointsToSet]] = []

        for index, builtin_name in enumerate(BUILTIN_FUNCTIONS):
            if self.kcfa_config.builtin_bootstrap_policy == "eager_inert":
                validate_bootstrap_contract(self.solver)
            if reusable:
                builtin_var, points = cached_rows[index]
                builtin_obj = None
            else:
                builtin_obj = ObjectFactory.create_builtin_function(builtin_name, context)
                builtin_var = Variable(name=builtin_name, kind=VariableKind.GLOBAL)
                points = PointsToSet.empty()
            ctx_var = self.state.get_variable(module_scope, context, builtin_var)

            # Retain the original first-request ordering: address resolution
            # and NormalNode construction precede singleton interning.
            node = NormalNode(ctx_var)
            if builtin_obj is not None:
                points = PointsToSet.singleton(builtin_obj)
                if cacheable:
                    # Capture the pure singleton before any queue merge. The
                    # module's mutable Box and global location stay separate.
                    templates.append((builtin_var, points))

            # Add the builtin object to the variable's points-to set
            if try_publish_builtin_seed(self.solver, module_scope, ctx_var, points):
                continue
            if self.kcfa_config.points_work_policy == "consumer_fair":
                # This is a queue classification, never lazy builtin omission.
                # All original variables, objects and seed facts remain live.
                # Later rule registration promotes a still-pending seed.
                has_consumers = bool(
                    self.state.constraints._by_variable.get(ctx_var)
                    or self.state.pointer_flow_graph.succs.get(node)
                    or self.solver._builtin_dependencies.get(ctx_var)
                )
                worklist = self.state._consumer_worklist
                if worklist is None:
                    raise RuntimeError("consumer_fair requires its configured worklist")
                worklist.add_seed((module_scope, node, points), cold=not has_consumers)
            else:
                self.state._worklist.add((module_scope, node, points))
            if self.solver._diagnostics is not None:
                self.solver._diagnostics.bump("bootstrap_seed_enqueued")

        from .exception_models import seed_referenced_exceptions

        seed_referenced_exceptions(self, module_scope, context)
        logger.debug(f"Initialized {len(BUILTIN_FUNCTIONS)} builtin functions")
        if reusable:
            return seed_templates
        if cacheable:
            return _BuiltinSeedTemplates(context, generation, tuple(templates))
        return None

    def query(self) -> ISolverQuery:
        """Get query interface for last analysis.

        Returns:
            Query interface for retrieving analysis results

        Raises:
            RuntimeError: If analyze() hasn't been called yet
        """
        if self.results is None:
            raise RuntimeError("Must call analyze() before query()")

        if not isinstance(self.results, AnalysisResult):
            raise TypeError("Native analysis result has an invalid query provider")
        return self.results.query()

    def _setup_logging(self) -> None:
        """Setup logging configuration."""
        logging.basicConfig(
            level=logging.getLevelNamesMapping()[self.kcfa_config.log_level],
            format="%(levelname)s: %(message)s",
        )


class AnalysisResult:
    """Container for analysis results.

    Provides access to points-to information, call graph, and statistics.
    """

    def __init__(self, solver_query: ISolverQuery) -> None:
        """Initialize analysis result.

        Args:
            solver_query: Query interface from solver
        """
        self._query = solver_query

    def query(self) -> ISolverQuery:
        """Get query interface.

        Returns:
            Query interface for this result
        """
        return self._query

    def get_statistics(self) -> JsonObject:
        """Get analysis statistics.

        Returns:
            Dictionary with analysis statistics
        """
        return self._query.get_statistics()
