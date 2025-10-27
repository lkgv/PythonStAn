"""Module dependency graph for ordered analysis.

Tracks import dependencies between modules and provides topological
ordering for bottom-up modular analysis.
"""

from typing import Dict, Set, List
from collections import defaultdict, deque
import logging

logger = logging.getLogger(__name__)

__all__ = ["ModuleDependencyGraph"]


class ModuleDependencyGraph:
    """Tracks module dependencies for ordered analysis.
    
    Maintains directed graph where edge (A, B) means A imports B.
    Provides topological ordering to analyze dependencies before dependents.
    """
    
    def __init__(self):
        self._edges: Dict[str, Set[str]] = defaultdict(set)
        self._reverse: Dict[str, Set[str]] = defaultdict(set)
    
    def add_import(self, importer: str, importee: str) -> None:
        self._edges[importer].add(importee)
        self._reverse[importee].add(importer)
    
    def get_imports(self, module: str) -> Set[str]:
        return self._edges.get(module, set())
    
    def get_importers(self, module: str) -> Set[str]:
        return self._reverse.get(module, set())
    
    def topological_sort(self) -> List[str]:
        all_nodes = set(self._edges.keys()) | set(self._reverse.keys())
        in_degree = {node: 0 for node in all_nodes}
        
        for node in all_nodes:
            for importer in self._reverse.get(node, set()):
                in_degree[importer] = in_degree.get(importer, 0) + 1
        
        queue = deque(sorted([node for node, deg in in_degree.items() if deg == 0]))
        result = []
        
        while queue:
            node = queue.popleft()
            result.append(node)
            
            importers = sorted(self._reverse.get(node, set()))
            for importer in importers:
                in_degree[importer] -= 1
                if in_degree[importer] == 0:
                    queue.append(importer)
        
        if len(result) != len(all_nodes):
            remaining = [n for n in all_nodes if n not in result]
            logger.warning(f"Topological sort incomplete. Cycles involving: {remaining}")
            result.extend(sorted(remaining))
        
        return result
    
    def detect_cycles(self) -> List[List[str]]:
        """Detect circular import chains using Tarjan's algorithm.
        
        Returns:
            List of cycles, where each cycle is a list of module names
        """
        all_nodes = set(self._edges.keys()) | set(self._reverse.keys())
        
        index_counter = [0]
        stack: List[str] = []
        lowlinks: Dict[str, int] = {}
        index: Dict[str, int] = {}
        on_stack: Dict[str, bool] = defaultdict(bool)
        cycles: List[List[str]] = []
        
        def strongconnect(node: str):
            index[node] = index_counter[0]
            lowlinks[node] = index_counter[0]
            index_counter[0] += 1
            stack.append(node)
            on_stack[node] = True
            
            for neighbor in self._edges.get(node, set()):
                if neighbor not in index:
                    strongconnect(neighbor)
                    lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
                elif on_stack[neighbor]:
                    lowlinks[node] = min(lowlinks[node], index[neighbor])
            
            if lowlinks[node] == index[node]:
                component = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    component.append(w)
                    if w == node:
                        break
                
                if len(component) > 1:
                    cycles.append(component)
        
        for node in all_nodes:
            if node not in index:
                strongconnect(node)
        
        return cycles
    
    def resolve_relative_import(
        self,
        current_module: str,
        import_name: str,
        level: int
    ) -> str:
        parts = current_module.split('.')
        
        parts = parts[:-1] if parts else []
        
        for _ in range(level - 1):
            if parts:
                parts.pop()
        
        if import_name:
            parts.append(import_name)
        
        return '.'.join(parts) if parts else import_name
    
    def get_all_modules(self) -> Set[str]:
        return set(self._edges.keys()) | set(self._reverse.keys())

