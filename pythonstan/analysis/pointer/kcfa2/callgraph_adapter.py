"""Call graph adapter for k-CFA pointer analysis.

This module provides a thin wrapper around PythonStAn's call graph infrastructure,
adapting it for use with context-sensitive pointer analysis.

The adapter handles:
- Context-sensitive call graph construction
- Integration with pythonstan.graph.call_graph.CallGraph
- Call target resolution
- Call graph dumping and visualization
"""

from typing import Set, Dict, List, Optional, Tuple, Any
from .context import Context, CallSite
from .config import KCFAConfig

__all__ = ["CallGraphAdapter"]


class CallGraphAdapter:
    """Adapter for PythonStAn call graph in k-CFA analysis.
    
    This class wraps the base CallGraph implementation to provide
    context-sensitive call graph operations needed for k-CFA analysis.
    """
    
    def __init__(self, config: Optional[KCFAConfig] = None):
        """Initialize call graph adapter.
        
        Args:
            config: k-CFA configuration
        """
        self.config = config or KCFAConfig()
        
        # Context-sensitive call graph: (caller_ctx, call_site) -> {(callee_ctx, callee_fn)}
        self._cs_call_graph: Dict[Tuple[Context, CallSite], Set[Tuple[Context, str]]] = {}
        
        # Call site to possible targets mapping
        self._call_targets: Dict[str, Set[str]] = {}
        
        # Function symbol table (function name -> function object)
        self._function_symbols: Dict[str, Any] = {}
        
        # Base call graph (context-insensitive)
        self._base_call_graph: Optional[Any] = None
        
    def add_edge(
        self, 
        caller_ctx: Context, 
        call_site: CallSite, 
        callee_ctx: Context,
        callee_fn: str
    ) -> None:
        """Add a context-sensitive call graph edge.
        
        Args:
            caller_ctx: Context of the calling function
            call_site: Call site information
            callee_ctx: Context of the called function
            callee_fn: Name of the called function
        """
        call_key = (caller_ctx, call_site)
        callee_info = (callee_ctx, callee_fn)
        
        if call_key not in self._cs_call_graph:
            self._cs_call_graph[call_key] = set()
            
        self._cs_call_graph[call_key].add(callee_info)
        
        # Also update context-insensitive mapping
        site_id = call_site.site_id
        if site_id not in self._call_targets:
            self._call_targets[site_id] = set()
        self._call_targets[site_id].add(callee_fn)
        
        if self.config.verbose:
            print(f"Added call edge: {caller_ctx} --[{call_site}]--> {callee_ctx}:{callee_fn}")
            
    def resolve_targets(self, call_site: CallSite) -> Set[str]:
        """Resolve possible call targets for a call site.
        
        Args:
            call_site: Call site to resolve
            
        Returns:
            Set of possible target function names
            
        Notes:
            This method provides context-insensitive target resolution.
            For context-sensitive resolution, use resolve_cs_targets.
        """
        site_id = call_site.site_id
        targets = self._call_targets.get(site_id, set())
        
        if not targets:
            # Try to resolve using base call graph or heuristics
            targets = self._resolve_static_targets(call_site)
            
        return targets
        
    def resolve_cs_targets(
        self, 
        caller_ctx: Context, 
        call_site: CallSite
    ) -> Set[Tuple[Context, str]]:
        """Resolve context-sensitive call targets.
        
        Args:
            caller_ctx: Context of the calling function
            call_site: Call site to resolve
            
        Returns:
            Set of (callee_context, callee_function) pairs
        """
        call_key = (caller_ctx, call_site)
        return self._cs_call_graph.get(call_key, set())
        
    def get_callers(self, function_name: str) -> Set[Tuple[Context, CallSite]]:
        """Get all callers of a function.
        
        Args:
            function_name: Name of the called function
            
        Returns:
            Set of (caller_context, call_site) pairs that call this function
        """
        callers = set()
        
        for (caller_ctx, call_site), targets in self._cs_call_graph.items():
            for callee_ctx, callee_fn in targets:
                if callee_fn == function_name:
                    callers.add((caller_ctx, call_site))
                    
        return callers
        
    def get_callees(self, caller_ctx: Context) -> Set[Tuple[CallSite, Context, str]]:
        """Get all callees from a context.
        
        Args:
            caller_ctx: Caller context
            
        Returns:
            Set of (call_site, callee_context, callee_function) tuples
        """
        callees = set()
        
        for (ctx, call_site), targets in self._cs_call_graph.items():
            if ctx == caller_ctx:
                for callee_ctx, callee_fn in targets:
                    callees.add((call_site, callee_ctx, callee_fn))
                    
        return callees
        
    def register_function(self, name: str, function_obj: Any) -> None:
        """Register a function in the symbol table.
        
        Args:
            name: Function name
            function_obj: Function object/IR representation
        """
        self._function_symbols[name] = function_obj
        
    def get_function(self, name: str) -> Optional[Any]:
        """Get function object by name.
        
        Args:
            name: Function name
            
        Returns:
            Function object or None if not found
        """
        return self._function_symbols.get(name)
        
    def set_base_call_graph(self, call_graph: Any) -> None:
        """Set the base (context-insensitive) call graph.
        
        Args:
            call_graph: PythonStAn CallGraph instance
        """
        self._base_call_graph = call_graph
        
    def dump(self, format: str = "text") -> str:
        """Dump the call graph in the specified format.
        
        Args:
            format: Output format ("text", "dot", "json")
            
        Returns:
            String representation of the call graph
        """
        if format == "text":
            return self._dump_text()
        elif format == "dot":
            return self._dump_dot()
        elif format == "json":
            return self._dump_json()
        else:
            raise ValueError(f"Unsupported format: {format}")
            
    def get_statistics(self) -> Dict[str, Any]:
        """Get call graph statistics.
        
        Returns:
            Dictionary with call graph statistics
        """
        total_edges = sum(len(targets) for targets in self._cs_call_graph.values())
        unique_call_sites = len(self._cs_call_graph)
        unique_functions = len(set(
            callee_fn for targets in self._cs_call_graph.values()
            for _, callee_fn in targets
        ))
        
        return {
            "total_cs_edges": total_edges,
            "unique_call_sites": unique_call_sites,
            "unique_functions": unique_functions,
            "contexts_with_calls": len(set(
                caller_ctx for caller_ctx, _ in self._cs_call_graph.keys()
            )),
            "registered_functions": len(self._function_symbols)
        }
        
    # Private helper methods
    
    def _resolve_static_targets(self, call_site: CallSite) -> Set[str]:
        """Resolve call targets using static analysis.
        
        Args:
            call_site: Call site to resolve
            
        Returns:
            Set of possible target function names
        """
        # TODO: Implement static target resolution
        # This could use:
        # - Class hierarchy information
        # - Direct function name resolution
        # - Method resolution order
        # - Conservative approximations for dynamic calls
        
        if self._base_call_graph:
            # Use base call graph if available
            # TODO: Integrate with pythonstan.graph.call_graph.CallGraph
            pass
            
        # Conservative fallback: return empty set
        # Real implementation should provide reasonable defaults
        return set()
        
    def _dump_text(self) -> str:
        """Dump call graph as text.
        
        Returns:
            Text representation of the call graph
        """
        lines = ["Context-Sensitive Call Graph:", ""]
        
        for (caller_ctx, call_site), targets in sorted(self._cs_call_graph.items(), key=str):
            caller_line = f"{caller_ctx} @ {call_site}:"
            lines.append(caller_line)
            
            for callee_ctx, callee_fn in sorted(targets, key=str):
                target_line = f"  -> {callee_ctx}:{callee_fn}"
                lines.append(target_line)
                
            lines.append("")
            
        return "\n".join(lines)
        
    def _dump_dot(self) -> str:
        """Dump call graph as DOT format.
        
        Returns:
            DOT representation of the call graph
        """
        lines = ["digraph CallGraph {"]
        lines.append("  rankdir=TB;")
        lines.append("  node [shape=box];")
        lines.append("")
        
        # Add nodes
        nodes = set()
        for (caller_ctx, call_site), targets in self._cs_call_graph.items():
            caller_node = f"{caller_ctx.call_string[-1].fn if caller_ctx.call_string else 'main'}@{caller_ctx}"
            nodes.add(caller_node)
            
            for callee_ctx, callee_fn in targets:
                callee_node = f"{callee_fn}@{callee_ctx}"
                nodes.add(callee_node)
                
        for node in sorted(nodes):
            safe_node = node.replace('"', '\\"')
            lines.append(f'  "{safe_node}";')
            
        lines.append("")
        
        # Add edges
        for (caller_ctx, call_site), targets in self._cs_call_graph.items():
            caller_node = f"{caller_ctx.call_string[-1].fn if caller_ctx.call_string else 'main'}@{caller_ctx}"
            
            for callee_ctx, callee_fn in targets:
                callee_node = f"{callee_fn}@{callee_ctx}"
                safe_caller = caller_node.replace('"', '\\"')
                safe_callee = callee_node.replace('"', '\\"')
                lines.append(f'  "{safe_caller}" -> "{safe_callee}";')
                
        lines.append("}")
        return "\n".join(lines)
        
    def _dump_json(self) -> str:
        """Dump call graph as JSON.
        
        Returns:
            JSON representation of the call graph
        """
        import json
        
        # Convert to JSON-serializable format
        json_data = {
            "nodes": {},
            "edges": []
        }
        
        # Add nodes
        node_id = 0
        node_map = {}
        
        for (caller_ctx, call_site), targets in self._cs_call_graph.items():
            caller_key = f"{caller_ctx}@{call_site.fn}"
            if caller_key not in node_map:
                node_map[caller_key] = node_id
                json_data["nodes"][str(node_id)] = {
                    "context": str(caller_ctx),
                    "function": call_site.fn,
                    "type": "caller"
                }
                node_id += 1
                
            for callee_ctx, callee_fn in targets:
                callee_key = f"{callee_ctx}@{callee_fn}"
                if callee_key not in node_map:
                    node_map[callee_key] = node_id
                    json_data["nodes"][str(node_id)] = {
                        "context": str(callee_ctx),
                        "function": callee_fn,
                        "type": "callee"
                    }
                    node_id += 1
                    
        # Add edges
        for (caller_ctx, call_site), targets in self._cs_call_graph.items():
            caller_key = f"{caller_ctx}@{call_site.fn}"
            caller_id = node_map[caller_key]
            
            for callee_ctx, callee_fn in targets:
                callee_key = f"{callee_ctx}@{callee_fn}"
                callee_id = node_map[callee_key]
                
                json_data["edges"].append({
                    "source": caller_id,
                    "target": callee_id,
                    "call_site": str(call_site)
                })
                
        return json.dumps(json_data, indent=2)