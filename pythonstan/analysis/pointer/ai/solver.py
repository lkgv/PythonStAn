"""Fixpoint solver for intraprocedural abstract interpretation.

This module implements:
- Worklist-based fixpoint solver with trace/value partitioning
- Widening at loop heads with per-loop iteration bounds
- Optional narrowing pass after fixpoint
- Trace partitioning with delayed merge at configured merge points
- Value partitioning for string constants and booleans

Architecture:
- CFGAdapter: Isolates CFG navigation (entry/preds/succs/labels/loop detection)
- TraceStateStore: Manages per-trace states and counters with eviction
- NarrowingPass: Post-widening refinement with edge-aware states
- AISolver: Orchestrates the fixpoint computation

Exception Handling:
- Internal exceptions now propagate naturally to AISummaryEngine boundary
- No silent failures; debugging is straightforward
"""

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    DefaultDict,
    Deque,
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
from ._cfg_adapter import CFGAdapter
from ._trace_partition import TraceKey, WorkItem, TraceStateStore
from ._narrowing import NarrowingPass

if TYPE_CHECKING:
    from pythonstan.ir.ir_statements import IRStatement, IRScope, IRFunc
    from pythonstan.graph.cfg.cfg import ControlFlowGraph
    from pythonstan.graph.cfg.base_block import BaseBlock
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext, Scope
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from .api import PtaQuery

__all__ = ["AISolver", "SolverConfig", "TraceKey", "WorkItem"]

logger = logging.getLogger(__name__)


