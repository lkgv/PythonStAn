"""Static source inventory, import identities, and independent entry scenarios."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import ast
import fnmatch
import hashlib
import io
import json
import tokenize

from pythonstan.analysis.contracts.identity import EntryScenario, SourceModule, stable_digest
from pythonstan.analysis.contracts.wire import thaw_snapshot
from .targets import Source, File, Project


class InputError(ValueError):
    pass


@dataclass(frozen=True)
class SourceSnapshot:
    modules: tuple[SourceModule, ...]
    scenarios: tuple[EntryScenario, ...]
    target: dict
    source_digest: str
    inventory: tuple[dict, ...]
    parsed_trees: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def module_map(self): return {m.module: m for m in self.modules}
    @property
    def entry_contract(self):
        return {"entry_coverage": "initialization_only" if all(s.entry_qualname is None for s in self.scenarios) else "declared_entries",
                "scenarios": [{"id": s.scenario_id, "module": s.root_module,
                               "qualname": s.entry_qualname, "arguments": thaw_snapshot(s.arguments),
                               "coverage": s.coverage} for s in self.scenarios]}


def _read(path, module, is_package, roots_digest, parsed_trees=None):
    try:
        raw = path.read_bytes()
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        source = raw.decode(encoding)
        tree = ast.parse(source, filename=str(path), type_comments=True, feature_version=(3, 12))
        result = SourceModule(module, str(path), source, is_package=is_package,
                            original_bytes_digest=hashlib.sha256(raw).hexdigest(),
                            encoding=encoding, import_root_digest=roots_digest)
        if parsed_trees is not None: parsed_trees[module] = (result.digest, tree)
        return result
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise InputError(f"{path}: {exc}") from exc


def _module_name(path, root):
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__": parts.pop()
    return ".".join(parts) if parts else root.name


def _imports(module):
    tree = ast.parse(module.source)
    package = module.module if module.is_package else module.module.rpartition(".")[0]
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".") if package else []
                trim = node.level - 1
                base = ".".join(parts[:len(parts)-trim] if trim else parts)
                name = ".".join(x for x in (base, node.module) if x)
            else: name = node.module or ""
            if name: result.add(name)
            for alias in node.names:
                if alias.name != "*": result.add(".".join(x for x in (name, alias.name) if x))
    return result


def _add_dependencies(modules, roots, roots_digest, parsed_trees=None):
    pending = list(modules.values())
    while pending:
        module = pending.pop()
        for name in sorted(_imports(module)):
            pieces = name.split(".")
            for size in range(1, len(pieces) + 1):
                candidate = ".".join(pieces[:size])
                if candidate in modules: continue
                found = None
                for root in roots:
                    stem = root.joinpath(*pieces[:size])
                    for path, package in ((stem / "__init__.py", True), (stem.with_suffix(".py"), False)):
                        if path.is_file():
                            found = _read(path, candidate, package, roots_digest, parsed_trees); break
                    if found: break
                if found:
                    modules[candidate] = found; pending.append(found)


def _entry(module, qualname):
    if qualname is None: return EntryScenario(module.module)
    parts = qualname.split(".")
    nodes = ast.parse(module.source).body
    found = None
    for part in parts:
        found = next((n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == part), None)
        if found is None: raise InputError(f"Unresolved entry: {module.module}:{qualname}")
        nodes = getattr(found, "body", [])
    if not isinstance(found, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise InputError(f"Entry is not a source function: {module.module}:{qualname}")
    required = len(found.args.posonlyargs) + len(found.args.args) - len(found.args.defaults)
    arguments = tuple({"top": True, "input_contract": "arbitrary_python_value"} for _ in range(required))
    arguments += tuple({"keyword": arg.arg, "abstract_value": {"top": True, "input_contract": "arbitrary_python_value"}}
                       for arg, default in zip(found.args.kwonlyargs, found.args.kw_defaults) if default is None)
    return EntryScenario(module.module, qualname, arguments=arguments, coverage="declared_abstract_inputs")


def snapshot_target(target, config):
    if not isinstance(target, (Source, File, Project)):
        raise TypeError("target must be Source, File, or Project")
    config = config.to_dict() if hasattr(config, "to_dict") else config
    options = config["inputs"]
    modules = {}
    parsed_trees = {}
    roots = []
    if isinstance(target, Source):
        roots = [Path(p).expanduser().resolve() for p in target.import_roots]
        if target.project_root is not None: roots.append(Path(target.project_root).expanduser().resolve())
        roots += [Path(p).expanduser().resolve() for p in options["source_roots"]]
        try: tree = ast.parse(target.code, filename=target.filename, type_comments=True, feature_version=(3, 12))
        except SyntaxError as exc: raise InputError(str(exc)) from exc
        roots_digest = stable_digest([str(p) for p in roots])
        modules[target.module] = SourceModule(target.module, target.filename, target.code,
                                               is_package=Path(target.filename).name == "__init__.py",
                                               import_root_digest=roots_digest)
        parsed_trees[target.module] = (modules[target.module].digest, tree)
        main = target.module
        _add_dependencies(modules, roots, roots_digest, parsed_trees)
        target_record = {"kind": "source", "filename": target.filename, "module": main}
        entries = options["entries"]
    elif isinstance(target, File):
        path = Path(target.path).expanduser().resolve()
        if not path.is_file(): raise InputError(f"File not found: {path}")
        root = path.parent
        while (root / "__init__.py").is_file(): root = root.parent
        roots = [root] + [Path(p).expanduser().resolve() for p in options["source_roots"]]
        roots_digest = stable_digest([str(p) for p in roots])
        main = target.module or _module_name(path, root)
        modules[main] = _read(path, main, path.name == "__init__.py", roots_digest, parsed_trees)
        _add_dependencies(modules, roots, roots_digest, parsed_trees)
        target_record = {"kind": "file", "path": str(path), "module": main, "source_roots": [str(p) for p in roots]}
        entries = options["entries"]
    else:
        root = Path(target.root).expanduser().resolve()
        if not root.is_dir(): raise InputError(f"Project not found: {root}")
        explicit_roots = target.source_roots or tuple(options["source_roots"])
        roots = [Path(p).resolve() if Path(p).is_absolute() else (root / p).resolve() for p in explicit_roots] if explicit_roots else [root / "src" if (root / "src").is_dir() else root]
        roots_digest = stable_digest([str(p) for p in roots])
        exclusions = tuple(options["exclusions"]) + tuple(target.exclusions)
        for source_root in roots:
            if not source_root.is_dir(): raise InputError(f"Source root not found: {source_root}")
            for path in sorted(source_root.rglob("*.py")):
                rel = path.relative_to(source_root)
                if any(ex in rel.parts or fnmatch.fnmatch(str(rel), ex) for ex in exclusions): continue
                name = _module_name(path, source_root)
                if name in modules and modules[name].filename != str(path):
                    raise InputError(f"Duplicate module identity {name}: {modules[name].filename}, {path}")
                modules[name] = _read(path, name, path.name == "__init__.py", roots_digest, parsed_trees)
        if not modules: raise InputError("no_entry: project has no analyzable Python modules")
        main = None
        entries = target.entries or tuple(options["entries"])
        target_record = {"kind": "project", "root": str(root), "source_roots": [str(p) for p in roots]}
    if entries:
        scenarios = []
        for entry in entries:
            if not isinstance(entry, str) or ":" not in entry: raise InputError("Entry must be module:qualname")
            name, qualname = entry.split(":", 1)
            if name not in modules: raise InputError(f"Entry module absent from inventory: {name}")
            scenarios.append(_entry(modules[name], qualname or None))
    else:
        scenarios = [EntryScenario(name) for name in sorted(modules) if main is None or name == main]
    source_digest = stable_digest({name: mod.digest for name, mod in sorted(modules.items())})
    inventory = tuple({"module": m.module, "filename": m.filename, "digest": m.digest,
                       "original_bytes_digest": m.original_bytes_digest, "encoding": m.encoding,
                       "is_package": m.is_package} for m in sorted(modules.values(), key=lambda x: x.module))
    return SourceSnapshot(tuple(sorted(modules.values(), key=lambda x: x.module)), tuple(scenarios), target_record, source_digest, inventory, parsed_trees)
