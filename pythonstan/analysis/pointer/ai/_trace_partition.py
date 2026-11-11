"""Trace partitioning support for AI solver.

Provides:
- TraceKey: Identifier for execution traces
- WorkItem: (block_id, trace_key) pair for worklist
- TraceStateStore: Manages per-trace states and counters with eviction
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, DefaultDict, Dict, List, Optional, Set, Tuple

if TYPE_CHECKING:
    from .state import AIState

__all__ = ["TraceKey", "WorkItem", "TraceStateStore"]


# =============================================================================
# TraceKey and WorkItem
# =============================================================================

class TraceKey:
    """Key identifying a unique trace (execution path) through the CFG.
    
    Traces are split at conditional branches and merged at configured merge points.
    
    Attributes:
        trace_id: Unique identifier for this trace lineage
        branch_history: Bounded hash of recent branch decisions (for compression)
        vpart_depth: Count of value-partition refinements used along the trace
    """
    
    def __init__(
        self,
        trace_id: int,
        branch_history: int = 0,
        vpart_depth: int = 0,
    ):
        self.trace_id = trace_id
        self.branch_history = branch_history
        self.vpart_depth = vpart_depth
    
    @classmethod
    def initial(cls) -> 'TraceKey':
        """Create initial trace key for entry block."""
        return cls(trace_id=0, branch_history=0, vpart_depth=0)
    
    def fork(
        self,
        new_trace_id: int,
        branch_tag: int,
        vpart_depth: Optional[int] = None,
    ) -> 'TraceKey':
        """Fork a new trace from this one with updated branch history.
        
        Args:
            new_trace_id: Fresh trace ID for the new trace
            branch_tag: Tag identifying which branch was taken
            vpart_depth: Optional override for value partition depth on the child
            
        Returns:
            New TraceKey with updated history
        """
        # Use bounded hash to keep branch_history from growing indefinitely
        # Keep only last 16 bits of history
        new_history = ((self.branch_history << 4) ^ branch_tag) & 0xFFFF
        return TraceKey(
            trace_id=new_trace_id,
            branch_history=new_history,
            vpart_depth=self.vpart_depth if vpart_depth is None else vpart_depth,
        )
    
    def with_vpart_depth(self, depth: int) -> 'TraceKey':
        """Return a copy of this TraceKey with updated value-partition depth."""
        return TraceKey(
            trace_id=self.trace_id,
            branch_history=self.branch_history,
            vpart_depth=depth,
        )
    
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TraceKey):
            return False
        return (
            self.trace_id == other.trace_id
            and self.branch_history == other.branch_history
            and self.vpart_depth == other.vpart_depth
        )
    
    def __hash__(self) -> int:
        return hash((self.trace_id, self.branch_history, self.vpart_depth))
    
    def __repr__(self) -> str:
        return f"TraceKey(id={self.trace_id}, hist={self.branch_history:04x}, vpart={self.vpart_depth})"


class WorkItem:
    """Item in the solver worklist, identifying a (block, trace) pair.
    
    When trace partitioning is disabled, all items use TraceKey.initial().
    """
    
    def __init__(self, block_id: int, trace_key: TraceKey):
        self.block_id = block_id
        self.trace_key = trace_key
    
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, WorkItem):
            return False
        return self.block_id == other.block_id and self.trace_key == other.trace_key
    
    def __hash__(self) -> int:
        return hash((self.block_id, self.trace_key))
    
    def __repr__(self) -> str:
        return f"WorkItem(blk={self.block_id}, trace={self.trace_key})"


# =============================================================================
# TraceStateStore
# =============================================================================

class TraceStateStore:
    """Manages per-trace states and counters with eviction policies.
    
    Tracks:
    - trace_states: (block_id, trace_key) -> AIState
    - state_update_counts: how many times each state was updated
    - loop_iteration_counts: per-loop-head iteration counts
    - trace_visit_counts: how many times each (block, trace) was visited
    """

    def __init__(
        self,
        max_traces_per_block: int = 8,
        max_total_traces: int = 32,
    ):
        """Initialize trace state store.
        
        Args:
            max_traces_per_block: Maximum traces per block before eviction
            max_total_traces: Maximum total traces before eviction
        """
        self.max_traces_per_block = max_traces_per_block
        self.max_total_traces = max_total_traces
        
        self.trace_states: Dict[Tuple[int, TraceKey], 'AIState'] = {}
        self.state_update_counts: DefaultDict[Tuple[int, TraceKey], int] = defaultdict(int)
        self.loop_iteration_counts: DefaultDict[Tuple[int, TraceKey], int] = defaultdict(int)
        self.trace_visit_counts: DefaultDict[Tuple[int, TraceKey], int] = defaultdict(int)
        
        self._next_trace_id: int = 1  # Start at 1 (0 is reserved for initial trace)

    def allocate_trace_id(self) -> int:
        """Allocate a new unique trace ID."""
        tid = self._next_trace_id
        self._next_trace_id += 1
        return tid

    def get_state(self, block_id: int, trace_key: TraceKey) -> Optional['AIState']:
        """Get state for a (block, trace) pair."""
        return self.trace_states.get((block_id, trace_key))

    def set_state(self, block_id: int, trace_key: TraceKey, state: 'AIState') -> None:
        """Set state for a (block, trace) pair."""
        self.trace_states[(block_id, trace_key)] = state

    def get_traces_at_block(self, block_id: int) -> List[TraceKey]:
        """Get all trace keys that have states at a given block."""
        return [tkey for (bid, tkey) in self.trace_states.keys() if bid == block_id]

    def count_total_traces(self) -> int:
        """Count total number of active traces in the system."""
        return len(self.trace_states)

    def count_traces_at_block(self, block_id: int) -> int:
        """Count number of traces at a specific block."""
        return sum(1 for (bid, _) in self.trace_states.keys() if bid == block_id)

    def gather_trace_states_for_block(
        self,
        block_id: int,
    ) -> Tuple[List['AIState'], int, int, int, int]:
        """Collect trace states and associated counters for a block.
        
        Returns:
            Tuple of (states, max_updates, max_loop_iters, max_visits, max_vpart_depth)
        """
        states: List['AIState'] = []
        max_updates = 0
        max_loop_iters = 0
        max_visits = 0
        max_vpart_depth = 0
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
            max_visits = max(
                max_visits,
                self.trace_visit_counts.get((block_id, tkey), 0),
            )
            max_vpart_depth = max(max_vpart_depth, getattr(tkey, "vpart_depth", 0))
        return states, max_updates, max_loop_iters, max_visits, max_vpart_depth

    def clear_trace_state_for_block(self, block_id: int) -> None:
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
        for key in list(self.trace_visit_counts.keys()):
            if key[0] == block_id:
                del self.trace_visit_counts[key]

    def merge_trace_counters(
        self,
        block_id: int,
        keep_tkey: TraceKey,
        drop_tkey: TraceKey,
    ) -> None:
        """Merge per-trace counters when collapsing traces."""
        keep_key = (block_id, keep_tkey)
        drop_key = (block_id, drop_tkey)
        keep_updates = self.state_update_counts.get(keep_key, 0)
        drop_updates = self.state_update_counts.get(drop_key, 0)
        if drop_key in self.state_update_counts:
            del self.state_update_counts[drop_key]
        self.state_update_counts[keep_key] = max(keep_updates, drop_updates)

        keep_loops = self.loop_iteration_counts.get(keep_key, 0)
        drop_loops = self.loop_iteration_counts.get(drop_key, 0)
        if drop_key in self.loop_iteration_counts:
            del self.loop_iteration_counts[drop_key]
        if keep_loops or drop_loops:
            self.loop_iteration_counts[keep_key] = max(keep_loops, drop_loops)

        keep_visits = self.trace_visit_counts.get(keep_key, 0)
        drop_visits = self.trace_visit_counts.get(drop_key, 0)
        if drop_key in self.trace_visit_counts:
            del self.trace_visit_counts[drop_key]
        if keep_visits or drop_visits:
            self.trace_visit_counts[keep_key] = max(keep_visits, drop_visits)

    def evict_traces_if_over_budget(self, block_id: int) -> List[WorkItem]:
        """Evict traces if over budget by merging oldest/smallest traces.
        
        Enforces both per-block and total trace limits.
        Returns surviving merged work items that the solver must revisit.
        """
        affected: Set[WorkItem] = set()
        # Per-block limit
        while self.count_traces_at_block(block_id) > self.max_traces_per_block:
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
            merged_key = TraceKey(
                trace_id=tkey2.trace_id,
                branch_history=tkey2.branch_history,
                vpart_depth=max(tkey1.vpart_depth, tkey2.vpart_depth),
            )
            self.trace_states[(block_id, merged_key)] = merged_state
            affected.add(WorkItem(block_id, merged_key))
            self.merge_trace_counters(block_id, merged_key, tkey1)
            self.merge_trace_counters(block_id, merged_key, tkey2)
        
        # Total limit
        while self.count_total_traces() > self.max_total_traces:
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
            merged_key = TraceKey(
                trace_id=tkey2.trace_id,
                branch_history=tkey2.branch_history,
                vpart_depth=max(tkey1.vpart_depth, tkey2.vpart_depth),
            )
            self.trace_states[(max_block, merged_key)] = merged_state
            affected.add(WorkItem(max_block, merged_key))
            self.merge_trace_counters(max_block, merged_key, tkey1)
            self.merge_trace_counters(max_block, merged_key, tkey2)

        return sorted(
            (item for item in affected
             if self.get_state(item.block_id, item.trace_key) is not None),
            key=lambda item: (item.block_id, item.trace_key.trace_id),
        )

    def merge_state_into_merge_point(
        self,
        block_id: int,
        incoming_state: 'AIState',
        block_states: Dict[int, 'AIState'],
        update_state: Optional[Callable[[WorkItem, 'AIState'], bool]] = None,
    ) -> Tuple[WorkItem, bool]:
        """Merge incoming state with all traces already at a merge point.
        
        Returns the canonical WorkItem for the merge point and whether the
        state changed.
        
        Args:
            block_id: Block ID of the merge point
            incoming_state: State flowing into the merge point
            block_states: Block-level state map (for sync)
            
        Returns:
            Tuple of (work_item, changed)
        """
        existing_states, max_updates, max_loop_iters, max_visits, max_vpart_depth = (
            self.gather_trace_states_for_block(block_id)
        )

        # Also fold in any block-level state left by prior collapses
        block_entry = block_states.get(block_id)
        if block_entry is not None:
            existing_states.append(block_entry)

        # Clear all per-trace bookkeeping for this block so we can re-seed
        self.clear_trace_state_for_block(block_id)

        merge_key = TraceKey.initial().with_vpart_depth(max_vpart_depth)
        if existing_states:
            seed = existing_states[0]
            for st in existing_states[1:]:
                seed = seed.join(st)
            self.trace_states[(block_id, merge_key)] = seed
            self.state_update_counts[(block_id, merge_key)] = max(max_updates, 1)
            if max_loop_iters:
                self.loop_iteration_counts[(block_id, merge_key)] = max_loop_iters
            if max_visits:
                self.trace_visit_counts[(block_id, merge_key)] = max_visits

        item = WorkItem(block_id=block_id, trace_key=merge_key)
        
        # Update state for the merged item
        old_state = self.get_state(block_id, merge_key)
        if update_state is not None:
            # The solver applies its usual loop widening policy after traces
            # have been canonicalized. A merge point is not exempt from it.
            changed = update_state(item, incoming_state)
        elif old_state is None:
            self.set_state(block_id, merge_key, incoming_state)
            self.state_update_counts[(block_id, merge_key)] = 1
            changed = True
        else:
            merged = old_state.join(incoming_state)
            # Check if state changed (fixpoint test)
            if merged.leq(old_state) and old_state.leq(merged):
                changed = False
            else:
                self.set_state(block_id, merge_key, merged)
                self.state_update_counts[(block_id, merge_key)] += 1
                changed = True

        # Keep block_states in sync for callers that read merged entries directly
        merged_state = self.get_state(block_id, merge_key)
        if merged_state is not None:
            block_states[block_id] = merged_state

        return item, changed

    def merge_all_traces_to_block_states(
        self,
        block_states: Dict[int, 'AIState'],
    ) -> None:
        """Merge all trace states into block_states for final result.
        
        Args:
            block_states: Block-level state map to populate
        """
        # Group by block
        block_traces: DefaultDict[int, List['AIState']] = defaultdict(list)
        for (block_id, trace_key), state in self.trace_states.items():
            block_traces[block_id].append(state)
        
        for block_id, states in block_traces.items():
            if states:
                merged = states[0]
                for s in states[1:]:
                    merged = merged.join(s)
                block_states[block_id] = merged