# TraceKey and WorkItem are now imported from _trace_partition module


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
            enable_value_partition=budget.enable_value_partition,
            max_value_partitions=budget.max_value_partitions,
            value_partition_str_cap=budget.value_partition_str_cap,
        )

        # Build CFG
        self.cfg = self._build_cfg()
        self.cfg_adapter = CFGAdapter(self.cfg)
        
        # State per block (at block entry), keyed by BaseBlock.idx
        # When trace partitioning is disabled, this is the primary state map.
        self.block_states: Dict[int, AIState] = {}
        
        # Edge out-states: (pred_id, succ_id) -> AIState
        # Used for proper narrowing computation with branch-specific states
        self.edge_out_states: Dict[Tuple[int, int], AIState] = {}
        
        # Block out-states: block_id -> AIState (for fall-through blocks)
        # Used as fallback when edge_out_states not available
        self.block_out_states: Dict[int, AIState] = {}
        
        # Trace partition store (manages per-trace states and counters)
        self.trace_store = TraceStateStore(
            max_traces_per_block=self.config.max_traces_per_block,
            max_total_traces=self.config.max_total_traces,
        )
        
        # Visit counts: per block when trace partitioning is off
        self.visit_counts: DefaultDict[int, int] = defaultdict(int)
        
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
            scope_ir=self.scope_ir,
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
        from pythonstan.world import World
        from pythonstan.analysis.transform.block_cfg import BlockCFGBuilder
        from pythonstan.graph.cfg.cfg import ControlFlowGraph
        
        # Check if World is set up with scope_manager (not all tests initialize it)
        world = World()
        if not hasattr(world, 'scope_manager') or world.scope_manager is None:
            return None
        
        # Check scope_ir has qualname (required for lookup)
        if not hasattr(self.scope_ir, 'qualname'):
            return None
        
        # First, try to get an existing block CFG from World
        existing_cfg = world.scope_manager.get_ir(self.scope_ir, "block cfg")
        if existing_cfg is not None and isinstance(existing_cfg, ControlFlowGraph):
            return existing_cfg
        
        # Next, try to get IR statements from World
        ir_stmts = world.scope_manager.get_ir(self.scope_ir, "ir")
        
        # If we have IR statements, build the CFG
        if ir_stmts is not None and isinstance(ir_stmts, list) and len(ir_stmts) > 0:
            builder = BlockCFGBuilder(self.scope_ir)
            builder.build_graph(ir_stmts)
            return builder.cfg
        
        return None

    # =========================================================================
    # Trace partition helpers
    # =========================================================================

    def _is_trace_partition_enabled(self) -> bool:
        """Check if trace partitioning is enabled."""
        return bool(
            self.config.enable_trace_partitioning
            or self.config.infer_merge_points
            or len(self.config.merge_points) > 0
        )

    def _allocate_trace_id(self) -> int:
        """Allocate a new unique trace ID."""
        return self.trace_store.allocate_trace_id()

    def _is_merge_point(self, block_id: int) -> bool:
        """Check if a block is a merge point where traces must join."""
        return block_id in self.config.merge_points
    
    def _maybe_infer_merge_points(self) -> None:
        """Infer merge points (CFG joins) when enabled."""
        if not self.config.infer_merge_points:
            return
        if self.cfg is None:
            return
        
        inferred: Set[int] = set()
        for block_id in self.cfg_adapter.get_all_block_ids():
            preds = self.cfg_adapter.get_predecessors(block_id)
            if preds is not None and len(preds) >= 2:
                inferred.add(block_id)
        
        if inferred:
            # Additive with user-provided merge points
            self.config.merge_points = set(self.config.merge_points) | inferred
        
        # Ensure partitioning is considered enabled when inference is requested
        self.config.enable_trace_partitioning = True

    def _get_merged_state_at_block(self, block_id: int) -> Optional[AIState]:
        """Get the merged state for a block (joining all traces if trace partitioning).
        
        For trace partitioning, joins all trace states that end at this block.
        For normal mode, just returns block_states[block_id].
        """
        if not self._is_trace_partition_enabled():
            return self.block_states.get(block_id)
        
        # Collect all trace states for this block
        trace_keys = self.trace_store.get_traces_at_block(block_id)
        trace_states_at_block = [
            self.trace_store.get_state(block_id, tkey)
            for tkey in trace_keys
        ]
        trace_states_at_block = [s for s in trace_states_at_block if s is not None]
        
        if not trace_states_at_block:
            return self.block_states.get(block_id)
        
        # Join all trace states
        result = trace_states_at_block[0]
        for state in trace_states_at_block[1:]:
            result = result.join(state)
        
        return result

    def _get_traces_at_block(self, block_id: int) -> List[TraceKey]:
        """Get all trace keys that have states at a given block."""
        return self.trace_store.get_traces_at_block(block_id)

    def _count_total_traces(self) -> int:
        """Count total number of active traces in the system."""
        return self.trace_store.count_total_traces()

    def _count_traces_at_block(self, block_id: int) -> int:
        """Count number of traces at a specific block."""
        return self.trace_store.count_traces_at_block(block_id)

    def _set_edge_out_state(self, pred_id: int, succ_id: int, state: AIState) -> None:
        """Store edge out-state using join to accumulate contributions."""
        existing = self.edge_out_states.get((pred_id, succ_id))
        if existing is None:
            self.edge_out_states[(pred_id, succ_id)] = state
        else:
            self.edge_out_states[(pred_id, succ_id)] = existing.join(state)

    def _set_block_out_state(self, block_id: int, state: AIState) -> None:
        """Store block out-state using join when multiple traces reach it."""
        existing = self.block_out_states.get(block_id)
        if existing is None:
            self.block_out_states[block_id] = state
        else:
            self.block_out_states[block_id] = existing.join(state)

    def _evict_traces_if_over_budget(self, block_id: int) -> None:
        """Evict traces if over budget by merging oldest/smallest traces."""
        self.trace_store.evict_traces_if_over_budget(block_id)

    def _merge_state_into_merge_point(
        self,
        block_id: int,
        incoming_state: AIState,
    ) -> Tuple[WorkItem, bool]:
        """Merge incoming state with all traces already at a merge point.
        
        Returns the canonical WorkItem for the merge point and whether the
        state changed.
        """
        return self.trace_store.merge_state_into_merge_point(
            block_id, incoming_state, self.block_states
        )

    def _get_state_for_work_item(self, item: WorkItem) -> Optional[AIState]:
        """Get the state for a work item, handling both partitioned and non-partitioned modes."""
        if self._is_trace_partition_enabled():
            return self.trace_store.get_state(item.block_id, item.trace_key)
        else:
            return self.block_states.get(item.block_id)

    def _set_state_for_work_item(self, item: WorkItem, state: AIState) -> None:
        """Set the state for a work item, handling both modes."""
        if self._is_trace_partition_enabled():
            self.trace_store.set_state(item.block_id, item.trace_key, state)
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
        entry_block = self.cfg_adapter.get_entry_block_id()
        if entry_block is None:
            return self._conservative_summary()

        # Optionally infer merge points before deciding partitioning mode
        self._maybe_infer_merge_points()
        
        # Identify loop heads
        self.loop_heads = self.cfg_adapter.identify_loop_heads()

        # Initialize with entry work item
        initial_trace_key = TraceKey.initial()
        initial_item = WorkItem(block_id=entry_block, trace_key=initial_trace_key)
        
        # Initialize worklist with WorkItems
        worklist: Deque[WorkItem] = deque([initial_item])
        in_worklist: Set[WorkItem] = {initial_item}
        
        # Set initial state for entry
        if self._is_trace_partition_enabled():
            self.trace_store.set_state(entry_block, initial_trace_key, self.initial_state)
        else:
            self.block_states[entry_block] = self.initial_state

        iterations = 0
        
        # Main fixpoint loop
        while worklist and iterations < self.config.max_iterations:
            iterations += 1
            
            # Pop work item from worklist (FIFO for breadth-first)
            item = worklist.popleft()
            in_worklist.discard(item)
            block_id = item.block_id
            trace_key = item.trace_key
            
            # Check visit count for the block (budget limit)
            if self._is_trace_partition_enabled():
                visit_key = (block_id, trace_key)
                if self.trace_store.trace_visit_counts[visit_key] >= self.config.max_block_visits:
                    continue
                self.trace_store.trace_visit_counts[visit_key] += 1
            else:
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
                if self._is_trace_partition_enabled():
                    iter_key = (block_id, trace_key)
                    self.trace_store.loop_iteration_counts[iter_key] += 1

            # Process block
            successors = self._process_block_with_trace(block_id, trace_key, state)

            # Update successor states and add to worklist
            for succ_id, succ_trace_key, succ_state in successors:
                if succ_id is None:
                    continue
                
                # Record edge out-state for narrowing
                self._set_edge_out_state(block_id, succ_id, succ_state)
                
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
                    if succ_item not in in_worklist:
                        worklist.append(succ_item)
                        in_worklist.add(succ_item)

        # Merge all trace states into block_states for final summary
        if self._is_trace_partition_enabled():
            self.trace_store.merge_all_traces_to_block_states(self.block_states)

        # Optional narrowing pass
        if self.config.enable_narrowing:
            narrowing = NarrowingPass(
                cfg_adapter=self.cfg_adapter,
                interpreter=self.interpreter,
                block_states=self.block_states,
                edge_out_states=self.edge_out_states,
                block_out_states=self.block_out_states,
            )
            narrowing.run(self.config.narrowing_iterations)

        return self.summary.to_summary()

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
        block = self.cfg_adapter.get_block(block_id)
        if block is None:
            return []

        current_state = state
        
        # Process each statement in the block
        for stmt_index, stmt in enumerate(self.cfg_adapter.get_block_stmts(block)):
            branch_input_state = current_state
            self.interpreter.set_current_location(block_id, stmt_index)
            result = self.interpreter.transfer(stmt, current_state, self.summary)
            
            if result.is_terminal:
                return []
            
            if result.states:
                # For non-branching statements, continue with the first state
                if len(result.states) == 1:
                    _, current_state = result.states[0]
                else:
                    # Optionally cap value partitioning by avoiding further splits
                    states_for_branches = result.states
                    succ_vpart_depth = trace_key.vpart_depth
                    if self.config.enable_value_partition and len(result.states) > 1:
                        if succ_vpart_depth >= self.config.max_value_partitions:
                            # No-split: reuse the pre-branch state for all branches
                            states_for_branches = [
                                (label, branch_input_state) for (label, _) in result.states
                            ]
                        else:
                            succ_vpart_depth = succ_vpart_depth + 1

                    # Branching: fork traces for each branch
                    resolved_succs: List[Tuple[Optional[int], TraceKey, AIState]] = []
                    
                    for branch_idx, (label, st) in enumerate(states_for_branches):
                        # Resolve successor block ID
                        succ_id = self.cfg_adapter.resolve_label_to_block_id(label)
                        if succ_id is None:
                            succ_id = self.cfg_adapter.get_fallthrough(block_id)
                        
                        # Fork trace for each branch when trace partitioning is enabled
                        if self._is_trace_partition_enabled() and len(result.states) > 1:
                            new_trace_id = self._allocate_trace_id()
                            succ_trace_key = trace_key.fork(
                                new_trace_id,
                                branch_idx,
                                vpart_depth=succ_vpart_depth,
                            )
                        else:
                            succ_trace_key = trace_key.with_vpart_depth(succ_vpart_depth)
                        
                        resolved_succs.append((succ_id, succ_trace_key, st))
                        
                        # Record edge out-state for narrowing
                        if succ_id is not None:
                            self._set_edge_out_state(block_id, succ_id, st)
                    
                    return resolved_succs

        # Normal fall-through
        # Record out-state for narrowing
        self._set_block_out_state(block_id, current_state)
        
        fallthrough = self.cfg_adapter.get_fallthrough(block_id)
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
            if self._is_trace_partition_enabled():
                self.trace_store.state_update_counts[(block_id, trace_key)] = 1
            return True

        # Determine widening threshold and whether to widen
        is_loop_head = block_id in self.loop_heads
        if self._is_trace_partition_enabled():
            update_count = self.trace_store.state_update_counts.get((block_id, trace_key), 0)
            loop_iters = self.trace_store.loop_iteration_counts.get((block_id, trace_key), 0)
        else:
            update_count = self.visit_counts.get(block_id, 0)
            loop_iters = 0
        
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
        if self._is_trace_partition_enabled():
            self.trace_store.state_update_counts[(block_id, trace_key)] = update_count + 1
        return True

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


