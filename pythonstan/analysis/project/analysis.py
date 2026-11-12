"""Project inventory and one shared production KCFA arena.

Source files remain separate modules with their own namespaces, globals, IR and
import edges. External dependencies are boundaries unless explicitly included in
the source inventory. No target import/exec or runtime trace feeds the analysis.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import time
import tokenize
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    require_array,
    require_object,
    require_string,
    validate_json_object,
)

from .report_contracts import (
    ModuleRecord,
    ProjectConfigWire,
    ProjectDiagnostic,
    ProjectProgress,
    ProjectReport,
)

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.analysis import PointerAnalysis
    from pythonstan.analysis.pointer.kcfa.constraints import Constraint
    from pythonstan.analysis.pointer.kcfa.context import CallSite, Ctx, NativeContext, Scope
    from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
    from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
    from pythonstan.ir import IRFunc, IRImport, IRModule, IRScope


@dataclass(frozen=True)
class ProjectConfig:
    project_root: str
    entry_file: str
    source_roots: tuple[str, ...] = (".",)
    dependency_source_roots: tuple[str, ...] = ()
    import_roots: tuple[str, ...] = (".",)
    entry_points: tuple[str, ...] = ()
    dependency_paths: tuple[str, ...] = ()
    auto_framework_entries: bool = True
    external_frameworks: tuple[tuple[str, str], ...] = ()
    exclude_files: tuple[str, ...] = ()
    excluded_dirs: tuple[str, ...] = (".git", ".venv", "venv", "__pycache__", "build", "dist")
    max_modules: int = 500
    max_source_bytes: int = 20 * 1024 * 1024
    max_iterations: int = 50_000
    context_policy: str = "2-cfa"
    index_sensitive: bool = True
    enable_ai_intrinsics: bool = False
    synthetic_method_contexts: bool = False
    analysis_stage: str = "solver"
    frontend_lifetime_policy: str = "auto"
    work_scheduling_policy: str = "fair"
    points_work_policy: str = "fifo"
    builtin_bootstrap_policy: str = "queued"
    keyword_context_model: str = "none"
    keyword_context_limit: int = 32

    def __post_init__(self) -> None:
        for name, text in (
            ("project_root", self.project_root),
            ("entry_file", self.entry_file),
            ("context_policy", self.context_policy),
        ):
            if type(text) is not str or not text:
                raise ValueError(name + " must be nonempty text")
        for name, values in (
            ("source_roots", self.source_roots),
            ("dependency_source_roots", self.dependency_source_roots),
            ("import_roots", self.import_roots),
            ("entry_points", self.entry_points),
            ("dependency_paths", self.dependency_paths),
            ("exclude_files", self.exclude_files),
            ("excluded_dirs", self.excluded_dirs),
        ):
            if type(values) is not tuple or any(type(v) is not str or not v for v in values):
                raise ValueError(name + " must be a tuple of nonempty strings")
            if len(set(values)) != len(values):
                raise ValueError("duplicate " + name)
        if not self.source_roots or not self.import_roots:
            raise ValueError("source_roots and import_roots must be nonempty")
        if type(self.analysis_stage) is not str or self.analysis_stage not in {
            "inventory",
            "frontend",
            "solver",
        }:
            raise ValueError("analysis_stage must be inventory, frontend or solver")
        if type(self.frontend_lifetime_policy) is not str or self.frontend_lifetime_policy not in {
            "auto",
            "retain",
            "module_closure",
        }:
            raise ValueError("frontend_lifetime_policy must be auto, retain or module_closure")
        if type(self.work_scheduling_policy) is not str or self.work_scheduling_policy not in {
            "legacy",
            "fair",
        }:
            raise ValueError("work_scheduling_policy must be legacy or fair")
        if type(self.points_work_policy) is not str or self.points_work_policy not in {
            "fifo",
            "consumer_fair",
        }:
            raise ValueError("points_work_policy must be fifo or consumer_fair")
        if type(self.builtin_bootstrap_policy) is not str or self.builtin_bootstrap_policy not in {
            "queued",
            "eager_inert",
        }:
            raise ValueError("builtin_bootstrap_policy must be queued or eager_inert")
        if type(self.keyword_context_model) is not str or self.keyword_context_model not in {
            "none",
            "receiver_key_v1",
        }:
            raise ValueError("keyword_context_model must be none or receiver_key_v1")
        if type(self.keyword_context_limit) is not int or self.keyword_context_limit <= 0:
            raise ValueError("keyword_context_limit must be a positive integer")
        for name, limit in (
            ("max_modules", self.max_modules),
            ("max_source_bytes", self.max_source_bytes),
            ("max_iterations", self.max_iterations),
        ):
            if type(limit) is not int or limit <= 0:
                raise ValueError(name + " must be a positive integer")
        for name, enabled in (
            ("auto_framework_entries", self.auto_framework_entries),
            ("index_sensitive", self.index_sensitive),
            ("enable_ai_intrinsics", self.enable_ai_intrinsics),
            ("synthetic_method_contexts", self.synthetic_method_contexts),
        ):
            if type(enabled) is not bool:
                raise ValueError(name + " must be Boolean")
        if type(self.external_frameworks) is not tuple or any(
            type(item) is not tuple
            or len(item) != 2
            or any(type(v) is not str or not v for v in item)
            for item in self.external_frameworks
        ):
            raise ValueError("external_frameworks must contain (name, version) pairs")
        if len(dict(self.external_frameworks)) != len(self.external_frameworks):
            raise ValueError("duplicate framework boundary")
        from pythonstan.analysis.pointer.kcfa.config import Config

        Config(context_policy=self.context_policy, max_iterations=self.max_iterations)

    def to_dict(self) -> ProjectConfigWire:
        """Project the declared configuration without a dynamic dataclass decoder."""
        return {
            "project_root": self.project_root,
            "entry_file": self.entry_file,
            "source_roots": self.source_roots,
            "dependency_source_roots": self.dependency_source_roots,
            "import_roots": self.import_roots,
            "entry_points": self.entry_points,
            "dependency_paths": self.dependency_paths,
            "auto_framework_entries": self.auto_framework_entries,
            "external_frameworks": self.external_frameworks,
            "exclude_files": self.exclude_files,
            "excluded_dirs": self.excluded_dirs,
            "max_modules": self.max_modules,
            "max_source_bytes": self.max_source_bytes,
            "max_iterations": self.max_iterations,
            "context_policy": self.context_policy,
            "index_sensitive": self.index_sensitive,
            "enable_ai_intrinsics": self.enable_ai_intrinsics,
            "synthetic_method_contexts": self.synthetic_method_contexts,
            "analysis_stage": self.analysis_stage,
            "frontend_lifetime_policy": self.frontend_lifetime_policy,
            "work_scheduling_policy": self.work_scheduling_policy,
            "points_work_policy": self.points_work_policy,
            "builtin_bootstrap_policy": self.builtin_bootstrap_policy,
            "keyword_context_model": self.keyword_context_model,
            "keyword_context_limit": self.keyword_context_limit,
        }

    @classmethod
    def from_dict(cls, data: object) -> ProjectConfig:
        if type(data) is not dict:
            raise ValueError("project configuration must be an object")
        raw: Mapping[object, object] = data
        allowed = set(cls.__dataclass_fields__)
        if set(raw) - allowed:
            raise ValueError(
                "unknown project options: "
                + ", ".join(sorted(str(key) for key in set(raw) - allowed))
            )
        values: dict[str, object] = {}
        for key, value in raw.items():
            if not isinstance(key, str):
                raise ValueError("project option names must be strings")
            values[key] = value
        return cls(
            project_root=_project_text(values, "project_root"),
            entry_file=_project_text(values, "entry_file"),
            source_roots=_project_strings(values, "source_roots", (".",)),
            dependency_source_roots=_project_strings(values, "dependency_source_roots", ()),
            import_roots=_project_strings(values, "import_roots", (".",)),
            entry_points=_project_strings(values, "entry_points", ()),
            dependency_paths=_project_strings(values, "dependency_paths", ()),
            auto_framework_entries=_project_bool(values, "auto_framework_entries", True),
            external_frameworks=_project_frameworks(values.get("external_frameworks", ())),
            exclude_files=_project_strings(values, "exclude_files", ()),
            excluded_dirs=_project_strings(
                values, "excluded_dirs", (".git", ".venv", "venv", "__pycache__", "build", "dist")
            ),
            max_modules=_project_int(values, "max_modules", 500),
            max_source_bytes=_project_int(values, "max_source_bytes", 20 * 1024 * 1024),
            max_iterations=_project_int(values, "max_iterations", 50_000),
            context_policy=_project_text(values, "context_policy", "2-cfa"),
            index_sensitive=_project_bool(values, "index_sensitive", True),
            enable_ai_intrinsics=_project_bool(values, "enable_ai_intrinsics", False),
            synthetic_method_contexts=_project_bool(values, "synthetic_method_contexts", False),
            analysis_stage=_project_text(values, "analysis_stage", "solver"),
            frontend_lifetime_policy=_project_text(values, "frontend_lifetime_policy", "auto"),
            work_scheduling_policy=_project_text(values, "work_scheduling_policy", "fair"),
            points_work_policy=_project_text(values, "points_work_policy", "fifo"),
            builtin_bootstrap_policy=_project_text(values, "builtin_bootstrap_policy", "queued"),
            keyword_context_model=_project_text(values, "keyword_context_model", "none"),
            keyword_context_limit=_project_int(values, "keyword_context_limit", 32),
        )


def _project_text(values: Mapping[str, object], name: str, default: str = "") -> str:
    value = values.get(name, default)
    if type(value) is not str or not value:
        choices = {
            "analysis_stage": "analysis_stage must be inventory, frontend or solver",
            "frontend_lifetime_policy": "frontend_lifetime_policy must be auto, retain or module_closure",
            "work_scheduling_policy": "work_scheduling_policy must be legacy or fair",
            "points_work_policy": "points_work_policy must be fifo or consumer_fair",
            "builtin_bootstrap_policy": "builtin_bootstrap_policy must be queued or eager_inert",
            "keyword_context_model": "keyword_context_model must be none or receiver_key_v1",
        }
        if name in choices:
            raise ValueError(choices[name])
        raise ValueError(name + " must be nonempty text")
    return value


def _project_bool(values: Mapping[str, object], name: str, default: bool) -> bool:
    value = values.get(name, default)
    if type(value) is not bool:
        raise ValueError(name + " must be Boolean")
    return value


def _project_int(values: Mapping[str, object], name: str, default: int) -> int:
    value = values.get(name, default)
    if type(value) is not int or value <= 0:
        raise ValueError(name + " must be a positive integer")
    return value


def _project_strings(
    values: Mapping[str, object],
    name: str,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    value = values.get(name, default)
    if not isinstance(value, (tuple, list)) or type(value) not in (tuple, list):
        raise ValueError(name + " must be an array")
    items: Iterable[object] = value
    result: list[str] = []
    for item in items:
        if type(item) is not str or not item:
            raise ValueError(name + " must be a tuple of nonempty strings")
        result.append(item)
    return tuple(result)


def _project_frameworks(value: object) -> tuple[tuple[str, str], ...]:
    if type(value) is dict:
        mapping: Mapping[object, object] = value
        rows: Iterable[object] = mapping.items()
    elif isinstance(value, (tuple, list)) and type(value) in (tuple, list):
        rows = value
    else:
        raise ValueError("external_frameworks must be an object or pair array")
    result: list[tuple[str, str]] = []
    for item in rows:
        if not isinstance(item, (list, tuple)) or type(item) not in (list, tuple) or len(item) != 2:
            raise ValueError("external_frameworks must contain (name, version) pairs")
        pair: list[object] | tuple[object, ...] = item
        left, right = pair
        if type(left) is not str or not left or type(right) is not str or not right:
            raise ValueError("external_frameworks must contain (name, version) pairs")
        result.append((left, right))
    return tuple(result)


def _under(root: Path, relative: str, *, directory: bool = False) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("project path escapes root: " + relative)
    if directory and not path.is_dir():
        raise ValueError("missing directory: " + relative)
    return path


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(config: ProjectConfig, root: Path) -> tuple[list[ModuleRecord], list[str]]:
    import_roots = [_under(root, name, directory=True) for name in config.import_roots]
    excluded_exact = {_under(root, name) for name in config.exclude_files}
    files: set[Path] = set()
    exclusions: list[str] = []
    ownership: dict[Path, str] = {}
    for relative in (*config.source_roots, *config.dependency_source_roots):
        source = _under(root, relative)
        if not source.exists():
            raise ValueError("missing source root: " + relative)
        candidates = [source] if source.is_file() else sorted(source.rglob("*.py"))
        for path in candidates:
            if path.suffix != ".py":
                continue
            original = str(path.relative_to(root))
            if path.resolve() in excluded_exact:
                exclusions.append(original)
                continue
            if any(part in config.excluded_dirs for part in path.relative_to(root).parts):
                exclusions.append(original)
                continue
            if not path.resolve().is_relative_to(root):
                raise ValueError("source symlink escapes project: " + original)
            resolved = path.resolve()
            files.add(resolved)
            category = "first_party" if relative in config.source_roots else "dependency_source"
            if ownership.get(resolved) == "first_party":
                category = "first_party"
            ownership[resolved] = category
    records: list[ModuleRecord] = []
    for path in sorted(files):
        bases = [base for base in import_roots if path.is_relative_to(base)]
        if not bases:
            records.append(
                {
                    "path": str(path.relative_to(root)),
                    "module": None,
                    "status": "outside_import_roots",
                }
            )
            continue
        base = max(bases, key=lambda p: len(p.parts))
        module_relative = path.relative_to(base)
        parts = list(module_relative.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        name = ".".join(parts)
        # FileFinder/importlib module components are strings, not Python AST
        # identifiers. Django migration names such as "0001_initial" are valid
        # string-import targets. A dot *inside* a filesystem component cannot
        # be represented by this dotted module identity without ambiguity.
        canonical = bool(parts) and all(p and "." not in p and "\x00" not in p for p in parts)
        status = "discovered" if canonical else "invalid_module_name"
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(64 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        records.append(
            {
                "path": str(path.relative_to(root)),
                "module": name or None,
                "status": status,
                "bytes": size,
                "sha256": digest.hexdigest(),
                "module_name_syntax": "identifier"
                if all(p.isidentifier() for p in parts)
                else "string_import_only",
            }
        )
    for record in records:
        record["ownership"] = ownership[(root / record["path"]).resolve()]
    names: dict[str, list[ModuleRecord]] = {}
    for record in records:
        if record["module"]:
            names.setdefault(record["module"], []).append(record)
    for group in names.values():
        if len(group) > 1:
            for record in group:
                record["status"] = "ambiguous_module_identity"
    return records, sorted(set(exclusions))


def _implementation_hash() -> str:
    root = Path(__file__).resolve().parents[3]
    digest = hashlib.sha256()
    for path in sorted((root / "pythonstan").rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode() + b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(64 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _activate_exported_entries(
    driver: PointerAnalysis,
    modules: Mapping[str, IRModule],
    available: Mapping[str, IRFunc],
    selected: Iterable[str],
) -> list[JsonObject]:
    """Invoke actual exported points-to sets, preserving decorator closures.

    These are explicit conditional external calls, not module source statements.
    Unknown argument witnesses do not establish exhaustive Python input coverage.
    The second solve consumes the *remaining* shared iteration budget; it never
    resets the solver's canonical module identities or earlier diagnostics.
    """
    from pythonstan.analysis.pointer.kcfa.constraints import (
        CallConstraint,
        ResidualEffectConstraint,
    )
    from pythonstan.analysis.pointer.kcfa.context import CallSite
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite
    from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
    from pythonstan.analysis.pointer.kcfa.variable import VariableKind
    from pythonstan.ir import IRCall

    from .entry_contracts import derive_exported_function_templates

    records: list[JsonObject] = []
    for name in selected:
        owners = [
            module for module in modules.values() if name.startswith(module.get_qualname() + ".")
        ]
        if not owners:
            continue
        module = max(owners, key=lambda m: len(m.get_qualname()))
        symbol = name[len(module.get_qualname()) + 1 :]
        if "." in symbol:
            records.append(
                {
                    "qualified_name": name,
                    "status": "source_scope_only",
                    "residual": "Nested/class callable export access is not modeled",
                }
            )
            continue
        scope = driver.state.get_scope_for_ir(module)
        if scope is None:
            continue
        exported = driver.solver.variable_factory.make_variable(symbol, VariableKind.GLOBAL)
        exported_var = driver.state.get_variable(scope, scope.context, exported)
        plan = derive_exported_function_templates(driver.state.get_points_to(exported_var))
        for index, template in enumerate(plan.templates):
            node = template.origin_node
            positional_names = list(template.positional_only)
            keyword_names = list(template.keyword_only)
            if template.vararg is None:
                keyword_names = [*template.positional_or_keyword, *keyword_names]
            else:
                positional_names.extend(template.positional_or_keyword)
            # A single additional positional value is one admissible variadic
            # witness, not an exhaustive tuple shape or an original-body guess.
            extra = ["$variadic_witness"] if template.vararg is not None else []
            names = [*positional_names, *keyword_names, *extra]
            arguments = {
                arg: driver.solver.variable_factory.make_variable(
                    f"$external_argument@{name}:{index}:{arg}", VariableKind.TEMPORARY
                )
                for arg in names
            }
            positional_names.extend(extra)
            call_ast = ast.Call(
                func=ast.Name(id=symbol, ctx=ast.Load()),
                args=[ast.Name(id=arguments[arg].name, ctx=ast.Load()) for arg in positional_names],
                keywords=[
                    ast.keyword(arg=arg, value=ast.Name(id=arguments[arg].name, ctx=ast.Load()))
                    for arg in keyword_names
                ],
            )
            ast.copy_location(call_ast, node)
            ast.fix_missing_locations(call_ast)
            call = IRCall(call_ast)
            site = CallSite(
                statement=call, scope_name="<conditional external project entry>", index=index
            )
            for variable in arguments.values():
                unknown = AbstractObject(scope.context, AllocSite(call, AllocKind.UNKNOWN))
                target = driver.state.get_variable(scope, scope.context, variable)
                driver.solver.handle_new_points_to(target, scope, PointsToSet.singleton(unknown))
            callee = driver.solver.variable_factory.make_variable(
                f"$external_callee@{name}:{index}", VariableKind.TEMPORARY
            )
            target = driver.state.get_variable(scope, scope.context, callee)
            driver.solver.handle_new_points_to(
                target, scope, PointsToSet.from_objects(template.functions)
            )
            driver.solver.add_constraint(
                scope,
                scope.context,
                CallConstraint(
                    callee,
                    tuple(arguments[arg] for arg in positional_names),
                    frozenset((arg, arguments[arg]) for arg in keyword_names),
                    None,
                    site,
                ),
            )
        driver.solver.add_constraint(
            scope,
            scope.context,
            ResidualEffectConstraint(
                location=name,
                reason="Conditional external calls use observed exported function snapshots and finite unknown witnesses; later export mutation/reentry, uncovered callable values, arbitrary/default/variadic argument shapes, framework dispatch and async resumption remain open",
                effects=(
                    "external_entry_argument_coverage",
                    "export_binding_mutation",
                    "continuation_coverage",
                    "may_raise",
                    "may_invoke_callbacks",
                    "may_mutate_heap",
                ),
            ),
        )
        records.append(
            {
                "qualified_name": name,
                "status": "exported_binding_call_installed"
                if plan.templates
                else "exported_binding_uncovered",
                "module_binding": symbol,
                "origin": "conditional_external_entry",
                "binding_model": "snapshot_of_exported_function_points",
                "argument_templates": plan.to_dict(),
                "calls_installed": len(plan.templates),
                "variadic_witness_extra_positionals": [
                    int(t.vararg is not None) for t in plan.templates
                ],
                "parameter_coverage": "unknown_witnesses_not_exhaustive",
                "synthetic_callsite": True,
            }
        )
    if any(record["status"] == "exported_binding_call_installed" for record in records):
        while True:
            try:
                next(driver.solver)
            except StopIteration:
                break
    return records


def _install_decorator_contracts(
    driver: PointerAnalysis,
    contracts: Iterable[JsonObject],
    record_by_module: Mapping[IRModule, ModuleRecord],
) -> list[JsonObject]:
    """Add declared callable-identity may-paths at real decorator applications.

    Source identity and original decorator position select a semantic boundary
    contract. The propagated value is the actual input object of the production
    CallConstraint, so source-defined wrappers and closure cells remain intact.
    """
    from pythonstan.analysis.pointer.kcfa.constraints import (
        CallConstraint,
        CopyConstraint,
        ResidualEffectConstraint,
    )

    sources = {module.get_qualname(): row["sha256"] for module, row in record_by_module.items()}
    rules: dict[str, dict[int, JsonObject]] = {}
    for contract in contracts:
        qualified_name = contract.get("qualified_name")
        module_name = contract.get("module")
        if type(qualified_name) is not str or type(module_name) is not str:
            continue
        owners = [name for name in sources if qualified_name.startswith(name + ".")]
        actual_owner = max(owners, key=len) if owners else None
        decorator_index = contract.get("decorator_index")
        if (
            contract.get("operation") != "applied_decorator_preserves_input_callable_identity"
            or type(decorator_index) is not int
            or decorator_index < 0
            or actual_owner != module_name
            or contract.get("source_sha256") != sources.get(module_name)
        ):
            continue
        rules.setdefault(qualified_name, {})[decorator_index] = contract
    applied: list[JsonObject] = []

    def translate(statement: IRFunc, constraints: list[Constraint]) -> list[Constraint]:
        selected = rules.get(statement.get_qualname(), {})
        extra: list[Constraint] = []
        for constraint in constraints:
            if (
                not isinstance(constraint, CallConstraint)
                or constraint.call_site.statement is not statement
            ):
                continue
            source_index = len(statement.decorator_list) - 1 - constraint.call_site.index
            contract = selected.get(source_index)
            if contract is None or len(constraint.args) != 1 or constraint.target is None:
                continue
            if "function_line" in contract and contract["function_line"] != getattr(
                statement.get_ast(), "lineno", None
            ):
                continue
            extra.append(CopyConstraint(constraint.args[0], constraint.target))
            extra.append(
                ResidualEffectConstraint(
                    statement.get_qualname(),
                    "Conditional callable-preserving decorator boundary leaves metadata, registration, exceptions and external effects open",
                    (
                        "conditional_external_decorator_model",
                        "may_mutate_heap",
                        "may_invoke_callbacks",
                        "may_raise",
                    ),
                )
            )
            applied.append(
                {
                    "qualified_name": statement.get_qualname(),
                    "decorator_index": source_index,
                    "contract": dict(contract),
                    "transfer": "actual_decorator_argument_to_result_points_to",
                    "call_edges_injected": 0,
                }
            )
        return [*constraints, *extra]

    driver.translator.function_constraint_transform = translate
    return applied


def _scope_context_counts(state: PointerAnalysisState) -> Counter[IRScope]:
    """Batch the existing query's counts without rescanning every context per IR.

    Preserve the query's historical internal-scope multiplicity and its
    call-graph deduplication. This is report projection only; solver facts and
    scope registration are untouched.
    """
    counts: Counter[IRScope] = Counter()
    seen: defaultdict[IRScope, set[Scope]] = defaultdict(set)
    for scope in state._internal_scope.values():
        counts[scope.stmt] += 1
        seen[scope.stmt].add(scope)
    for scope in state.call_graph.get_nodes():
        if scope not in seen[scope.stmt]:
            counts[scope.stmt] += 1
            seen[scope.stmt].add(scope)
    return counts


def _caller_scope_name(callsite: Ctx[CallSite]) -> str:
    scope = callsite.scope
    if scope is None:
        raise ValueError("Project call edge has no caller scope")
    return scope.stmt.get_qualname()


def _project_diagnostic(
    diagnostic: ProjectDiagnostic,
    hook: Literal["solver_ready", "project_finished"],
    solver: PointerSolver | None,
    report: ProjectReport,
) -> None:
    """Invoke an explicitly injected trusted control hook outside solver work.

    The native analyzer never imports the optional inspector. Ordinary observer
    failures affect only observation health; interruption is not swallowed.
    """
    try:
        if hook == "solver_ready":
            if solver is None:
                raise ValueError("solver_ready requires an initialized solver")
            diagnostic.solver_ready(solver)
        else:
            diagnostic.project_finished(solver, report)
    except Exception as exc:
        observation = report.setdefault("inspector", {"status": "failed", "errors": []})
        observation["status"] = "failed"
        observation["errors"].append({"hook": hook, "exception": type(exc).__name__})


def _finish_project_report(
    report: ProjectReport,
    config: ProjectConfig,
    root: Path,
    records: list[ModuleRecord],
    before: str,
    started: float,
    *,
    diagnostic: ProjectDiagnostic | None = None,
    solver: PointerSolver | None = None,
) -> ProjectReport:
    for row in records:
        if "sha256" in row:
            path = root / row["path"]
            try:
                stable = _file_digest(path) == row["sha256"]
            except (OSError, MemoryError):
                stable = False
            if diagnostic is not None and "consumed_sha256" in row:
                stable = stable and row["consumed_sha256"] == row["sha256"]
            row["source_stable"] = stable
            report["source_snapshot_stable"] &= stable
    report["coverage"] = {
        "discovered_python_files": len(records),
        **{
            name + "_modules": sum(bool(row[name]) for row in records)
            for name in ("parsed", "lowered", "translated", "solver_constraints_scheduled")
        },
        "unprocessed_files": sum(not row["translated"] for row in records),
        "dependency_boundaries": len(report["dependency_boundaries"]),
        "whole_declared_first_party_source_lowered": bool(records)
        and all(row["lowered"] for row in records),
        "whole_declared_first_party_module_constraints_translated": bool(records)
        and all(row["translated"] for row in records),
    }
    try:
        current_inventory, _ = _inventory(config, root)
        original_snapshot = {(row["path"], row.get("sha256")) for row in records}
        current_snapshot = {(row["path"], row.get("sha256")) for row in current_inventory}
        report["source_snapshot_stable"] &= original_snapshot == current_snapshot
    except (OSError, ValueError, MemoryError):
        report["source_snapshot_stable"] = False
    report["inventory"] = report["modules"]
    report["entry_points"] = report.get("resolved_entry_points", [])
    first_party = [row for row in records if row["ownership"] == "first_party"]
    dependencies = [row for row in records if row["ownership"] == "dependency_source"]
    report["coverage"].update(
        whole_selected_module_source_lowered=bool(records)
        and all(row["lowered"] for row in records),
        whole_declared_first_party_source_lowered=bool(first_party)
        and all(row["lowered"] for row in first_party),
        whole_declared_first_party_module_constraints_translated=bool(first_party)
        and all(row["translated"] for row in first_party),
        first_party_discovered=len(first_party),
        first_party_lowered=sum(row["lowered"] for row in first_party),
        first_party_translated=sum(row["translated"] for row in first_party),
        dependency_source_discovered=len(dependencies),
        dependency_source_translated=sum(row["translated"] for row in dependencies),
    )
    report["first_party_translation_complete"] = bool(first_party) and all(
        row["translated"] for row in first_party
    )
    report["frontend_executed"] = any(row["parsed"] for row in records)
    report["synthetic_method_contexts_enabled"] = config.synthetic_method_contexts
    report["synthetic_method_context_policy"] = (
        "Legacy synthetic self contexts are enabled by explicit project configuration; their counts do not establish concrete entry reachability or complete argument coverage."
        if config.synthetic_method_contexts
        else "Declared entries and actual calls only; automatic synthetic self roots are disabled. Uncalled method scopes are inventoried but are not claimed analyzed."
    )
    if report["solver_completed"] and report["coverage"]["unprocessed_files"]:
        report["status"] = "partial_source_analysis"
    try:
        report["implementation_sha256_after"] = _implementation_hash()
    except MemoryError:
        report["implementation_sha256_after"] = None
        report["implementation_verification_error"] = (
            "MemoryError during final implementation verification"
        )
    report["implementation_stable"] = before == report["implementation_sha256_after"]
    report["wall_seconds"] = time.perf_counter() - started
    if not report["source_snapshot_stable"] or not report["implementation_stable"]:
        report["status"] = "unstable_evidence"
    if diagnostic is not None:
        _project_diagnostic(diagnostic, "project_finished", solver, report)
    return report


def analyze_project(
    config: ProjectConfig | dict[str, object],
    *,
    progress: ProjectProgress | None = None,
    diagnostic: ProjectDiagnostic | None = None,
) -> ProjectReport:
    """Analyze all declared first-party files in one arena and expose coverage.

    ``source_roots`` selects source trees, while ``import_roots`` determines
    canonical module names. Framework entries are conditional external roots;
    their argument coverage and framework dispatch remain explicit boundaries.
    This API has iteration/source budgets. A supervising process supplies wall
    and memory limits, since the global World arena is deliberately process-local.
    ``diagnostic`` is an optional trusted control hook with ``solver_ready`` and
    ``project_finished`` methods. It may enable passive observations and capture
    at the final verified boundary; it must not alter roots or request work.
    """
    if type(config) is dict:
        config = ProjectConfig.from_dict(config)
    if type(config) is not ProjectConfig:
        raise ValueError("expected ProjectConfig")
    root = Path(config.project_root).resolve()
    if not root.is_dir():
        raise ValueError("project_root must be a directory")
    entry = _under(root, config.entry_file)
    before = _implementation_hash()
    started = time.perf_counter()
    solver: PointerSolver | None = None
    sequence = 0
    last_emitted_phase: str | None = None
    config_digest = hashlib.sha256(
        json.dumps(config.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    def emit(phase: str, **details: object) -> None:
        nonlocal sequence, last_emitted_phase
        last_emitted_phase = phase
        if progress is not None:
            sequence += 1
            progress(
                validate_json_object(
                    {
                        "schema_version": 1,
                        "sequence": sequence,
                        "phase": phase,
                        "implementation_sha256_before": before,
                        "configuration_sha256": config_digest,
                        "elapsed_seconds": time.perf_counter() - started,
                        **details,
                    }
                )
            )

    emit("inventory_started")
    records, excluded = _inventory(config, root)
    report: ProjectReport = {
        "schema_version": 1,
        "analysis_unit": "complete_declared_first_party_source_trees",
        "solver_completed": False,
        "semantic_complete": False,
        "status": "error",
        "effective_config": config.to_dict(),
        "modules": records,
        "excluded_files": excluded,
        "source_snapshot_stable": True,
        "implementation_sha256_before": before,
        "implementation_sha256_after": None,
        "implementation_stable": False,
        "call_edges": [],
        "import_edges": [],
        "dependency_boundaries": [],
        "frontend_errors": [],
        "entry_roots": [],
        "unresolved_entries": [],
        "analysis_executed": False,
        "whole_program_soundness_proved": False,
        "analysis_complete": False,
        "runtime_observations_used": False,
        "target_source_executed": False,
        "coverage_interpretation": "File discovery/lowering is not function-body reachability, framework dispatch coverage, or semantic completeness.",
        "boundary_policy": "Only explicitly inventoried source is analyzed. Third-party, stdlib, dynamic imports and native effects remain open.",
    }
    # Record phase flags independently of the terminal per-file status.
    for row in records:
        row.update(
            {
                "parsed": False,
                "lowered": False,
                "translated": False,
                "solver_constraints_scheduled": False,
            }
        )
    report["requested_analysis_stage"] = config.analysis_stage
    # Frontend-only and inventory callers retain their established inspection
    # API, including statement CFGs. Streaming belongs to the project solver.
    lifetime_policy = (
        "module_closure"
        if config.analysis_stage == "solver" and config.frontend_lifetime_policy != "retain"
        else "retain"
    )
    report["frontend_lifetime"] = {
        "requested_policy": config.frontend_lifetime_policy,
        "effective_policy": lifetime_policy,
        "canonical_ir_preserved": True,
        "block_cfg_preserved": True,
        "statement_cfg_inspection_available": lifetime_policy == "retain",
    }
    report["configuration_sha256"] = config_digest
    emit("inventory_completed", inventory=[dict(row) for row in records], excluded_files=excluded)
    if not any((root / row["path"]).resolve() == entry for row in records):
        raise ValueError("entry_file must belong to the selected first-party inventory")
    if config.analysis_stage == "inventory":
        report["status"] = "inventory_completed"
        return _finish_project_report(
            report, config, root, records, before, started, diagnostic=diagnostic
        )
    from pythonstan.analysis.analysis import AnalysisConfig
    from pythonstan.analysis.pointer.kcfa import Config as PointerConfig
    from pythonstan.analysis.pointer.kcfa import PointerAnalysis
    from pythonstan.ir import IRFunc, IRModule
    from pythonstan.world.analysis_manager import AnalysisManager
    from pythonstan.world.config import Config as WorldConfig
    from pythonstan.world.namespace import Namespace
    from pythonstan.world.scope_manager import ModuleGraph
    from pythonstan.world.world import World

    World.setup()
    world = World()
    world_config = WorldConfig(str(entry), str(root), mock_libs=False)
    world_config.library_paths = [str(_under(root, p)) for p in config.import_roots] + list(
        config.dependency_paths
    )
    world.build(world_config)
    manager = AnalysisManager()
    manager.build([])
    world.analysis_manager = manager
    from pythonstan.analysis.closure import ClosureAnalysis

    from .frontend_lifetime import FrontendLifetimeError, ModuleClosureLifetime

    closure = manager.get_analyzer("closure")
    if not isinstance(closure, ClosureAnalysis):
        raise TypeError("closure analyzer must implement ClosureAnalysis")
    lifetime = (
        ModuleClosureLifetime(
            closure,
            (name for row in records if (name := row["module"])),
            report["frontend_lifetime"],
        )
        if lifetime_policy == "module_closure"
        else None
    )
    modules: dict[str, IRModule] = {}
    record_by_module: dict[IRModule, ModuleRecord] = {}
    imports_by_module: dict[IRModule, tuple[IRImport, ...]] = {}
    total_bytes = 0
    for row in records:
        if row["status"] != "discovered":
            continue
        if (
            len(modules) >= config.max_modules
            or total_bytes + row["bytes"] > config.max_source_bytes
        ):
            row["status"] = "source_budget_exhausted"
            emit("module_budget_exhausted", module_record=dict(row))
            continue
        total_bytes += row["bytes"]
        module_name = row["module"]
        if module_name is None:
            raise ValueError("discovered module must have a canonical name")
        path = root / row["path"]
        emit("module_parse_started", module=row["module"], path=row["path"])
        try:
            raw = path.read_bytes()
            if diagnostic is not None:
                row["consumed_sha256"] = hashlib.sha256(raw).hexdigest()
                row["consumed_bytes"] = len(raw)
                row["source_read_matches_inventory"] = row["consumed_sha256"] == row["sha256"]
                report["source_snapshot_stable"] &= row["source_read_matches_inventory"]
            encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
            text = raw.decode(encoding)
            compile(text, str(path), "exec", dont_inherit=True)  # syntax validation only
            tree = ast.parse(text, str(path))
            from pythonstan.analysis.contracts import SourceModule

            source_snapshot = SourceModule(
                module_name,
                str(path),
                text,
                is_package=path.name == "__init__.py",
                original_bytes_digest=hashlib.sha256(raw).hexdigest(),
                encoding=encoding,
            )
            module = IRModule(
                module_name,
                tree,
                module_name.rsplit(".", 1)[-1],
                str(path),
                source_snapshot=source_snapshot,
            )
            sm = world.scope_manager
            sm.scopes.add(module)
            sm.names2scope[module_name] = module
            sm.file2mod[str(path)] = module
            ns = Namespace.from_str(module_name)
            world.module2ns[module] = ns
            world.namespace_manager.ns2path[ns] = str(path)
            world.namespace_manager.names2path[module_name] = str(path)
            modules[module_name] = module
            record_by_module[module] = row
            row["parsed"] = True
            for phase in ("three address", "ir", "block cfg", "cfg"):
                emit(
                    "module_frontend_started",
                    module=row["module"],
                    frontend_phase=phase,
                    module_record=dict(row),
                )
                manager.analysis(phase, module)
            imports_by_module[module] = tuple(world.scope_manager.get_import_ir(module) or ())
            row["lowered"] = True
            row["status"] = "lowered"
            if lifetime is not None:
                emit("module_closure_started", module=row["module"], module_record=dict(row))
                lifetime.consume(module)
                emit(
                    "module_closure_completed",
                    module=row["module"],
                    frontend_lifetime=dict(report["frontend_lifetime"]),
                )
            emit("module_frontend_completed", module_record=dict(row))
        except FrontendLifetimeError as exc:
            row["status"] = "frontend_ownership_error"
            row["diagnostic"] = str(exc)
            report["frontend_errors"].append({"module": row["module"], "diagnostic": str(exc)})
            report["status"] = "analysis_error"
            report["analysis_error"] = type(exc).__name__ + ": " + str(exc)
            report["analysis_failure_phase"] = last_emitted_phase
            emit(
                "analysis_error",
                analysis_error=report["analysis_error"],
                failure_phase=last_emitted_phase,
                module_record=dict(row),
            )
            return _finish_project_report(
                report, config, root, records, before, started, diagnostic=diagnostic
            )
        except MemoryError:
            row["status"] = "frontend_resource_exhausted"
            row["diagnostic"] = "MemoryError in frontend"
            report["frontend_errors"].append(
                {"module": row["module"], "diagnostic": row["diagnostic"]}
            )
            report["status"] = "resource_exhausted"
            report["analysis_error"] = row["diagnostic"]
            report["analysis_failure_phase"] = last_emitted_phase
            with suppress(MemoryError):
                emit(
                    "analysis_resource_exhausted",
                    analysis_error=row["diagnostic"],
                    failure_phase=report["analysis_failure_phase"],
                    module_record=dict(row),
                )
            # A fatal allocation failure cannot safely enter the global arena
            # with this partially lowered module. Preserve all unprocessed rows.
            return _finish_project_report(
                report, config, root, records, before, started, diagnostic=diagnostic
            )
        except Exception as exc:
            row["status"] = "frontend_error"
            row["diagnostic"] = type(exc).__name__ + ": " + str(exc)
            report["frontend_errors"].append(
                {"module": row["module"], "diagnostic": row["diagnostic"]}
            )
            emit("module_frontend_error", module_record=dict(row))
    if config.analysis_stage == "frontend":
        report["status"] = (
            "frontend_completed" if all(row["lowered"] for row in records) else "partial_frontend"
        )
        return _finish_project_report(
            report, config, root, records, before, started, diagnostic=diagnostic
        )
    emit("import_graph_started")
    graph = ModuleGraph()
    for module, row in record_by_module.items():
        if row["lowered"]:
            graph.add_node(module)
    world.scope_manager.set_module_graph(graph)
    for module in sorted(graph.get_modules(), key=lambda m: m.get_qualname()):
        ns = world.module2ns[module]
        for statement in imports_by_module.get(module, ()):
            resolved = None
            try:
                resolved = world.namespace_manager.get_import(ns, statement)
            except Exception as exc:
                report["dependency_boundaries"].append(
                    {
                        "module": module.get_qualname(),
                        "import": str(statement),
                        "kind": "resolution_error",
                        "diagnostic": type(exc).__name__ + ": " + str(exc),
                    }
                )
                continue
            if resolved:
                target_ns, target_path = resolved
                target = modules.get(target_ns.to_str())
                if target is not None and target in graph.get_modules():
                    graph.add_edge(module, statement, target)
                    world.import_manager.set_import(module, statement, target)
                    report["import_edges"].append([module.get_qualname(), target.get_qualname()])
                    continue
            report["dependency_boundaries"].append(
                {
                    "module": module.get_qualname(),
                    "import": str(statement),
                    "kind": "outside_source_inventory" if resolved else "unresolved_import",
                    "resolved_path": str(resolved[1]) if resolved else None,
                }
            )
    chosen = next(
        (
            module
            for module, row in record_by_module.items()
            if (root / row["path"]).resolve() == entry and row["lowered"]
        ),
        None,
    )
    if chosen is not None:
        world.set_entry_module(chosen)
        selected = set(config.entry_points)
        if config.auto_framework_entries:
            try:
                from pythonstan.analysis.project_frameworks import discover_framework_entries

                framework = validate_json_object(
                    discover_framework_entries(
                        {
                            name: root / record_by_module[module]["path"]
                            for name, module in modules.items()
                        },
                        external_frameworks=dict(config.external_frameworks),
                    )
                )
                report["framework_entry_discovery"] = framework
                entries = [require_object(item) for item in require_array(framework["entries"])]
                selected.update(require_string(item["qualified_name"]) for item in entries)
                report["entry_roots"].extend(entries)
            except ImportError as exc:
                report["framework_entry_discovery"] = {"entries": [], "residuals": [str(exc)]}
        available = {
            scope.get_qualname(): scope
            for scope in world.scope_manager.get_scopes()
            if isinstance(scope, IRFunc)
        }
        valid = sorted(name for name in selected if name in available)
        report["unresolved_entries"] = sorted(selected - set(valid))
        report["entry_roots"].extend(
            {
                "qualified_name": name,
                "origin": "explicit_entry",
                "parameter_coverage": "unmodeled_unknown_external_arguments",
            }
            for name in config.entry_points
            if name in available
        )
        report["resolved_entry_points"] = valid
        # Top-level roots invoke their actual exported values after initialization;
        # pre-seeding the undecorated source body would bypass replacement decorators.
        click_callback_entries = {
            require_string(item["qualified_name"])
            for raw_item in require_array(
                report.get("framework_entry_discovery", {}).get("entries", [])
            )
            if (item := require_object(raw_item))
            if item.get("framework") == "click"
            and item.get("kind") == "cli_command"
            and item.get("callback_identity_verified") is True
            and item["qualified_name"] in valid
        }
        report["framework_callback_entry_contracts"] = [
            {
                "qualified_name": name,
                "origin": "conditional_click_command_callback",
                "entry_model": "source callback may be invoked by native Command",
                "module_export_is_function": False,
                "parameter_coverage": "unmodeled_unknown_external_arguments",
            }
            for name in sorted(click_callback_entries)
        ]
        source_scope_entries = sorted(click_callback_entries)
        for name in valid:
            owners = [
                module
                for module in modules.values()
                if name.startswith(module.get_qualname() + ".")
            ]
            owner = max(owners, key=lambda module: len(module.get_qualname())) if owners else None
            if owner is None or "." in name[len(owner.get_qualname()) + 1 :]:
                source_scope_entries.append(name)
        report["source_scope_entry_assumptions"] = source_scope_entries
        options = PointerConfig(
            context_policy=config.context_policy,
            max_iterations=config.max_iterations,
            work_scheduling_policy=config.work_scheduling_policy,
            points_work_policy=config.points_work_policy,
            builtin_bootstrap_policy=config.builtin_bootstrap_policy,
            keyword_context_model=config.keyword_context_model,
            keyword_context_limit=config.keyword_context_limit,
            index_sensitive=config.index_sensitive,
            enable_ai_intrinsics=config.enable_ai_intrinsics,
            enable_synthetic_method_contexts=config.synthetic_method_contexts,
            entry_points=source_scope_entries,
            verbose=False,
            log_level="ERROR",
            project_path=str(root),
        ).to_dict()
        driver = PointerAnalysis(AnalysisConfig("project_kcfa", "PointerAnalysis", options=options))
        solver = driver.solver
        if diagnostic is not None:
            _project_diagnostic(diagnostic, "solver_ready", solver, report)
        report["boundary_contract_applications"] = _install_decorator_contracts(
            driver,
            (
                require_object(item)
                for item in require_array(
                    report.get("framework_entry_discovery", {}).get(
                        "identity_decorator_contracts", []
                    )
                )
            ),
            record_by_module,
        )
        scheduled: set[IRScope] = set()

        def observe(scope: Scope, context: NativeContext, constraint: Constraint) -> None:
            scheduled.add(scope.stmt)

        driver.solver.constraint_observer = observe

        def solve_with_checkpoint() -> None:
            translated = set(driver.translator._scope_constraints)
            updates: list[ModuleRecord] = []
            for module, row in record_by_module.items():
                row["translated"] = module in translated
                row["solver_constraints_scheduled"] = module in scheduled
                updates.append(row.copy())
            emit("solver_fixpoint_started", module_updates=updates)

        driver.solver.fixpoint_observer = solve_with_checkpoint
        try:
            emit("closure_started", entry_points=valid)
            if lifetime is None:
                manager.analysis("closure", chosen)
            else:
                lifetime.finish()
                manager.results["closure"] = None
            emit("pointer_analysis_started")
            result = driver.analyze(chosen, {})
            if result.get_statistics().get("converged", False):
                emit("export_entries_started", statistics=result.get_statistics())
                report["exported_entry_activation"] = _activate_exported_entries(
                    driver,
                    modules,
                    available,
                    [name for name in valid if name not in click_callback_entries],
                )
            report["analysis_executed"] = True
            query = result.query()
            emit("report_statistics_started")
            stats = query.get_statistics()
            report["statistics"] = stats
            emit("report_semantics_started", statistics=stats)
            report["semantic_status"] = query.get_semantic_status()
            emit("report_unknowns_started")
            report["unknown_records"] = query.get_unknown_details()
            emit("report_residuals_started")
            report["residual_effects"] = query.get_residual_effects()
            emit("report_call_graph_started")
            report["call_edges"] = [
                list(pair)
                for pair in sorted(
                    {
                        (_caller_scope_name(edge.callsite), edge.callee.stmt.get_qualname())
                        for edge in query.call_graph().edges
                    }
                )
            ]
            translated = set(driver.translator._scope_constraints)
            for module, row in record_by_module.items():
                row["translated"] = module in translated
                row["solver_constraints_scheduled"] = module in scheduled
                if row["translated"]:
                    row["status"] = "constraints_translated"
            emit("report_scope_coverage_started")
            context_counts = _scope_context_counts(driver.state)
            report["scope_coverage"] = [
                {
                    "name": scope.get_qualname(),
                    "kind": type(scope).__name__,
                    "translated": scope in translated,
                    "constraints_scheduled": scope in scheduled,
                    "call_graph_contexts": context_counts[scope],
                }
                for scope in sorted(
                    world.scope_manager.get_scopes(), key=lambda s: s.get_qualname()
                )
            ]
            report["constraint_fixpoint_reached"] = bool(stats.get("converged", False))
            report["solver_completed"] = report["constraint_fixpoint_reached"]
            report["status"] = (
                "completed_with_residuals"
                if report["constraint_fixpoint_reached"]
                else "incomplete"
            )
            emit("solver_completed", statistics=stats, solver_completed=report["solver_completed"])
        except Exception as exc:
            report["status"] = (
                "resource_exhausted" if isinstance(exc, MemoryError) else "analysis_error"
            )
            report["analysis_error"] = type(exc).__name__ + ": " + str(exc)
            report["analysis_failure_phase"] = last_emitted_phase
            with suppress(MemoryError):
                emit(
                    "analysis_resource_exhausted"
                    if isinstance(exc, MemoryError)
                    else "analysis_error",
                    analysis_error=report["analysis_error"],
                    failure_phase=report["analysis_failure_phase"],
                )
    else:
        report["analysis_error"] = "entry module did not lower successfully"
    emit("final_source_verification_started")
    final_report = _finish_project_report(
        report, config, root, records, before, started, diagnostic=diagnostic, solver=solver
    )
    emit(
        "report_ready",
        solver_completed=final_report["solver_completed"],
        statistics=final_report.get("statistics", {}),
    )
    return final_report
