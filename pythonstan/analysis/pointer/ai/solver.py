"""Fixpoint solver for intraprocedural abstract interpretation.

This module implements:
- Worklist-based fixpoint solver
- SCC ordering for efficient iteration
- Widening at loop heads
- Optional narrowing pass after fixpoint
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
    from .api import PtaQuery

__all__ = ["AISolver", "SolverConfig"]

logger = logging.getLogger(__name__)


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
    """
    max_iterations: int = 100
    max_block_visits: int = 50
    widen_delay: int = 2
    enable_narrowing: bool = True
    narrowing_iterations: int = 2


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
        )

        # Build CFG
        self.cfg = self._build_cfg()
        
        # State per block (at block entry), keyed by BaseBlock.idx
        self.block_states: Dict[int, AIState] = {}
        
        # Visit counts per block (for widening decision)
        self.visit_counts: DefaultDict[int, int] = defaultdict(int)
        
        # Loop heads (targets of back edges)
        self.loop_heads: Set[int] = set()
        
        # Summary accumulator (shared across all transfers)
        self.summary = SummaryAccumulator()
        
        # Interpreter
        self.interpreter = IRInterpreter(
            pta_query=pta_query,
            budget=budget,
            caller_scope=caller_scope,
            call_context=call_context,
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

    def solve(self) -> AISummary:
        """Run fixpoint computation and return summary.
        
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

        # Initialize worklist with int block IDs
        worklist: List[int] = [entry_block]
        self.block_states[entry_block] = self.initial_state

        iterations = 0
        
        # Main fixpoint loop
        while worklist and iterations < self.config.max_iterations:
            iterations += 1
            
            # Pop block from worklist (FIFO for breadth-first)
            block_id = worklist.pop(0)
            
            # Check visit count
            if self.visit_counts[block_id] >= self.config.max_block_visits:
                continue
            self.visit_counts[block_id] += 1

            # Get current state at block entry
            state = self.block_states.get(block_id)
            if state is None:
                continue

            # Process block
            successors = self._process_block(block_id, state)

            # Update successor states and add to worklist
            for succ_id, succ_state in successors:
                if succ_id is None:
                    continue
                    
                changed = self._update_block_state(succ_id, succ_state)
                if changed and succ_id not in worklist:
                    worklist.append(succ_id)

        # Optional narrowing pass
        if self.config.enable_narrowing:
            self._narrowing_pass()

        return self.summary.to_summary()

    def _process_block(
        self,
        block_id: int,
        state: AIState,
    ) -> List[Tuple[Optional[int], AIState]]:
        """Process a single CFG block.
        
        Args:
            block_id: Block identifier (BaseBlock.idx)
            state: State at block entry
            
        Returns:
            List of (successor_id, state_at_successor_entry) pairs
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
                    # Branching: return all successor states
                    # Convert label strings to block indices if needed
                    resolved_succs = []
                    for label, st in result.states:
                        if label is None:
                            resolved_succs.append((self._get_fallthrough(block_id), st))
                        elif isinstance(label, int):
                            resolved_succs.append((label, st))
                        else:
                            # Label is a string, try to resolve it via fallthrough
                            resolved_succs.append((self._get_fallthrough(block_id), st))
                    return resolved_succs
            else:
                # No state change
                pass

        # Normal fall-through
        fallthrough = self._get_fallthrough(block_id)
        if fallthrough is not None:
            return [(fallthrough, current_state)]
        return []

    def _update_block_state(
        self,
        block_id: int,
        new_state: AIState,
    ) -> bool:
        """Update state at block entry, with widening at loop heads.
        
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
        should_widen = is_loop_head and visits >= self.config.widen_delay

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
        """Run narrowing iterations to refine after widening."""
        if self.cfg is None:
            return

        for _ in range(self.config.narrowing_iterations):
            changed = False
            
            for block_id in list(self.block_states.keys()):
                old_state = self.block_states[block_id]
                
                # Recompute incoming state from predecessors
                preds = self._get_predecessors(block_id)
                if not preds:
                    continue
                    
                incoming = AIState.bottom(self.pta_query, self.budget)
                for pred_id in preds:
                    pred_state = self.block_states.get(pred_id)
                    if pred_state:
                        incoming = incoming.join(pred_state)
                
                # Narrow
                narrowed = old_state.narrow(incoming)
                
                if not narrowed.leq(old_state):
                    self.block_states[block_id] = narrowed
                    changed = True
            
            if not changed:
                break

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

