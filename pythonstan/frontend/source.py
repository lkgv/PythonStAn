"""Session-owned, nonexecuting Python 3.12 source frontend.

ASTs are implementation-owned IR and never executed. Source/byte/module/root
identities remain on SourceModule; public results contain frozen JSON only.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import EllipsisType, MappingProxyType
from typing import override

from pythonstan.analysis.contracts.identity import ProgramPointId, SourceModule
from pythonstan.analysis.contracts.json_data import (
    JsonValue,
    require_bool,
    require_string,
    validate_json_object,
)

from .private_names import lower_private_names

# Marker instances are shared by CPython parser calls. They are not independent
# program points, because only their enclosing expressions carry source spans.
_LOCATIONLESS_MARKERS = (ast.expr_context, ast.operator, ast.unaryop, ast.boolop, ast.cmpop)

type SourceInput = SourceModule | Mapping[str, JsonValue]
type ParsedTreeCache = Mapping[str, tuple[str, ast.Module]]
type SourceLiteral = (
    None
    | bool
    | int
    | float
    | complex
    | str
    | bytes
    | EllipsisType
    | tuple[SourceLiteral, ...]
    | frozenset[SourceLiteral]
)


def _checked_source_literal(value: object) -> SourceLiteral:
    if value is None or isinstance(value, (bool, int, float, complex, str, bytes, EllipsisType)):
        return value
    if isinstance(value, tuple):
        return tuple(_checked_source_literal(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(_checked_source_literal(item) for item in value)
    raise TypeError("AST constant contains an unsupported payload")


def source_constant(node: ast.Constant) -> SourceLiteral:
    """Validate CPython's dynamically typed Constant field at the real AST boundary."""
    value: object = node.value
    return _checked_source_literal(value)


def make_source_constant(value: SourceLiteral) -> ast.Constant:
    """Build a Constant with CPython's validated recursive literal payload.

    The host AST API admits tuple and frozenset constants although the stub's
    constructor annotation lists only scalar payloads. Keep that dynamic AST
    boundary here; all callers pass the recursively checked SourceLiteral.
    """
    constant = ast.Constant(value=None)
    ast.AST.__setattr__(constant, "value", value)
    return constant


def _location(node: ast.AST) -> tuple[int, int] | None:
    """Validate the CPython AST's dynamic source-position metadata at its boundary."""
    attributes = vars(node)
    line: object = attributes.get("lineno")
    if line is None:
        return None
    column: object = attributes.get("col_offset", 0)
    if not isinstance(line, int) or not isinstance(column, int):
        raise TypeError("AST source positions must be integers")
    return line, column


def source_position(node: ast.AST) -> tuple[int, int]:
    """Return validated coordinates or the generated-node origin."""
    return _location(node) or (0, 0)


def source_node_identity(module: str, digest: str, node: ast.AST, path: str = "") -> ProgramPointId:
    """Keep source selectors stable across engine and planner parsing."""
    location = _location(node)
    if location is not None:
        line, column = location
        return ProgramPointId(f"{module}:{digest[:16]}:{line}:{column}:{type(node).__name__}")
    if path:
        return ProgramPointId(f"{module}:{digest[:16]}:synthetic:{path}:{type(node).__name__}")
    return ProgramPointId(f"{module}:{digest[:16]}:0:0:{type(node).__name__}")


@dataclass(frozen=True)
class SourceProgram:
    modules: Mapping[str, SourceModule]
    trees: Mapping[str, ast.Module]
    nodes: Mapping[int, ProgramPointId]
    node_modules: Mapping[int, str]


class _SourcePointIndex(Mapping[int, ProgramPointId]):
    """Read-only point mapping with session-local, demand-driven formatting."""

    __slots__ = ("_nodes", "_modules", "_sources", "_cache")

    def __init__(
        self,
        nodes: dict[int, ast.AST],
        modules: Mapping[int, str],
        sources: Mapping[str, SourceModule],
        synthetic: dict[int, ProgramPointId],
    ) -> None:
        # The private frontend transfers its builder dictionary here; no other
        # writable reference leaves parse_sources, so a full AST-map copy is unnecessary.
        self._nodes: Mapping[int, ast.AST] = MappingProxyType(nodes)
        self._modules: Mapping[int, str] = modules
        self._sources: Mapping[str, SourceModule] = sources
        self._cache: dict[int, ProgramPointId] = synthetic

    @override
    def __len__(self) -> int:
        return len(self._nodes)

    @override
    def __iter__(self) -> Iterator[int]:
        return iter(self._nodes)

    @override
    def __getitem__(self, key: int) -> ProgramPointId:
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        node = self._nodes[key]
        module = self._modules[key]
        identifier = source_node_identity(module, self._sources[module].digest, node)
        self._cache[key] = identifier
        return identifier


