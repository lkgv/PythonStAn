"""Narrowing pass for AI solver.

Implements post-widening refinement by:
- Recomputing incoming states from predecessor out-states
- Applying narrowing (meet) to refine after widening
- Re-executing transfers to update out-states
"""

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from .state import AIState
    from .summary import SummaryAccumulator
    from .interpreter import IRInterpreter
    from ._cfg_adapter import CFGAdapter

__all__ = ["NarrowingPass"]


class NarrowingPass:
    """Narrowing pass to refine states after widening fixpoint.
    
    Uses edge_out_states for edge-specific states (from branches), falling
    back to block_out_states for fall-through blocks.
    
    Narrowing refines the fixpoint by computing meet of current state with
    a join of predecessor out-states, then re-executing transfers.
    
    IMPORTANT: During narrowing, we use a throwaway summary to avoid
    duplicating effects. The main summary was already populated during
    the widening fixpoint phase.
    """

    def __init__(
        self,
        cfg_adapter: 'CFGAdapter',
        interpreter: 'IRInterpreter',
        block_states: Dict[int, 'AIState'],
        edge_out_states: Dict[Tuple[int, int], 'AIState'],
        block_out_states: Dict[int, 'AIState'],
    ):
        """Initialize narrowing pass.
        
        Args:
            cfg_adapter: CFG navigation interface
            interpreter: IR interpreter for transfer functions
            block_states: Block entry states to refine
            edge_out_states: Edge-specific out-states
            block_out_states: Block out-states (fallback)
        """
        self.cfg_adapter = cfg_adapter
        self.interpreter = interpreter
        self.block_states = block_states
        self.edge_out_states = edge_out_states
        self.block_out_states = block_out_states

    def run(self, narrowing_iterations: int) -> None:
        """Run narrowing iterations.
        
        Args:
            narrowing_iterations: Number of narrowing iterations to perform
        """
        from .summary import SummaryAccumulator
        
        # Use a throwaway summary during narrowing to avoid duplicating effects
        # The main summary was populated during the widening phase
        narrowing_summary = SummaryAccumulator()

        for iteration in range(narrowing_iterations):
            changed = False
            
            # Process blocks in reverse postorder for efficiency
            for block_id in list(self.block_states.keys()):
                old_state = self.block_states.get(block_id)
                if old_state is None:
                    continue
                
                # Recompute incoming state from predecessor out-states
                preds = self.cfg_adapter.get_predecessors(block_id)
                if not preds:
                    continue
                
                # Use edge-specific states when available
                incoming_states: List['AIState'] = []
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
                    self._recompute_block_out_states(block_id, narrowed, narrowing_summary)
            
            if not changed:
                break

    def _recompute_block_out_states(
        self, 
        block_id: int, 
        entry_state: 'AIState',
        summary: 'SummaryAccumulator',
    ) -> None:
        """Re-execute transfers to compute out-states after narrowing.
        
        Updates block_out_states and edge_out_states based on the new entry state.
        
        Args:
            block_id: Block to recompute
            entry_state: Entry state for the block
            summary: Summary accumulator to use
        """
        block = self.cfg_adapter.get_block(block_id)
        if block is None:
            return
        
        current_state = entry_state
        
        # Process each statement in the block
        for stmt_index, stmt in enumerate(self.cfg_adapter.get_block_stmts(block)):
            self.interpreter.set_current_location(block_id, stmt_index)
            result = self.interpreter.transfer(stmt, current_state, summary)
            
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
                        succ_id = self.cfg_adapter.resolve_label_to_block_id(label)
                        
                        if succ_id is not None:
                            self._set_edge_out_state(block_id, succ_id, st)
                    return
        
        # Normal fall-through: update block out-state
        self._set_block_out_state(block_id, current_state)

    def _set_edge_out_state(self, pred_id: int, succ_id: int, state: 'AIState') -> None:
        """Store edge out-state using join to accumulate contributions."""
        existing = self.edge_out_states.get((pred_id, succ_id))
        if existing is None:
            self.edge_out_states[(pred_id, succ_id)] = state
        else:
            self.edge_out_states[(pred_id, succ_id)] = existing.join(state)

    def _set_block_out_state(self, block_id: int, state: 'AIState') -> None:
        """Store block out-state using join when multiple traces reach it."""
        existing = self.block_out_states.get(block_id)
        if existing is None:
            self.block_out_states[block_id] = state
        else:
            self.block_out_states[block_id] = existing.join(state)
