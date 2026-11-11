"""Compatibility solver facade; all source execution uses the shared driver."""
from dataclasses import dataclass,field
from typing import Dict,Set
from pythonstan.compat.legacy_ai._trace_partition import TraceKey,WorkItem
from pythonstan.compat.legacy_ai._cfg_adapter import CFGAdapter
from pythonstan.compat.legacy_ai.api import AnalysisBudget
from pythonstan.compat.legacy_ai.state import AIState

@dataclass
class SolverConfig:
    """Configuration for the AI solver.
    
    Attributes:
        max_iterations: Maximum global iterations
        max_block_visits: Maximum visits per block
        widen_delay: Iterations before starting to widen
        enable_narrowing: Run narrowing pass after fixpoint
        narrowing_iterations: Number of narrowing iterations
        
        Trace Partition / Delayed Merge:
        enable_trace_partitioning: Enable trace partitioning even when
            merge_points is empty (useful with inferred merge points).
        infer_merge_points: Infer merge points from CFG join nodes (>=2 preds).
        merge_points: Set of BaseBlock.idx where traces must merge.
            When non-empty, the solver keeps per-trace states distinct
            across blocks, only forcing joins at these merge points.
            Empty set = no trace partitioning unless enable_trace_partitioning
            or infer_merge_points is set.
        max_traces_per_block: Maximum traces allowed per block before forced merge.
        max_total_traces: Maximum total traces in the system before eviction.
        
        Loop Bounds:
        loop_unroll: Global loop unroll/iteration limit before widening.
            Overridden by per-loop settings in loop_iters.
        loop_iters: Dict mapping loop-head block IDs to their specific
            iteration limits. Takes precedence over global loop_unroll.
        
        Value Partition:
        enable_value_partition: Enable splitting states by value (string/bool domains).
        max_value_partitions: Maximum value partitions per variable before merging.
        value_partition_str_cap: Max string constants to enumerate for partitioning.
        
        Widening:
        max_addr_set_size: Cap for address set size before summarization.
    """
    max_iterations: int = 100
    max_block_visits: int = 50
    widen_delay: int = 2
    enable_narrowing: bool = True
    narrowing_iterations: int = 2
    
    # Trace partition: blocks where traces must merge (empty = merge everywhere
    # unless enable_trace_partitioning or infer_merge_points is set)
    enable_trace_partitioning: bool = False
    infer_merge_points: bool = False
    merge_points: Set[int] = field(default_factory=set)
    max_traces_per_block: int = 8  # Cap on distinct traces per block
    max_total_traces: int = 32  # Cap on total traces in system
    
    # Loop bounds: global and per-loop-head limits
    loop_unroll: int = 5  # Default iterations before widening kicks in
    loop_iters: Dict[int, int] = field(default_factory=dict)  # Per-loop overrides
    
    # Value partition settings
    enable_value_partition: bool = False
    max_value_partitions: int = 4  # Max partitions per variable
    value_partition_str_cap: int = 4  # Max strings to enumerate for partitioning
    
    # Widening settings
    max_addr_set_size: int = 50  # Address set cap before summarization


class AISolver:
    """Run an original-source scope through the one maintained fixed point.

    Physical TAC block identities, old trace stores and private transfer hooks
    are retired.  ``exit_state`` is the actual shared normal-exit projection.
    """
    def __init__(self,scope_ir,initial_state,pta_query,budget,caller_scope=None,call_context=None,config=None,recursion_depth=0,work_ledger=None):
        self.scope_ir,self.initial_state,self.pta_query,self.budget=scope_ir,initial_state,pta_query,budget
        self.caller_scope,self.call_context=caller_scope,call_context
        self.config=config or SolverConfig();self.recursion_depth=recursion_depth;self.work_ledger=work_ledger
        self.block_states={};self.block_out_states={};self.edge_out_states={};self.visit_counts={};self.loop_heads=set()
        self.cfg=None;self.cfg_adapter=None;self.exit_state=None;self.result=None;self._shared_driver=None

    def solve(self):
        from pythonstan.compat.shared_ai import execute_scope
        self.result,self._shared_driver,self._shared_bridge,self._shared_frame=execute_scope(
            self.scope_ir,self.initial_state,self.pta_query,self.budget,self.caller_scope,self.call_context,self.config,self.work_ledger)
        self.exit_state=self.result.exit_state
        # These maps expose the one actual entry/exit; they are not invented
        # physical CFG IDs and never drive execution.
        self.block_states={'source_entry':self.initial_state}
        self.block_out_states={} if self.exit_state is None else {'source_exit':self.exit_state}
        self.summary=self.result
        return self.result