def _index_tree(
    name: str,
    source: SourceModule,
    tree: ast.Module,
    nodes: dict[int, ast.AST],
    node_modules: dict[int, str],
    synthetic: dict[int, ProgramPointId],
) -> None:
    """Index owned ASTs iteratively without constructing unused whole-tree paths."""
    pending: list[tuple[ast.AST, str]] = [(tree, "module")]
    while pending:
        node, path = pending.pop()
        identifier: ProgramPointId | None = None
        nodes[id(node)] = node
        node_modules[id(node)] = name
        if _location(node) is None:
            identifier = source_node_identity(name, source.digest, node, path)
            synthetic[id(node)] = identifier
        for field, raw_value in ast.iter_fields(node):
            # CPython exposes dynamic AST fields; immediately bound their scope
            # to validated AST/list alternatives before any typed consumer.
            value: object = raw_value
            if isinstance(value, ast.AST):
                if isinstance(value, _LOCATIONLESS_MARKERS):
                    continue
                if _location(value) is not None:
                    child_path = ""
                else:
                    if identifier is None:
                        identifier = source_node_identity(name, source.digest, node)
                    child_path = f"{identifier}.{field}"
                pending.append((value, child_path))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    if isinstance(child, ast.AST) and not isinstance(child, _LOCATIONLESS_MARKERS):
                        if _location(child) is not None:
                            child_path = ""
                        else:
                            if identifier is None:
                                identifier = source_node_identity(name, source.digest, node)
                            child_path = f"{identifier}.{field}[{index}]"
                        pending.append((child, child_path))


def _optional_string(value: JsonValue) -> str | None:
    return None if value is None else require_string(value)


def _source_record(item: SourceInput) -> SourceModule:
    if isinstance(item, SourceModule):
        return item
    data = validate_json_object(item)
    module = require_string(data["module"])
    return SourceModule(
        module=module,
        filename=require_string(data.get("filename", module + ".py")),
        source=require_string(data.get("source", data.get("code", ""))),
        digest=require_string(data.get("digest", "")),
        is_package=require_bool(data.get("is_package", False)),
        original_bytes_digest=_optional_string(data.get("original_bytes_digest")),
        encoding=require_string(data.get("encoding", "utf-8")),
        import_root_digest=require_string(data.get("import_root_digest", "")),
    )


def _cached_tree(cached: object, source: SourceModule) -> ast.Module:
    if isinstance(cached, tuple) and len(cached) == 2:
        digest: object = cached[0]
        tree: object = cached[1]
        if digest == source.digest and isinstance(tree, ast.Module):
            return tree
    raise ValueError(f"source parser cache identity mismatch: {source.module}")


def normalize_source_modules(
    modules: Mapping[str, SourceInput] | Iterable[SourceInput],
) -> Mapping[str, SourceModule]:
    """Validate source records once without parsing or executing their text.

    This is the same identity boundary used by frontend preparation and by an
    engine accepting an already-prepared program. Duplicate checks occur in
    input order, before any source is parsed.
    """
    sources: dict[str, SourceModule] = {}
    iterable = modules.values() if isinstance(modules, Mapping) else modules
    for item in iterable:
        source = _source_record(item)
        if source.module in sources:
            raise ValueError(f"duplicate source module identity: {source.module}")
        sources[source.module] = source
    return MappingProxyType(sources)


def parse_sources(
    modules: Mapping[str, SourceInput] | Iterable[SourceInput],
    *,
    parsed_trees: ParsedTreeCache | None = None,
) -> SourceProgram:
    """Create one execution's validated program without executing target source.

    Cache rows are internal input-normalizer records bound to decoded source
    digests. They are reused only within their owning execution. A rerun supplies
    an empty cache, so private-name lowering never shares ASTs between sessions.
    """
    sources = normalize_source_modules(modules)
    trees: dict[str, ast.Module] = {}
    nodes: dict[int, ast.AST] = {}
    node_modules: dict[int, str] = {}
    synthetic: dict[int, ProgramPointId] = {}
    for name, source in sources.items():
        cached = parsed_trees.get(name) if parsed_trees is not None else None
        tree = (
            ast.parse(source.source, source.filename, type_comments=True, feature_version=(3, 12))
            if cached is None
            else _cached_tree(cached, source)
        )
        # Compilation checks symbol/placement rules accepted by ast.parse.
        # The resulting code object is immediately discarded, never executed.
        compile(tree, source.filename, "exec", dont_inherit=True)
        tree = lower_private_names(tree)
        trees[name] = tree
        _index_tree(name, source, tree, nodes, node_modules, synthetic)
    frozen_sources = sources
    frozen_ownership = MappingProxyType(node_modules)
    return SourceProgram(
        frozen_sources,
        MappingProxyType(trees),
        _SourcePointIndex(nodes, frozen_ownership, frozen_sources, synthetic),
        frozen_ownership,
    )
