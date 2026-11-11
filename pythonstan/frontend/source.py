"""Session-owned, nonexecuting Python 3.12 source frontend.

ASTs are implementation-owned IR and never executed. Source/byte/module/root
identities remain on SourceModule; public results contain frozen JSON only.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pythonstan.analysis.contracts import SourceModule


# CPython shares these marker objects across nodes and even parser calls. They
# carry neither an operation's location nor children: the enclosing expression
# is the program point. Recording their object IDs would assign one module's
# source identity to another module's operator/context marker.
_LOCATIONLESS_MARKERS = (ast.expr_context, ast.operator, ast.unaryop, ast.boolop, ast.cmpop)


def source_node_identity(module: str, digest: str, node: ast.AST, path: str = "") -> str:
    """Keep source selectors stable across engine and planner parsing.

    The complete source digest is validated alongside this compact node ID.
    Nodes lacking a source span use a structural path instead of object repr.
    """
    if hasattr(node, "lineno"):
        return f"{module}:{digest[:16]}:{node.lineno}:{getattr(node, 'col_offset', 0)}:{type(node).__name__}"
    if path:
        return f"{module}:{digest[:16]}:synthetic:{path}:{type(node).__name__}"
    return f"{module}:{digest[:16]}:0:0:{type(node).__name__}"


@dataclass(frozen=True)
class SourceProgram:
    modules: Mapping[str, SourceModule]
    trees: Mapping[str, ast.Module]
    nodes: Mapping[int, str]
    node_modules: Mapping[int, str]


class _SourcePointIndex(Mapping):
    """Read-only point mapping with session-local, demand-driven formatting.

    Large source inputs need not allocate a long identity string for every
    node before analysis starts. AST ownership is retained by SourceProgram;
    only points actually requested by the solver are memoized here.
    """

    __slots__ = ("_nodes", "_modules", "_sources", "_cache")

    def __init__(self, nodes, modules, sources, synthetic):
        self._nodes = MappingProxyType(nodes)
        self._modules = modules
        self._sources = sources
        self._cache = synthetic

    def __len__(self):
        return len(self._nodes)

    def __iter__(self):
        return iter(self._nodes)

    def __getitem__(self, key):
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        node = self._nodes[key]
        module = self._modules[key]
        identifier = source_node_identity(module, self._sources[module].digest, node)
        self._cache[key] = identifier
        return identifier


def _index_tree(name, source, tree, nodes, node_modules, synthetic):
    """Index source points without constructing unused whole-tree paths.

    Located AST nodes retain the public span identity exactly. The few
    structural nodes without spans use a path rooted at their nearest located
    ancestor. A stack avoids Python recursion limits for long expressions.
    """
    pending = [(tree, "module")]
    while pending:
        node, path = pending.pop()
        identifier = None
        nodes[id(node)] = node
        node_modules[id(node)] = name
        if not hasattr(node, "lineno"):
            identifier = source_node_identity(name, source.digest, node, path)
            synthetic[id(node)] = identifier
        for field, value in ast.iter_fields(node):
            if isinstance(value, ast.AST):
                if isinstance(value, _LOCATIONLESS_MARKERS):
                    continue
                # Almost all semantic AST nodes have spans. Do not build a
                # path string that source_node_identity will discard.
                if hasattr(value, "lineno"):
                    child_path = ""
                else:
                    if identifier is None:
                        identifier = source_node_identity(name, source.digest, node)
                    child_path = f"{identifier}.{field}"
                pending.append((value, child_path))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    if isinstance(child, ast.AST) and not isinstance(child, _LOCATIONLESS_MARKERS):
                        if hasattr(child, "lineno"):
                            child_path = ""
                        else:
                            if identifier is None:
                                identifier = source_node_identity(name, source.digest, node)
                            child_path = f"{identifier}.{field}[{index}]"
                        pending.append((child, child_path))


def parse_sources(modules, *, parsed_trees=None) -> SourceProgram:
    """Create one session's validated source program.

    ``parsed_trees`` is the input normalizer's internal parser cache, mapping
    module names to ``(decoded_source_digest, ast.Module)``. It is not a public
    configuration input. Cache rows must belong to the exact decoded source;
    compilation still validates symbol/statement placement for reused trees.
    No tree or cache is shared across sessions.
    """
    sources = {}
    iterable = modules.values() if isinstance(modules, Mapping) else modules
    for item in iterable:
        if isinstance(item, SourceModule):
            source = item
        elif isinstance(item, Mapping):
            source = SourceModule(
                item["module"], item.get("filename", item["module"] + ".py"),
                item.get("source", item.get("code", "")), item.get("digest", ""),
                item.get("is_package", False), item.get("original_bytes_digest"),
                item.get("encoding", "utf-8"), item.get("import_root_digest", ""),
            )
        else:
            raise TypeError("source frontend requires SourceModule snapshots")
        if source.module in sources:
            raise ValueError(f"duplicate source module identity: {source.module}")
        sources[source.module] = source
    trees, nodes, node_modules, synthetic = {}, {}, {}, {}
    for name, source in sources.items():
        cached = parsed_trees.get(name) if parsed_trees is not None else None
        if cached is None:
            tree = ast.parse(source.source, source.filename, type_comments=True, feature_version=(3, 12))
        else:
            if (not isinstance(cached, tuple) or len(cached) != 2 or
                    cached[0] != source.digest or not isinstance(cached[1], ast.Module)):
                raise ValueError(f"source parser cache identity mismatch: {name}")
            tree = cached[1]
        # Compilation checks Python's symbol/placement rules that ast.parse
        # alone accepts (e.g. return outside a function). This code object is
        # immediately discarded and is never executed.
        compile(tree, source.filename, "exec", dont_inherit=True)
        from .private_names import lower_private_names
        tree=lower_private_names(tree)
        trees[name] = tree
        _index_tree(name, source, tree, nodes, node_modules, synthetic)
    frozen_sources = MappingProxyType(sources)
    frozen_ownership = MappingProxyType(node_modules)
    return SourceProgram(frozen_sources, MappingProxyType(trees),
                         _SourcePointIndex(nodes, frozen_ownership, frozen_sources, synthetic),
                         frozen_ownership)
