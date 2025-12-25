"""Fixpoint solver for intraprocedural abstract interpretation.

This module implements:
- Worklist-based fixpoint solver with trace/value partitioning
- SCC ordering for efficient iteration
- Widening at loop heads with per-loop iteration bounds
- Optional narrowing pass after fixpoint
- Trace partitioning with delayed merge at configured merge points
- Value partitioning for string constants and booleans
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    DefaultDict,
    Dict,
    FrozenSet,
    List,
    NamedTuple,
    Optional,
    Set,
    Tuple,
)

from .state import AIState, AbsVal
from .summary import SummaryAccumulator
from .interpreter import IRInterpreter, TransferResult
from .api import AISummary, AnalysisBudget

if TYPE_CHECKING:
    from pythonstan.ir.ir_statements import IRStatement, IRScope, IRFunc
    from pythonstan.graph.cfg.cfg import ControlFlowGraph
    from pythonstan.graph.cfg.base_block import BaseBlock
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext, Scope
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from .api import PtaQuery

__all__ = ["AISolver", "SolverConfig", "TraceKey", "WorkItem"]

logger = logging.getLogger(__name__)


# =============================================================================
# TraceKey and WorkItem - Keys for partitioned state tracking
# =============================================================================

class TraceKey(NamedTuple):
    """Key identifying a unique trace (execution path) through the CFG.
    
    Traces are split at conditional branches and merged at configured merge points.
    
    Attributes:
        trace_id: Unique identifier for this trace lineage
        branch_history: Bounded hash of recent branch decisions (for compression)
    """
    trace_id: int
    branch_history: int = 0  # Hash of recent branch decisions
    
    @classmethod
    def initial(cls) -> 'TraceKey':
        """Create initial trace key for entry block."""
        return cls(trace_id=0, branch_history=0)
    
    def fork(self, new_trace_id: int, branch_tag: int) -> 'TraceKey':
        """Fork a new trace from this one with updated branch history.
        
        Args:
            new_trace_id: Fresh trace ID for the new trace
            branch_tag: Tag identifying which branch was taken
            
        Returns:
            New TraceKey with updated history
        """
        # Use bounded hash to keep branch_history from growing indefinitely
        # Keep only last 16 bits of history
        new_history = ((self.branch_history << 4) ^ branch_tag) & 0xFFFF
        return TraceKey(trace_id=new_trace_id, branch_history=new_history)


class WorkItem(NamedTuple):
    """Item in the solver worklist, identifying a (block, trace) pair.
    
    When trace partitioning is disabled, all items use TraceKey.initial().
    """
    block_id: int
    trace_key: TraceKey


# =============================================================================
# SolverConfig
# =============================================================================

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
        merge_points: Set of BaseBlock.idx where traces must merge.
            When non-empty, the solver keeps per-trace states distinct
            across blocks, only forcing joins at these merge points.
            Empty set = no trace partitioning (default, merge everywhere).
        max_traces_per_block: Maximum traces allowed per block before forced merge.
        max_total_traces: Maximum total traces in the system before eviction.
        
        Loop Bounds:
        loop_unroll: Global loop unroll/iteration limit before widening.
            Overridden by per-loop settings in loop_iters.
        loop_iters: Dict mapping loop-head block IDs to their specific
            iteration limits. Takes precedence over global loop_unroll.
        
        Value Partition:
        enable_value_partition: Enable splitting states by value (str_const, bool).
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
    
    # Trace partition: blocks where traces must merge (empty = merge everywhere)
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


# =============================================================================
# AISolver
# =============================================================================

class AISolver:
    """Worklist-based fixpoint solver for intraprocedural AI.
    
    Iterates over CFG blocks, applying transfer functions until
    fixpoint is reached (or budget exhausted).
    
    Features:
    - Worklist with priority based on CFG structure
    - Widening at loop heads after widen_delay iterations
    - Optional narrowing pass
    - Summary accumulation across all paths
    - Trace partitioning with delayed merge at configured merge points
    - Loop iteration bounds for controlled unrolling
    """

    def __init__(
        self,
        scope_ir: 'IRScope',
        initial_state: AIState,
        pta_query: 'PtaQuery',
        budget: AnalysisBudget,
        caller_scope: Optional['Scope'] = None,
        call_context: Optional['AbstractContext'] = None,
        config: Optional[SolverConfig] = None,
        recursion_depth: int = 0,
    ):
        """Initialize solver.
        
        Args:
            scope_ir: IR scope to analyze (IRFunc, IRModule, etc.)
            initial_state: Initial abstract state at entry
            pta_query: Interface to query PTA
            budget: Analysis budget
            caller_scope: Caller's scope (for context)
            call_context: Call context
            config: Solver configuration
            recursion_depth: Current recursion depth for nested AI calls
        """
        self.scope_ir = scope_ir
        self.initial_state = initial_state
        self.pta_query = pta_query
        self.budget = budget
        self.caller_scope = caller_scope
        self.call_context = call_context
        self.config = config or SolverConfig(
            max_iterations=budget.max_iterations,
            max_block_visits=budget.max_stmt_visits,
            enable_narrowing=budget.enable_narrowing,
            narrowing_iterations=budget.narrowing_iterations,
            max_addr_set_size=budget.max_addr_set_size,
        )

        # Build CFG
        self.cfg = self._build_cfg()
        
        # State per block (at block entry), keyed by BaseBlock.idx
        # When trace partitioning is disabled, this is the primary state map.
        self.block_states: Dict[int, AIState] = {}
        
        # Edge out-states: (pred_id, succ_id) -> AIState
        # Used for proper narrowing computation with branch-specific states
        self.edge_out_states: Dict[Tuple[int, int], AIState] = {}
        
        # Block out-states: block_id -> AIState (for fall-through blocks)
        # Used as fallback when edge_out_states not available
        self.block_out_states: Dict[int, AIState] = {}
        
        # Trace partition support: states keyed by (block_id, trace_key)
        # Enabled when config.merge_points is non-empty
        self.trace_states: Dict[Tuple[int, TraceKey], AIState] = {}
        self._next_trace_id: int = 1  # Start at 1 (0 is reserved for initial trace)
        
        # State update counts per (block, trace): used for proper widening trigger
        # This tracks how many times state was updated, not how many times block was processed
        self.state_update_counts: DefaultDict[Tuple[int, TraceKey], int] = defaultdict(int)
        
        # Visit counts per block (for max_block_visits budget - legacy)
        self.visit_counts: DefaultDict[int, int] = defaultdict(int)
        
        # Per-loop-head per-trace iteration counters for loop unroll bounds
        # Key: (loop_head_block_id, trace_key) -> iteration count
        self.loop_iteration_counts: DefaultDict[Tuple[int, TraceKey], int] = defaultdict(int)
        
        # Loop heads (targets of back edges)
        self.loop_heads: Set[int] = set()
        
        # Summary accumulator (shared across all transfers)
        self.summary = SummaryAccumulator()
        
        # Current recursion depth (for nested AI calls in descriptor/hook invocations)
        self.recursion_depth = recursion_depth
        
        # Interpreter
        self.interpreter = IRInterpreter(
            pta_query=pta_query,
            budget=budget,
            caller_scope=caller_scope,
            call_context=call_context,
            recursion_depth=self.recursion_depth,
        )

    def _build_cfg(self) -> Optional['ControlFlowGraph']:
        """Build CFG from scope IR.
        
        Uses World().scope_manager to retrieve existing IR/CFG if available,
        otherwise builds from statements.
        
        Returns:
            ControlFlowGraph for the scope, or None if not available
        """
        try:
            from pythonstan.world import World
            from pythonstan.analysis.transform.block_cfg import BlockCFGBuilder
            from pythonstan.graph.cfg.cfg import ControlFlowGraph
            from pythonstan.ir.ir_statements import IRFunc, IRModule, IRClass
            
            # First, try to get an existing block CFG from World
            try:
                existing_cfg = World().scope_manager.get_ir(self.scope_ir, "block cfg")
                if existing_cfg is not None and isinstance(existing_cfg, ControlFlowGraph):
                    return existing_cfg
            except Exception:
                pass  # World not initialized or scope not registered
            
            # Next, try to get IR statements from World
            ir_stmts = None
            try:
                ir_stmts = World().scope_manager.get_ir(self.scope_ir, "ir")
            except Exception:
                pass
            
            # If we have IR statements, build the CFG
            if ir_stmts is not None and isinstance(ir_stmts, list) and len(ir_stmts) > 0:
                builder = BlockCFGBuilder(self.scope_ir)
                builder.build_graph(ir_stmts)
                return builder.cfg
            
            return None
        except Exception as e:
            logger.warning(f"Failed to build CFG: {e}")
            return None

    # =========================================================================
    # Trace partition helpers
    # =========================================================================

    def _is_trace_partition_enabled(self) -> bool:
        """Check if trace partitioning is enabled (merge_points is non-empty)."""
        return len(self.config.merge_points) > 0

    def _allocate_trace_id(self) -> int:
        """Allocate a new unique trace ID."""
        tid = self._next_trace_id
        self._next_trace_id += 1
        return tid

    def _is_merge_point(self, block_id: int) -> bool:
        """Check if a block is a merge point where traces must join."""
        return block_id in self.config.merge_points

    def _get_merged_state_at_block(self, block_id: int) -> Optional[AIState]:
        """Get the merged state for a block (joining all traces if trace partitioning).
        
        For trace partitioning, joins all trace states that end at this block.
        For normal mode, just returns block_states[block_id].
        """
        if not self._is_trace_partition_enabled():
            return self.block_states.get(block_id)
        
        # Collect all trace states for this block
        trace_states_at_block = [
            state for (bid, tkey), state in self.trace_states.items()
            if bid == block_id
        ]
        
        if not trace_states_at_block:
            return self.block_states.get(block_id)
        
        # Join all trace states
        result = trace_states_at_block[0]
        for state in trace_states_at_block[1:]:
            result = result.join(state)
        
        return result

    def _get_traces_at_block(self, block_id: int) -> List[TraceKey]:
        """Get all trace keys that have states at a given block."""
        return [tkey for (bid, tkey) in self.trace_states.keys() if bid == block_id]

    def _count_total_traces(self) -> int:
        """Count total number of active traces in the system."""
        return len(self.trace_states)

    def _count_traces_at_block(self, block_id: int) -> int:
        """Count number of traces at a specific block."""
        return sum(1 for (bid, _) in self.trace_states.keys() if bid == block_id)

    def _gather_trace_states_for_block(
        self,
        block_id: int,
    ) -> Tuple[List[AIState], int, int]:
        """Collect trace states and associated counters for a block."""
        states: List[AIState] = []
        max_updates = 0
        max_loop_iters = 0
        for (bid, tkey), state in self.trace_states.items():
            if bid != block_id:
                continue
            states.append(state)
            max_updates = max(
                max_updates,
                self.state_update_counts.get((block_id, tkey), 0),
            )
            max_loop_iters = max(
                max_loop_iters,
                self.loop_iteration_counts.get((block_id, tkey), 0),
            )
        return states, max_updates, max_loop_iters

    def _clear_trace_state_for_block(self, block_id: int) -> None:
        """Remove all per-trace bookkeeping for a block."""
        for key in list(self.trace_states.keys()):
            if key[0] == block_id:
                del self.trace_states[key]
        for key in list(self.state_update_counts.keys()):
            if key[0] == block_id:
                del self.state_update_counts[key]
        for key in list(self.loop_iteration_counts.keys()):
            if key[0] == block_id:
                del self.loop_iteration_counts[key]

    def _evict_traces_if_over_budget(self, block_id: int) -> None:
        """Evict traces if over budget by merging oldest/smallest traces.
        
        Enforces both per-block and total trace limits.
        """
        # Per-block limit
        while self._count_traces_at_block(block_id) > self.config.max_traces_per_block:
            traces_at_block = [
                (tkey, state) for (bid, tkey), state in self.trace_states.items()
                if bid == block_id
            ]
            if len(traces_at_block) <= 1:
                break
            
            # Merge the two oldest traces (lowest trace_id)
            sorted_traces = sorted(traces_at_block, key=lambda x: x[0].trace_id)
            tkey1, state1 = sorted_traces[0]
            tkey2, state2 = sorted_traces[1]
            
            merged_state = state1.join(state2)
            # Keep the newer trace key, remove both old ones
            del self.trace_states[(block_id, tkey1)]
            del self.trace_states[(block_id, tkey2)]
            self.trace_states[(block_id, tkey2)] = merged_state
        
        # Total limit
        while self._count_total_traces() > self.config.max_total_traces:
            # Find the block with most traces and merge there
            block_trace_counts = defaultdict(int)
            for (bid, _) in self.trace_states.keys():
                block_trace_counts[bid] += 1
            
            if not block_trace_counts:
                break
            
            # Pick block with most traces
            max_block = max(block_trace_counts.keys(), key=lambda b: block_trace_counts[b])
            if block_trace_counts[max_block] <= 1:
                break
            
            # Merge two traces at that block
            traces_at_block = [
                (tkey, state) for (bid, tkey), state in self.trace_states.items()
                if bid == max_block
            ]
            sorted_traces = sorted(traces_at_block, key=lambda x: x[0].trace_id)
            tkey1, state1 = sorted_traces[0]
            tkey2, state2 = sorted_traces[1]
            
            merged_state = state1.join(state2)
            del self.trace_states[(max_block, tkey1)]
            del self.trace_states[(max_block, tkey2)]
            self.trace_states[(max_block, tkey2)] = merged_state

    def _collapse_traces_at_merge_point(self, block_id: int) -> None:
        """Collapse all traces at a merge point into a single merged state.
        
        Removes individual trace states and stores the merged state in block_states.
        """
        if not self._is_trace_partition_enabled():
            return
        
        if not self._is_merge_point(block_id):
            return
        
        merged = self._get_merged_state_at_block(block_id)
        if merged is not None:
            self.block_states[block_id] = merged
            
            # Remove trace states for this block and seed canonical trace key
            trace_states, max_updates, max_loop_iters = self._gather_trace_states_for_block(block_id)
            self._clear_trace_state_for_block(block_id)

            merge_key = TraceKey.initial()
            if trace_states:
                merged_seed = trace_states[0]
                for state in trace_states[1:]:
                    merged_seed = merged_seed.join(state)
            else:
                merged_seed = merged

            self.trace_states[(block_id, merge_key)] = merged_seed
            self.state_update_counts[(block_id, merge_key)] = max(max_updates, 1)
            if max_loop_iters:
                self.loop_iteration_counts[(block_id, merge_key)] = max_loop_iters

    def _merge_state_into_merge_point(
        self,
        block_id: int,
        incoming_state: AIState,
    ) -> Tuple[WorkItem, bool]:
        """Merge incoming state with all traces already at a merge point.
        
        Returns the canonical WorkItem for the merge point and whether the
        state changed.
        """
        merge_key = TraceKey.initial()

        existing_states, max_updates, max_loop_iters = self._gather_trace_states_for_block(block_id)

        # Also fold in any block-level state left by prior collapses
        block_entry = self.block_states.get(block_id)
        if block_entry is not None:
            existing_states.append(block_entry)

        # Clear all per-trace bookkeeping for this block so we can re-seed
        self._clear_trace_state_for_block(block_id)

        if existing_states:
            seed = existing_states[0]
            for st in existing_states[1:]:
                seed = seed.join(st)
            self.trace_states[(block_id, merge_key)] = seed
            self.state_update_counts[(block_id, merge_key)] = max(max_updates, 1)
            if max_loop_iters:
                self.loop_iteration_counts[(block_id, merge_key)] = max_loop_iters

        item = WorkItem(block_id=block_id, trace_key=merge_key)
        changed = self._update_state_for_item(item, incoming_state)

        # Keep block_states in sync for callers that read merged entries directly
        merged_state = self._get_state_for_work_item(item)
        if merged_state is not None:
            self.block_states[block_id] = merged_state

        return item, changed

    def _get_state_for_work_item(self, item: WorkItem) -> Optional[AIState]:
        """Get the state for a work item, handling both partitioned and non-partitioned modes."""
        if self._is_trace_partition_enabled():
            return self.trace_states.get((item.block_id, item.trace_key))
        else:
            return self.block_states.get(item.block_id)

    def _set_state_for_work_item(self, item: WorkItem, state: AIState) -> None:
        """Set the state for a work item, handling both modes."""
        if self._is_trace_partition_enabled():
            self.trace_states[(item.block_id, item.trace_key)] = state
        else:
            self.block_states[item.block_id] = state

    def solve(self) -> AISummary:
        """Run fixpoint computation and return summary.
        
        Uses WorkItem-based worklist when trace partitioning is enabled.
        Each WorkItem is a (block_id, trace_key) pair.
        
        Returns:
            AISummary with accumulated effects
        """
        if self.cfg is None:
            # No CFG: return conservative summary
            logger.warning("No CFG available, returning conservative summary")
            return self._conservative_summary()

        # Initialize entry block
        entry_block = self._get_entry_block()
        if entry_block is None:
            return self._conservative_summary()

        # Identify loop heads
        self._identify_loop_heads()

        # Initialize with entry work item
        initial_trace_key = TraceKey.initial()
        initial_item = WorkItem(block_id=entry_block, trace_key=initial_trace_key)
        
        # Initialize worklist with WorkItems
        worklist: List[WorkItem] = [initial_item]
        
        # Set initial state for entry
        if self._is_trace_partition_enabled():
            self.trace_states[(entry_block, initial_trace_key)] = self.initial_state
        else:
            self.block_states[entry_block] = self.initial_state

        iterations = 0
        
        # Main fixpoint loop
        while worklist and iterations < self.config.max_iterations:
            iterations += 1
            
            # Pop work item from worklist (FIFO for breadth-first)
            item = worklist.pop(0)
            block_id = item.block_id
            trace_key = item.trace_key
            
            # Check visit count for the block (budget limit)
            if self.visit_counts[block_id] >= self.config.max_block_visits:
                continue
            self.visit_counts[block_id] += 1

            # Get current state for this work item
            state = self._get_state_for_work_item(item)
            if state is None:
                continue
            
            # Track loop iteration counts for loop heads (used for widening trigger)
            # Note: We do NOT stop processing here - instead we use the count
            # to decide when to widen in _update_state_for_item.
            # Sound loop analysis requires: unroll N iterations, then widen,
            # then continue until fixpoint (state stops changing).
            if block_id in self.loop_heads:
                iter_key = (block_id, trace_key)
                self.loop_iteration_counts[iter_key] += 1

            # Process block
            successors = self._process_block_with_trace(block_id, trace_key, state)

            # Update successor states and add to worklist
            for succ_id, succ_trace_key, succ_state in successors:
                if succ_id is None:
                    continue
                
                # Record edge out-state for narrowing
                self.edge_out_states[(block_id, succ_id)] = succ_state
                
                if self._is_trace_partition_enabled() and self._is_merge_point(succ_id):
                    succ_item, changed = self._merge_state_into_merge_point(succ_id, succ_state)
                else:
                    succ_item = WorkItem(block_id=succ_id, trace_key=succ_trace_key)
                    changed = self._update_state_for_item(succ_item, succ_state)
                
                if changed:
                    # Evict traces if over budget
                    if self._is_trace_partition_enabled():
                        self._evict_traces_if_over_budget(succ_id)
                    
                    # Add to worklist if not already present
                    if succ_item not in worklist:
                        worklist.append(succ_item)

        # Merge all trace states into block_states for final summary
        if self._is_trace_partition_enabled():
            self._merge_all_traces_to_block_states()

        # Optional narrowing pass
        if self.config.enable_narrowing:
            self._narrowing_pass()

        return self.summary.to_summary()

    def _merge_all_traces_to_block_states(self) -> None:
        """Merge all trace states into block_states for final result."""
        # Group by block
        block_traces: DefaultDict[int, List[AIState]] = defaultdict(list)
        for (block_id, trace_key), state in self.trace_states.items():
            block_traces[block_id].append(state)
        
        for block_id, states in block_traces.items():
            if states:
                merged = states[0]
                for s in states[1:]:
                    merged = merged.join(s)
                self.block_states[block_id] = merged

    def _process_block(
        self,
        block_id: int,
        state: AIState,
    ) -> List[Tuple[Optional[int], AIState]]:
        """Process a single CFG block (legacy interface for non-partitioned mode).
        
        Args:
            block_id: Block identifier (BaseBlock.idx)
            state: State at block entry
            
        Returns:
            List of (successor_id, state_at_successor_entry) pairs
        """
        results = self._process_block_with_trace(block_id, TraceKey.initial(), state)
        # Convert to legacy format (drop trace_key)
        return [(succ_id, succ_state) for succ_id, _, succ_state in results]

    def _process_block_with_trace(
        self,
        block_id: int,
        trace_key: TraceKey,
        state: AIState,
    ) -> List[Tuple[Optional[int], TraceKey, AIState]]:
        """Process a single CFG block with trace tracking.
        
        On branching statements, forks the trace into distinct trace keys.
        
        Args:
            block_id: Block identifier (BaseBlock.idx)
            trace_key: Current trace key
            state: State at block entry
            
        Returns:
            List of (successor_id, trace_key, state_at_successor_entry) triples
        """
        block = self._get_block(block_id)
        if block is None:
            return []

        current_state = state
        
        # Process each statement in the block
        for stmt in self._get_block_stmts(block):
            result = self.interpreter.transfer(stmt, current_state, self.summary)
            
            if result.is_terminal:
                return []
            
            if result.states:
                # For non-branching statements, continue with the first state
                if len(result.states) == 1:
                    _, current_state = result.states[0]
                else:
                    # Branching: fork traces for each branch
                    resolved_succs: List[Tuple[Optional[int], TraceKey, AIState]] = []
                    
                    for branch_idx, (label, st) in enumerate(result.states):
                        # Resolve successor block ID
                        if label is None:
                            succ_id = self._get_fallthrough(block_id)
                        elif isinstance(label, int):
                            succ_id = label
                        else:
                            # Label is a string - try to resolve via CFG's label2blk
                            resolved_id = self._resolve_label_to_block_id(label)
                            if resolved_id is not None:
                                succ_id = resolved_id
                            else:
                                # Fall back to CFG successors if label resolution fails
                                succ_id = self._get_fallthrough(block_id)
                        
                        # Fork trace for each branch when trace partitioning is enabled
                        if self._is_trace_partition_enabled() and len(result.states) > 1:
                            new_trace_id = self._allocate_trace_id()
                            succ_trace_key = trace_key.fork(new_trace_id, branch_idx)
                        else:
                            succ_trace_key = trace_key
                        
                        resolved_succs.append((succ_id, succ_trace_key, st))
                        
                        # Record edge out-state for narrowing
                        if succ_id is not None:
                            self.edge_out_states[(block_id, succ_id)] = st
                    
                    return resolved_succs
            else:
                # No state change
                pass

        # Normal fall-through
        # Record out-state for narrowing
        self.block_out_states[block_id] = current_state
        
        fallthrough = self._get_fallthrough(block_id)
        if fallthrough is not None:
            return [(fallthrough, trace_key, current_state)]
        return []

    def _update_state_for_item(
        self,
        item: WorkItem,
        new_state: AIState,
    ) -> bool:
        """Update state for a work item, with widening at loop heads.
        
        Sound loop analysis:
        - First N iterations (N = loop_unroll or loop_iters[block_id]): use join
        - After N iterations: use widening to guarantee termination
        - Continue until fixpoint (state stops changing)
        
        Uses both state_update_counts and loop_iteration_counts for widening trigger.
        
        Args:
            item: Work item (block_id, trace_key)
            new_state: Incoming state
            
        Returns:
            True if state changed
        """
        block_id = item.block_id
        trace_key = item.trace_key
        
        old_state = self._get_state_for_work_item(item)
        
        if old_state is None:
            self._set_state_for_work_item(item, new_state)
            self.state_update_counts[(block_id, trace_key)] = 1
            return True

        # Determine widening threshold and whether to widen
        is_loop_head = block_id in self.loop_heads
        update_count = self.state_update_counts[(block_id, trace_key)]
        loop_iters = self.loop_iteration_counts.get((block_id, trace_key), 0)
        
        # Get per-loop or global widening threshold
        if block_id in self.config.loop_iters:
            widen_delay = self.config.loop_iters[block_id]
        elif is_loop_head:
            widen_delay = self.config.loop_unroll
        else:
            widen_delay = self.config.widen_delay
        
        # Widen at loop heads after threshold is reached
        # Use max of (update_count, loop_iters) to catch both paths
        should_widen = is_loop_head and max(update_count, loop_iters) >= widen_delay

        if should_widen:
            merged = old_state.widen(new_state)
        else:
            merged = old_state.join(new_state)

        # Check if state changed (fixpoint test)
        if merged.leq(old_state) and old_state.leq(merged):
            return False

        self._set_state_for_work_item(item, merged)
        self.state_update_counts[(block_id, trace_key)] = update_count + 1
        return True

    def _update_block_state(
        self,
        block_id: int,
        new_state: AIState,
    ) -> bool:
        """Update state at block entry, with widening at loop heads.
        
        Uses per-loop iteration limits from config.loop_iters or
        global config.loop_unroll to determine widening threshold.
        
        Args:
            block_id: Block identifier (BaseBlock.idx)
            new_state: Incoming state
            
        Returns:
            True if state changed
        """
        old_state = self.block_states.get(block_id)
        
        if old_state is None:
            self.block_states[block_id] = new_state
            return True

        # Check if we need to widen
        is_loop_head = block_id in self.loop_heads
        visits = self.visit_counts[block_id]
        
        # Determine widening delay: use per-loop setting if available
        if block_id in self.config.loop_iters:
            widen_delay = self.config.loop_iters[block_id]
        elif is_loop_head:
            # Use global loop_unroll for loop heads
            widen_delay = self.config.loop_unroll
        else:
            # Non-loop-head blocks use standard widen_delay
            widen_delay = self.config.widen_delay
        
        should_widen = is_loop_head and visits >= widen_delay

        if should_widen:
            merged = old_state.widen(new_state)
        else:
            merged = old_state.join(new_state)

        # Check if state changed
        if merged.leq(old_state) and old_state.leq(merged):
            return False

        self.block_states[block_id] = merged
        return True

    def _identify_loop_heads(self) -> None:
        """Identify loop heads (targets of back edges) in CFG."""
        if self.cfg is None:
            return

        # Simple heuristic: blocks that are targets of edges from
        # blocks with higher indices (assuming forward numbering)
        visited: Set[int] = set()
        
        def dfs(block_id: int, path: Set[int]) -> None:
            if block_id in path:
                # Back edge found
                self.loop_heads.add(block_id)
                return
            if block_id in visited:
                return
                
            visited.add(block_id)
            path = path | {block_id}
            
            for succ in self._get_successors(block_id):
                dfs(succ, path)
        
        entry = self._get_entry_block()
        if entry is not None:
            dfs(entry, set())

    def _narrowing_pass(self) -> None:
        """Run narrowing iterations to refine after widening.
        
        Uses edge_out_states for edge-specific states (from branches), falling
        back to block_out_states for fall-through blocks.
        
        Narrowing refines the fixpoint by computing meet of current state with
        a join of predecessor out-states, then re-executing transfers.
        
        IMPORTANT: During narrowing, we use a throwaway summary to avoid
        duplicating effects. The main summary was already populated during
        the widening fixpoint phase.
        """
        if self.cfg is None:
            return

        # Use a throwaway summary during narrowing to avoid duplicating effects
        # The main summary was populated during the widening phase
        narrowing_summary = SummaryAccumulator()

        for iteration in range(self.config.narrowing_iterations):
            changed = False
            
            # Process blocks in reverse postorder for efficiency
            for block_id in list(self.block_states.keys()):
                old_state = self.block_states.get(block_id)
                if old_state is None:
                    continue
                
                # Recompute incoming state from predecessor out-states
                preds = self._get_predecessors(block_id)
                if not preds:
                    continue
                
                # Use edge-specific states when available
                incoming_states: List[AIState] = []
                for pred_id in preds:
                    # First try edge out-state (from branching)
                    edge_state = self.edge_out_states.get((pred_id, block_id))
                    if edge_state is not None:
                        incoming_states.append(edge_state)
                    else:
                        # Fall back to block out-state (fall-through)
                        block_out = self.block_out_states.get(pred_id)
                        if block_out is not None:
                            incoming_states.append(block_out)
                        else:
                            # Last resort: use predecessor's entry state
                            pred_entry = self.block_states.get(pred_id)
                            if pred_entry is not None:
                                incoming_states.append(pred_entry)
                
                if not incoming_states:
                    continue
                
                # Join all incoming states
                incoming = incoming_states[0]
                for s in incoming_states[1:]:
                    incoming = incoming.join(s)
                
                # Narrow: meet the current state with incoming
                narrowed = old_state.narrow(incoming)
                
                # Check if state changed
                if not narrowed.leq(old_state) or not old_state.leq(narrowed):
                    self.block_states[block_id] = narrowed
                    changed = True
                    
                    # Re-execute transfers to update out-states
                    # Use throwaway summary to avoid duplicating effects
                    self._recompute_block_out_states(block_id, narrowed, narrowing_summary)
            
            if not changed:
                break

    def _recompute_block_out_states(
        self, 
        block_id: int, 
        entry_state: AIState,
        summary: Optional[SummaryAccumulator] = None,
    ) -> None:
        """Re-execute transfers to compute out-states after narrowing.
        
        Updates block_out_states and edge_out_states based on the new entry state.
        
        Args:
            block_id: Block to recompute
            entry_state: Entry state for the block
            summary: Summary accumulator to use (defaults to self.summary)
        """
        block = self._get_block(block_id)
        if block is None:
            return
        
        # Use provided summary or default to main summary
        use_summary = summary if summary is not None else self.summary
        
        current_state = entry_state
        
        # Process each statement in the block
        for stmt in self._get_block_stmts(block):
            result = self.interpreter.transfer(stmt, current_state, use_summary)
            
            if result.is_terminal:
                # Terminal block - no out-state
                self.block_out_states.pop(block_id, None)
                return
            
            if result.states:
                if len(result.states) == 1:
                    _, current_state = result.states[0]
                else:
                    # Branching: update edge out-states
                    for label, st in result.states:
                        if label is None:
                            succ_id = self._get_fallthrough(block_id)
                        elif isinstance(label, int):
                            succ_id = label
                        else:
                            succ_id = self._resolve_label_to_block_id(label)
                            if succ_id is None:
                                succ_id = self._get_fallthrough(block_id)
                        
                        if succ_id is not None:
                            self.edge_out_states[(block_id, succ_id)] = st
                    return
        
        # Normal fall-through: update block out-state
        self.block_out_states[block_id] = current_state

    def _conservative_summary(self) -> AISummary:
        """Return a conservative summary when analysis fails."""
        # Return top with unknown effect
        return AISummary(
            ret=frozenset(),
            writes=(),
            reads=(),
            calls=(),
            unknown_effects=(),
            may_raise=frozenset({"Exception"}),
        )

    # =========================================================================
    # CFG navigation helpers
    # =========================================================================

    def _get_entry_block(self) -> Optional[int]:
        """Get entry block identifier (BaseBlock.idx)."""
        if self.cfg is None:
            return None
        try:
            if self.cfg.entry_blk is not None:
                return self.cfg.entry_blk.idx
        except Exception:
            pass
        return None

    def _get_block(self, block_id: int) -> Optional['BaseBlock']:
        """Get CFG block by identifier (BaseBlock.idx)."""
        if self.cfg is None:
            return None
        try:
            for block in self.cfg.blks:
                if block.idx == block_id:
                    return block
            # Also check entry block
            if self.cfg.entry_blk is not None and self.cfg.entry_blk.idx == block_id:
                return self.cfg.entry_blk
        except Exception:
            pass
        return None

    def _get_block_stmts(self, block: 'BaseBlock') -> List['IRStatement']:
        """Get statements in a block."""
        try:
            return list(block.stmts) if hasattr(block, 'stmts') else []
        except Exception:
            return []

    def _get_successors(self, block_id: int) -> List[int]:
        """Get successor block identifiers."""
        block = self._get_block(block_id)
        if block is None or self.cfg is None:
            return []
        try:
            succs = self.cfg.succs_of(block)
            return [s.idx for s in succs]
        except Exception:
            return []

    def _get_predecessors(self, block_id: int) -> List[int]:
        """Get predecessor block identifiers."""
        block = self._get_block(block_id)
        if block is None or self.cfg is None:
            return []
        try:
            preds = self.cfg.preds_of(block)
            return [p.idx for p in preds]
        except Exception:
            return []

    def _get_fallthrough(self, block_id: int) -> Optional[int]:
        """Get fall-through successor for a block."""
        succs = self._get_successors(block_id)
        return succs[0] if succs else None

    def _resolve_label_to_block_id(self, label_str: str) -> Optional[int]:
        """Resolve a label string to a block ID via CFG.label2blk.
        
        Args:
            label_str: Label string (e.g., "label_5")
            
        Returns:
            Block ID (BaseBlock.idx) if found, None otherwise
        """
        if self.cfg is None:
            return None
        
        try:
            from pythonstan.ir.ir_statements import Label
            
            # Try to find the label in CFG's label2blk dictionary
            for label_obj, block in self.cfg.label2blk.items():
                # Compare label string representation
                if hasattr(label_obj, 'to_s') and label_obj.to_s() == label_str:
                    return block.idx
                # Also try string conversion
                if str(label_obj) == label_str or str(label_obj.idx) == label_str:
                    return block.idx
            
            # Try parsing label_str to extract idx (format: "label_N" or just "N")
            if label_str.startswith("label_"):
                try:
                    idx = int(label_str[6:])  # Skip "label_" prefix
                    # Find block with matching label idx
                    for label_obj, block in self.cfg.label2blk.items():
                        if hasattr(label_obj, 'idx') and label_obj.idx == idx:
                            return block.idx
                except ValueError:
                    pass
            
            # Try direct integer parse
            try:
                idx = int(label_str)
                # Check if a block with this idx exists
                for block in self.cfg.blks:
                    if block.idx == idx:
                        return idx
            except ValueError:
                pass
            
        except Exception:
            pass
        
        return None

