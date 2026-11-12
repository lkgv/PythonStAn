"""Static source inventory, import identities, and independent entry scenarios."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import io
import tokenize
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType

from pythonstan.analysis.contracts.identity import EntryScenario, SourceModule, stable_digest
from pythonstan.analysis.contracts.json_data import (
    FrozenJsonObject,
    FrozenJsonValue,
    JsonObject,
    JsonValue,
    freeze_json,
    freeze_json_object,
    validate_json,
    validate_json_object,
)
from pythonstan.config.model import EffectiveConfig, InputsConfig
from pythonstan.frontend.private_names import mangle

from .reuse import input_resolution_contract
from .targets import File, Project, Source, Target

type ParsedTreeCache = Mapping[str, tuple[str, ast.Module]]
type MutableParsedTreeCache = MutableMapping[str, tuple[str, ast.Module]]


class InputError(ValueError):
    pass


@dataclass(frozen=True, init=False)
class SourceSnapshot:
    """Immutable source identities with a parser cache owned by one execution.

    Target and inventory are JSON boundary records. Construction validates and
    freezes them, so replacing an execution's scenario order cannot expose a
    writable alias. AST objects are internal mutable IR; reruns discard the cache.
    """

    modules: tuple[SourceModule, ...]
    scenarios: tuple[EntryScenario, ...]
    target: FrozenJsonObject
    source_digest: str
    inventory: tuple[FrozenJsonObject, ...]
    parsed_trees: ParsedTreeCache = field(repr=False, compare=False)
    input_resolution: FrozenJsonObject = field(repr=False)

    def __init__(
        self,
        modules: tuple[SourceModule, ...],
        scenarios: tuple[EntryScenario, ...],
        target: object,
        source_digest: str,
        inventory: Sequence[object],
        parsed_trees: ParsedTreeCache | None = None,
        *,
        input_resolution: object | None = None,
    ) -> None:
        object.__setattr__(self, "modules", tuple(modules))
        object.__setattr__(self, "scenarios", tuple(scenarios))
        object.__setattr__(self, "target", freeze_json_object(validate_json_object(target)))
        object.__setattr__(self, "source_digest", source_digest)
        object.__setattr__(
            self,
            "inventory",
            tuple(freeze_json_object(validate_json_object(row)) for row in inventory),
        )
        object.__setattr__(self, "parsed_trees", MappingProxyType(dict(parsed_trees or {})))
        object.__setattr__(
            self,
            "input_resolution",
            freeze_json_object(
                validate_json_object({} if input_resolution is None else input_resolution)
            ),
        )

    @property
    def module_map(self) -> dict[str, SourceModule]:
        return {module.module: module for module in self.modules}

    @property
    def entry_contract(self) -> JsonObject:
        scenarios: list[JsonValue] = [
            {
                "id": scenario.scenario_id,
                "module": scenario.root_module,
                "qualname": scenario.entry_qualname,
                "arguments": validate_json(scenario.arguments),
                "coverage": scenario.coverage,
            }
            for scenario in self.scenarios
        ]
        return {
            "entry_coverage": "initialization_only"
            if all(scenario.entry_qualname is None for scenario in self.scenarios)
            else "declared_entries",
            "scenarios": scenarios,
        }

    def for_rerun(self) -> SourceSnapshot:
        """Retain source bytes and scenarios while removing the previous AST owner."""
        return replace(self, parsed_trees={})


def _read(
    path: Path,
    module: str,
    is_package: bool,
    roots_digest: str,
    parsed_trees: MutableParsedTreeCache | None = None,
) -> SourceModule:
    try:
        raw = path.read_bytes()
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        source = raw.decode(encoding)
        tree = ast.parse(source, filename=str(path), type_comments=True, feature_version=(3, 12))
        result = SourceModule(
            module,
            str(path),
            source,
            is_package=is_package,
            original_bytes_digest=hashlib.sha256(raw).hexdigest(),
            encoding=encoding,
            import_root_digest=roots_digest,
        )
        if parsed_trees is not None:
            parsed_trees[module] = (result.digest, tree)
        return result
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise InputError(f"{path}: {exc}") from exc


def _module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts else root.name


def _imports(module: SourceModule) -> set[str]:
    tree = ast.parse(module.source)
    package = module.module if module.is_package else module.module.rpartition(".")[0]
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".") if package else []
                trim = node.level - 1
                base = ".".join(parts[: len(parts) - trim] if trim else parts)
                name = ".".join(x for x in (base, node.module) if x)
            else:
                name = node.module or ""
            if name:
                result.add(name)
            for alias in node.names:
                if alias.name != "*":
                    result.add(".".join(x for x in (name, alias.name) if x))
    return result


def _add_dependencies(
    modules: dict[str, SourceModule],
    roots: Sequence[Path],
    roots_digest: str,
    parsed_trees: MutableParsedTreeCache | None = None,
) -> None:
    pending = list(modules.values())
    while pending:
        module = pending.pop()
        for name in sorted(_imports(module)):
            pieces = name.split(".")
            for size in range(1, len(pieces) + 1):
                candidate = ".".join(pieces[:size])
                if candidate in modules:
                    continue
                found = None
                for root in roots:
                    stem = root.joinpath(*pieces[:size])
                    for path, package in (
                        (stem / "__init__.py", True),
                        (stem.with_suffix(".py"), False),
                    ):
                        if path.is_file():
                            found = _read(path, candidate, package, roots_digest, parsed_trees)
                            break
                    if found:
                        break
                if found:
                    modules[candidate] = found
                    pending.append(found)


def _entry(module: SourceModule, qualname: str | None) -> EntryScenario:
    if qualname is None:
        return EntryScenario(module.module)
    parts = qualname.split(".")
    nodes: list[ast.stmt] = ast.parse(module.source).body
    found: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | None = None
    private: str | None = None
    for part in parts:
        found = next(
            (
                n
                for n in nodes
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and n.name == part
            ),
            None,
        )
        if found is None:
            raise InputError(f"Unresolved entry: {module.module}:{qualname}")
        if isinstance(found, ast.ClassDef):
            private = found.name
        nodes = found.body
    if not isinstance(found, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise InputError(f"Entry is not a source function: {module.module}:{qualname}")
    required = len(found.args.posonlyargs) + len(found.args.args) - len(found.args.defaults)
    arguments: tuple[FrozenJsonValue, ...] = tuple(
        freeze_json({"top": True, "input_contract": "arbitrary_python_value"})
        for _ in range(required)
    )
    arguments += tuple(
        freeze_json(
            {
                "keyword": mangle(private, arg.arg),
                "abstract_value": {"top": True, "input_contract": "arbitrary_python_value"},
            }
        )
        for arg, default in zip(found.args.kwonlyargs, found.args.kw_defaults, strict=False)
        if default is None
    )
    return EntryScenario(
        module.module, qualname, arguments=arguments, coverage="declared_abstract_inputs"
    )


def snapshot_target(
    target: Target,
    config: EffectiveConfig | Mapping[str, JsonValue],
) -> SourceSnapshot:
    if not isinstance(target, (Source, File, Project)):
        raise TypeError("target must be Source, File, or Project")
    options = (
        config.inputs
        if isinstance(config, EffectiveConfig)
        else InputsConfig.from_wire(config["inputs"])
    )
    modules: dict[str, SourceModule] = {}
    parsed_trees: dict[str, tuple[str, ast.Module]] = {}
    roots: list[Path] = []
    main: str | None
    entries: tuple[str, ...]
    target_record: JsonObject
    if isinstance(target, Source):
        roots = [Path(p).expanduser().resolve() for p in target.import_roots]
        if target.project_root is not None:
            roots.append(Path(target.project_root).expanduser().resolve())
        if options.project_root is not None:
            roots.append(Path(options.project_root).expanduser().resolve())
        roots += [Path(p).expanduser().resolve() for p in options.source_roots]
        try:
            tree = ast.parse(
                target.code, filename=target.filename, type_comments=True, feature_version=(3, 12)
            )
        except SyntaxError as exc:
            raise InputError(str(exc)) from exc
        roots_digest = stable_digest([str(p) for p in roots])
        modules[target.module] = SourceModule(
            target.module,
            target.filename,
            target.code,
            is_package=Path(target.filename).name == "__init__.py",
            import_root_digest=roots_digest,
        )
        parsed_trees[target.module] = (modules[target.module].digest, tree)
        main = target.module
        _add_dependencies(modules, roots, roots_digest, parsed_trees)
        target_record = {"kind": "source", "filename": target.filename, "module": main}
        entries = options.entries
    elif isinstance(target, File):
        path = Path(target.path).expanduser().resolve()
        if not path.is_file():
            raise InputError(f"File not found: {path}")
        root = path.parent
        while (root / "__init__.py").is_file():
            root = root.parent
        roots = (
            [root]
            + (
                [Path(options.project_root).expanduser().resolve()]
                if options.project_root is not None
                else []
            )
            + [Path(p).expanduser().resolve() for p in options.source_roots]
        )
        roots_digest = stable_digest([str(p) for p in roots])
        main = target.module or _module_name(path, root)
        modules[main] = _read(path, main, path.name == "__init__.py", roots_digest, parsed_trees)
        _add_dependencies(modules, roots, roots_digest, parsed_trees)
        target_record = {
            "kind": "file",
            "path": str(path),
            "module": main,
            "source_roots": [str(p) for p in roots],
        }
        entries = options.entries
    else:
        root = Path(target.root).expanduser().resolve()
        if not root.is_dir():
            raise InputError(f"Project not found: {root}")
        if (
            options.project_root is not None
            and Path(options.project_root).expanduser().resolve() != root
        ):
            raise InputError("Conflicting project roots")
        explicit_roots = target.source_roots or tuple(options.source_roots)
        roots = (
            [
                Path(p).resolve() if Path(p).is_absolute() else (root / p).resolve()
                for p in explicit_roots
            ]
            if explicit_roots
            else [root / "src" if (root / "src").is_dir() else root]
        )
        roots_digest = stable_digest([str(p) for p in roots])
        exclusions = tuple(options.exclusions) + tuple(target.exclusions)
        for source_root in roots:
            if not source_root.is_dir():
                raise InputError(f"Source root not found: {source_root}")
            for path in sorted(source_root.rglob("*.py")):
                rel = path.relative_to(source_root)
                if any(ex in rel.parts or fnmatch.fnmatch(str(rel), ex) for ex in exclusions):
                    continue
                name = _module_name(path, source_root)
                if name in modules and modules[name].filename != str(path):
                    raise InputError(
                        f"Duplicate module identity {name}: {modules[name].filename}, {path}"
                    )
                modules[name] = _read(
                    path, name, path.name == "__init__.py", roots_digest, parsed_trees
                )
        if not modules:
            raise InputError("no_entry: project has no analyzable Python modules")
        main = None
        entries = target.entries or tuple(options.entries)
        target_record = {
            "kind": "project",
            "root": str(root),
            "source_roots": [str(p) for p in roots],
        }
    if entries:
        scenarios = []
        for entry in entries:
            if not isinstance(entry, str) or ":" not in entry:
                raise InputError("Entry must be module:qualname")
            name, qualname = entry.split(":", 1)
            if name not in modules:
                raise InputError(f"Entry module absent from inventory: {name}")
            scenarios.append(_entry(modules[name], qualname or None))
    else:
        scenarios = [
            EntryScenario(name) for name in sorted(modules) if main is None or name == main
        ]
    source_digest = stable_digest({name: mod.digest for name, mod in sorted(modules.items())})
    inventory: tuple[JsonObject, ...] = tuple(
        {
            "module": m.module,
            "filename": m.filename,
            "digest": m.digest,
            "original_bytes_digest": m.original_bytes_digest,
            "encoding": m.encoding,
            "is_package": m.is_package,
        }
        for m in sorted(modules.values(), key=lambda x: x.module)
    )
    return SourceSnapshot(
        tuple(sorted(modules.values(), key=lambda x: x.module)),
        tuple(scenarios),
        target_record,
        source_digest,
        inventory,
        parsed_trees,
        input_resolution=input_resolution_contract(target, options),
    )
