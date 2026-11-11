"""CFG adapter for AI solver.

Provides a clean interface to CFG operations used by the fixpoint solver:
- Entry/exit block access
- Predecessor/successor queries
- Label resolution
- Loop head detection (back-edge analysis)
"""

from typing import TYPE_CHECKING, Any, List, Optional, Set

if TYPE_CHECKING:
    from pythonstan.graph.cfg.cfg import ControlFlowGraph
    from pythonstan.graph.cfg.base_block import BaseBlock
    from pythonstan.ir.ir_statements import IRStatement, Label

__all__ = ["CFGAdapter"]


class CFGAdapter:
    """Adapter providing CFG navigation for AI solver.
    
    Encapsulates all CFG queries to isolate solver from CFG implementation details.
    """

    def __init__(self, cfg: Optional['ControlFlowGraph']):
        """Initialize CFG adapter.
        
        Args:
            cfg: Control flow graph to wrap (may be None)
        """
        self.cfg = cfg

    def get_entry_block_id(self) -> Optional[int]:
        """Get entry block identifier (BaseBlock.idx)."""
        if self.cfg is None:
            return None
        if self.cfg.entry_blk is not None:
            return self.cfg.entry_blk.idx
        return None

    def get_block(self, block_id: int) -> Optional['BaseBlock']:
        """Get CFG block by identifier (BaseBlock.idx)."""
        if self.cfg is None:
            return None
        for block in self.cfg.blks:
            if block.idx == block_id:
                return block
        # Also check entry block
        if self.cfg.entry_blk is not None and self.cfg.entry_blk.idx == block_id:
            return self.cfg.entry_blk
        return None

    def get_all_block_ids(self) -> List[int]:
        """Get all block identifiers in the CFG."""
        if self.cfg is None:
            return []
        return [block.idx for block in self.cfg.blks]

    def get_block_stmts(self, block: 'BaseBlock') -> List['IRStatement']:
        """Get statements in a block."""
        return list(block.stmts) if hasattr(block, 'stmts') else []

    def get_successors(self, block_id: int) -> List[int]:
        """Get successor block identifiers."""
        block = self.get_block(block_id)
        if block is None or self.cfg is None:
            return []
        succs = self.cfg.succs_of(block)
        return [s.idx for s in succs]

    def get_predecessors(self, block_id: int) -> List[int]:
        """Get predecessor block identifiers."""
        block = self.get_block(block_id)
        if block is None or self.cfg is None:
            return []
        preds = self.cfg.preds_of(block)
        return [p.idx for p in preds]

    def get_fallthrough(self, block_id: int) -> Optional[int]:
        """Get fall-through successor for a block."""
        from pythonstan.graph.cfg.edges import NormalEdge

        block = self.get_block(block_id)
        if block is not None and self.cfg is not None:
            for edge in self.cfg.out_edges_of(block):
                if isinstance(edge, NormalEdge):
                    return edge.get_tgt().idx
        succs = self.get_successors(block_id)
        return succs[0] if succs else None

    def resolve_label_to_block_id(self, label: Any) -> Optional[int]:
        """Resolve a label (Label object, str, or int) to a block ID.
        
        Args:
            label: Label to resolve (Label object, string, or int)
            
        Returns:
            Block ID (BaseBlock.idx) if found, None otherwise
        """
        if label is None:
            return None
        if isinstance(label, int):
            return label
        
        # Try Label object first (preferred path)
        from pythonstan.ir.ir_statements import Label
        if isinstance(label, Label):
            return self._resolve_label_obj(label)
        
        # Fall back to string resolution for legacy compatibility
        label_str = label if isinstance(label, str) else str(label)
        return self._resolve_label_str(label_str)

    def _resolve_label_obj(self, label: 'Label') -> Optional[int]:
        """Resolve a Label object via CFG.label2blk dictionary."""
        if self.cfg is None:
            return None
        
        # Direct lookup in CFG's label2blk dictionary
        block = self.cfg.label2blk.get(label)
        if block is not None:
            return block.idx
        
        return None

    def _resolve_label_str(self, label_str: str) -> Optional[int]:
        """Resolve a label string to a block ID (legacy compatibility).
        
        Args:
            label_str: Label string (e.g., "label_5")
            
        Returns:
            Block ID (BaseBlock.idx) if found, None otherwise
        """
        if self.cfg is None:
            return None
        
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
        
        return None

    def identify_loop_heads(self) -> Set[int]:
        """Identify loop heads (targets of back edges) in CFG.
        
        Uses DFS to detect back edges.
        
        Returns:
            Set of block IDs that are loop heads
        """
        if self.cfg is None:
            return set()

        loop_heads: Set[int] = set()
        visited: Set[int] = set()
        
        def dfs(block_id: int, path: Set[int]) -> None:
            if block_id in path:
                # Back edge found
                loop_heads.add(block_id)
                return
            if block_id in visited:
                return
                
            visited.add(block_id)
            path = path | {block_id}
            
            for succ in self.get_successors(block_id):
                dfs(succ, path)
        
        entry = self.get_entry_block_id()
        if entry is not None:
            dfs(entry, set())
        
        return loop_heads
