"""Deterministic import dependencies and SCC scheduling for modular clients.

Edges point from importer to dependency. A cyclic component must be solved to
fixpoint as a group; flattening its schedule is not an acyclic proof.
"""
from graphlib import CycleError
from importlib.util import resolve_name


class ModuleDependencyGraph:
    def __init__(self):
        self._imports = {}
        self._importers = {}

    def add_module(self, name):
        self._imports.setdefault(name, set())
        self._importers.setdefault(name, set())

    def add_import(self, importer, imported):
        self.add_module(importer)
        self.add_module(imported)
        self._imports[importer].add(imported)
        self._importers[imported].add(importer)

    def get_imports(self, name):
        return frozenset(self._imports.get(name, ()))

    def get_importers(self, name):
        return frozenset(self._importers.get(name, ()))

    def strongly_connected_components(self):
        # Iterative Kosaraju avoids Python recursion limits on real projects.
        seen, order = set(), []
        for root in sorted(self._imports):
            if root in seen:
                continue
            seen.add(root)
            stack = [(root, iter(sorted(self._imports[root])))]
            while stack:
                node, successors = stack[-1]
                child = next(successors, None)
                if child is None:
                    stack.pop()
                    order.append(node)
                elif child not in seen:
                    seen.add(child)
                    stack.append((child, iter(sorted(self._imports[child]))))
        seen, components = set(), []
        for root in reversed(order):
            if root in seen:
                continue
            component, stack = [], [root]
            seen.add(root)
            while stack:
                node = stack.pop()
                component.append(node)
                for parent in sorted(self._importers[node], reverse=True):
                    if parent not in seen:
                        seen.add(parent)
                        stack.append(parent)
            components.append(tuple(sorted(component)))
        return list(reversed(components))  # dependencies before importers

    def detect_cycles(self):
        return [group for group in self.strongly_connected_components()
                if len(group) > 1 or group[0] in self._imports[group[0]]]

    def topological_sort(self):
        cycles = self.detect_cycles()
        if cycles:
            raise CycleError('cyclic module dependencies; schedule SCCs', cycles)
        return [group[0] for group in self.strongly_connected_components()]

    @staticmethod
    def resolve_relative_import(current_module, name, level, *, is_package=False):
        """Resolve without importing; packages explicitly use their own anchor."""
        if type(level) is not int or level < 0:
            raise ValueError('relative import level must be a nonnegative integer')
        if level == 0:
            return name
        package = current_module if is_package else current_module.rpartition('.')[0]
        return resolve_name('.' * level + name, package)
