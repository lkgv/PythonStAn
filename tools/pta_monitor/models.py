"""Data models for PTA Monitor UI.

Simple dataclasses and types for passing data between backend and frontend.
Text-first approach: prefer str() and raw dict/list dumps.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Set
from enum import Enum


class DeltaEventType(Enum):
    """Types of events tracked for delta computation."""
    POINTS_TO_UPDATE = "points_to_update"
    CALL_EDGE_CREATED = "call_edge_created"
    CALL_FAILED = "call_failed"
    CONSTRAINT_APPLIED = "constraint_applied"
    PFG_EDGE_ACTIVATED = "pfg_edge_activated"
    SCOPE_ANALYZED = "scope_analyzed"
    OBJECT_ALLOCATED = "object_allocated"


@dataclass
class DeltaEvent:
    """A single delta event."""
    event_type: DeltaEventType
    iteration: int
    data: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'type': self.event_type.value,
            'iteration': self.iteration,
            'data': self.data
        }


@dataclass
class PointsToInfo:
    """Points-to information for a variable."""
    variable_str: str
    size: int
    objects: List[Dict[str, Any]]
    
    def to_text(self) -> str:
        lines = [
            f"Variable: {self.variable_str}",
            f"Points-to size: {self.size}",
            "Objects:"
        ]
        for obj in self.objects:
            lines.append(f"  - {obj}")
        return "\n".join(lines)


@dataclass
class CallSiteInfo:
    """Information about a call site."""
    call_site_str: str
    callee_var: str
    callee_pts_size: int
    resolved_edges: List[Dict[str, Any]]
    failures: List[Dict[str, Any]]
    
    def to_text(self) -> str:
        lines = [
            f"Call site: {self.call_site_str}",
            f"Callee variable: {self.callee_var}",
            f"Callee points-to size: {self.callee_pts_size}",
            "",
            f"Resolved edges ({len(self.resolved_edges)}):"
        ]
        for edge in self.resolved_edges:
            lines.append(f"  - {edge}")
        lines.append("")
        lines.append(f"Failures ({len(self.failures)}):")
        for fail in self.failures:
            lines.append(f"  - {fail}")
        return "\n".join(lines)


@dataclass
class IRStatement:
    """IR statement for display."""
    index: int
    text: str
    is_call: bool = False
    call_site_id: Optional[str] = None
    scope_qualname: Optional[str] = None
    line_number: Optional[int] = None
    block_id: Optional[int] = None  # Basic block index
    indent_level: int = 0  # Indentation level for nested scopes


@dataclass
class IRBlockInfo:
    """Information about a basic block for display."""
    block_id: int
    label: str  # e.g., "bb0", "bb1"
    statements: List['IRStatement']
    scope_qualname: str
    indent_level: int = 0


@dataclass
class ModuleInfo:
    """Information about a module for the UI."""
    qualname: str
    filename: str
    display_name: str
    source_code: str
    ir_statements: List[IRStatement]  # Flat list for backward compatibility
    ir_text: str = ""  # Block-formatted IR text for Monaco editor
    functions: List[str] = field(default_factory=list)
    classes: List[str] = field(default_factory=list)
    # Mapping from source line number to IR line numbers (1-based)
    source_to_ir: Dict[int, List[int]] = field(default_factory=dict)
    # Mapping from IR line number to source line number (1-based)
    ir_to_source: Dict[int, int] = field(default_factory=dict)


@dataclass
class GraphNode:
    """Node for graph visualization."""
    id: str
    label: str
    node_type: str  # 'function', 'class', 'module', 'variable', 'object'
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    """Edge for graph visualization."""
    source: str
    target: str
    edge_type: str  # 'call', 'pfg_normal', 'pfg_inherit', 'pfg_instance'
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DeltaSummary:
    """Summary of changes between iteration k and current iteration."""
    from_iteration: int
    to_iteration: int
    
    # Points-to changes
    points_to_additions: List[Dict[str, Any]]
    
    # Call edge changes
    new_call_edges: List[Dict[str, Any]]
    new_call_failures: List[Dict[str, Any]]
    
    # Constraint activity
    constraints_applied_counts: Dict[str, int]
    constraint_examples: List[str]
    
    # Scope/context changes
    new_scopes_analyzed: List[str]
    
    # PFG activity
    pfg_activations: int
    pfg_object_flow: int
    
    def to_text(self) -> str:
        lines = [
            f"=== Delta Summary: Iteration {self.from_iteration} → {self.to_iteration} ===",
            "",
            f"--- Points-to Additions ({len(self.points_to_additions)}) ---"
        ]
        for item in self.points_to_additions[:50]:  # Limit display
            lines.append(f"  {item.get('variable', '?')}: +{item.get('added_count', 0)} objects")
        if len(self.points_to_additions) > 50:
            lines.append(f"  ... and {len(self.points_to_additions) - 50} more")
        
        lines.append("")
        lines.append(f"--- New Call Edges ({len(self.new_call_edges)}) ---")
        for edge in self.new_call_edges[:30]:
            lines.append(f"  {edge.get('caller', '?')} -> {edge.get('callee', '?')}")
        if len(self.new_call_edges) > 30:
            lines.append(f"  ... and {len(self.new_call_edges) - 30} more")
        
        lines.append("")
        lines.append(f"--- Call Failures ({len(self.new_call_failures)}) ---")
        for fail in self.new_call_failures[:20]:
            lines.append(f"  {fail.get('call_site', '?')}: {fail.get('reason', '?')}")
        if len(self.new_call_failures) > 20:
            lines.append(f"  ... and {len(self.new_call_failures) - 20} more")
        
        lines.append("")
        lines.append("--- Constraints Applied ---")
        for ctype, count in sorted(self.constraints_applied_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {ctype}: {count}")
        
        lines.append("")
        lines.append(f"--- New Scopes Analyzed ({len(self.new_scopes_analyzed)}) ---")
        for scope in self.new_scopes_analyzed[:20]:
            lines.append(f"  {scope}")
        if len(self.new_scopes_analyzed) > 20:
            lines.append(f"  ... and {len(self.new_scopes_analyzed) - 20} more")
        
        lines.append("")
        lines.append("--- PFG Activity ---")
        lines.append(f"  Edge activations: {self.pfg_activations}")
        lines.append(f"  Object flow count: {self.pfg_object_flow}")
        
        return "\n".join(lines)


@dataclass
class HotSpots:
    """Hot spots for debugging - problematic areas in the analysis."""
    # Top callsites by failures
    top_failed_callsites: List[Dict[str, Any]]
    
    # Variables with empty points-to
    empty_pts_variables: List[str]
    
    # Functions/methods with objects but no incoming calls
    uncalled_functions: List[Dict[str, Any]]
    
    # Modules with low call edge coverage
    low_coverage_modules: List[Dict[str, Any]]
    
    def to_text(self) -> str:
        lines = [
            "=== HOT SPOTS (Potential Issues) ===",
            "",
            f"--- Top Failed Call Sites ({len(self.top_failed_callsites)}) ---"
        ]
        for item in self.top_failed_callsites[:20]:
            lines.append(f"  {item.get('call_site', '?')}: {item.get('failure_count', 0)} failures ({item.get('reason', '?')})")
        
        lines.append("")
        lines.append(f"--- Variables with Empty Points-to ({len(self.empty_pts_variables)}) ---")
        for var in self.empty_pts_variables[:30]:
            lines.append(f"  {var}")
        if len(self.empty_pts_variables) > 30:
            lines.append(f"  ... and {len(self.empty_pts_variables) - 30} more")
        
        lines.append("")
        lines.append(f"--- Uncalled Functions ({len(self.uncalled_functions)}) ---")
        for func in self.uncalled_functions[:20]:
            lines.append(f"  {func.get('name', '?')} (has {func.get('object_count', 0)} objects)")
        
        lines.append("")
        lines.append(f"--- Low Coverage Modules ({len(self.low_coverage_modules)}) ---")
        for mod in self.low_coverage_modules[:10]:
            lines.append(f"  {mod.get('module', '?')}: {mod.get('call_edges', 0)} call edges")
        
        return "\n".join(lines)


@dataclass
class AnalysisStatus:
    """Current status of the analysis."""
    is_running: bool
    is_paused: bool
    current_iteration: int
    worklist_size: int
    num_call_edges: int
    num_plain_call_edges: int
    num_pfg_edges: int
    num_variables: int
    num_objects: int
    elapsed_time: float
    
    def to_text(self) -> str:
        status = "RUNNING" if self.is_running else ("PAUSED" if self.is_paused else "COMPLETE")
        return (
            f"Status: {status}\n"
            f"Iteration: {self.current_iteration}\n"
            f"Worklist: {self.worklist_size}\n"
            f"Call edges: {self.num_call_edges} (absolute: {self.num_plain_call_edges})\n"
            f"PFG edges: {self.num_pfg_edges}\n"
            f"Variables: {self.num_variables}\n"
            f"Objects: {self.num_objects}\n"
            f"Elapsed: {self.elapsed_time:.2f}s"
        )

