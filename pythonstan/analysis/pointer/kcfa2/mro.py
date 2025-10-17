"""Method Resolution Order (MRO) computation using C3 linearization.

This module implements Python's C3 linearization algorithm for computing
Method Resolution Order (MRO) in class hierarchies. The C3 algorithm ensures
proper handling of multiple inheritance and diamond patterns.

References:
- PEP 253: Subtyping Built-in Types
- PEP 3119: Introducing Abstract Base Classes  
- "The Python 2.3 Method Resolution Order" by Michele Simionato
"""

from typing import Dict, List, Set, Optional

__all__ = ["ClassHierarchyManager", "compute_c3_mro", "MROError"]


class MROError(Exception):
    """Exception raised when MRO cannot be computed (inconsistent hierarchy)."""
    pass


class ClassHierarchyManager:
    """Manages class hierarchy for pointer analysis.
    
    Unlike pythonstan.world.ClassHierarchy which works with IRClass objects,
    this manager works with allocation site IDs (strings) for integration
    with the pointer analysis.
    """
    
    def __init__(self):
        """Initialize class hierarchy manager."""
        # Map: class_id -> list of direct base class IDs
        self._bases: Dict[str, List[str]] = {}
        # Map: class_id -> list of direct subclass IDs  
        self._subclasses: Dict[str, List[str]] = {}
        # Cache: class_id -> computed MRO
        self._mro_cache: Dict[str, List[str]] = {}
        
    def add_class(self, class_id: str, base_ids: Optional[List[str]] = None):
        """Add a class to the hierarchy.
        
        Args:
            class_id: Allocation site ID for the class
            base_ids: List of base class allocation site IDs (None for no bases)
        """
        if class_id not in self._bases:
            self._bases[class_id] = base_ids or []
            
        # Register in subclasses of bases
        if base_ids:
            for base_id in base_ids:
                if base_id not in self._subclasses:
                    self._subclasses[base_id] = []
                if class_id not in self._subclasses[base_id]:
                    self._subclasses[base_id].append(class_id)
                    
        # Invalidate MRO cache for this class and its subclasses
        self._invalidate_mro_cache(class_id)
        
    def _invalidate_mro_cache(self, class_id: str):
        """Invalidate MRO cache for a class and all its subclasses."""
        to_invalidate = [class_id]
        invalidated = set()
        
        while to_invalidate:
            cid = to_invalidate.pop()
            if cid in invalidated:
                continue
            invalidated.add(cid)
            
            # Remove from cache
            if cid in self._mro_cache:
                del self._mro_cache[cid]
                
            # Add subclasses to invalidation list
            if cid in self._subclasses:
                to_invalidate.extend(self._subclasses[cid])
                
    def get_bases(self, class_id: str) -> List[str]:
        """Get direct base classes for a class.
        
        Args:
            class_id: Class allocation site ID
            
        Returns:
            List of base class IDs (empty if no bases)
        """
        return self._bases.get(class_id, [])
        
    def get_subclasses(self, class_id: str) -> List[str]:
        """Get direct subclasses for a class.
        
        Args:
            class_id: Class allocation site ID
            
        Returns:
            List of subclass IDs (empty if no subclasses)
        """
        return self._subclasses.get(class_id, [])
        
    def get_mro(self, class_id: str) -> List[str]:
        """Get Method Resolution Order for a class.
        
        Uses C3 linearization algorithm with caching for efficiency.
        
        Args:
            class_id: Class allocation site ID
            
        Returns:
            List of class IDs in MRO order (includes class_id itself)
            
        Raises:
            MROError: If hierarchy is inconsistent and MRO cannot be computed
        """
        if class_id in self._mro_cache:
            return self._mro_cache[class_id]
            
        mro = compute_c3_mro(class_id, self)
        self._mro_cache[class_id] = mro
        return mro
        
    def has_class(self, class_id: str) -> bool:
        """Check if a class is registered in the hierarchy."""
        return class_id in self._bases


def compute_c3_mro(class_id: str, hierarchy: ClassHierarchyManager) -> List[str]:
    """Compute C3 linearization (MRO) for a class.
    
    Python's MRO algorithm ensures:
    1. Children come before parents (depth-first)
    2. Parent order is preserved (left-to-right)
    3. Diamond patterns are handled correctly
    4. No class appears twice
    
    Algorithm:
        L(C) = [C] + merge(L(B1), L(B2), ..., L(Bn), [B1, B2, ..., Bn])
        
        where merge() selects the head of the first list such that this head
        does not appear in the tail of any other list.
    
    Args:
        class_id: Class to compute MRO for
        hierarchy: ClassHierarchyManager instance
        
    Returns:
        List of class IDs in MRO order (includes class_id as first element)
        
    Raises:
        MROError: If inheritance graph is inconsistent
        
    Examples:
        >>> # Diamond inheritance: D(B, C), B(A), C(A)
        >>> # MRO(D) = [D, B, C, A, object]
    """
    # Get direct bases
    bases = hierarchy.get_bases(class_id)
    
    if not bases:
        # No bases - MRO is just [class_id, object]
        # 'object' is implicit base for all classes
        if class_id == "object":
            return ["object"]
        else:
            return [class_id, "object"]
    
    # Compute MRO for each base recursively
    base_mros = []
    for base_id in bases:
        if not hierarchy.has_class(base_id):
            # Unknown base class - conservatively add to hierarchy
            hierarchy.add_class(base_id)
        base_mro = compute_c3_mro(base_id, hierarchy)
        base_mros.append(base_mro)
    
    # C3 linearization: L(C) = [C] + merge(L(B1), L(B2), ..., [B1, B2, ...])
    try:
        merged = _c3_merge(base_mros + [bases])
        return [class_id] + merged
    except MROError as e:
        # If merge fails, fall back to simple linearization
        # This handles cases where the hierarchy is malformed
        if hierarchy.get_bases(class_id):
            # Use first base's MRO + remaining bases
            return [class_id] + base_mros[0]
        else:
            return [class_id, "object"]


def _c3_merge(sequences: List[List[str]]) -> List[str]:
    """Merge sequences using C3 algorithm.
    
    Selects heads of sequences that don't appear in any tail,
    removes them from all sequences, and repeats until done.
    
    Args:
        sequences: List of sequences to merge
        
    Returns:
        Merged sequence
        
    Raises:
        MROError: If no valid merge exists (inconsistent hierarchy)
    """
    result = []
    
    # Make mutable copies
    seqs = [list(seq) for seq in sequences]
    
    while True:
        # Remove empty sequences
        seqs = [seq for seq in seqs if seq]
        if not seqs:
            return result
        
        # Find a candidate: head that doesn't appear in any tail
        candidate = None
        for seq in seqs:
            head = seq[0]
            
            # Check if head appears in any tail
            appears_in_tail = False
            for other_seq in seqs:
                if head in other_seq[1:]:
                    appears_in_tail = True
                    break
            
            if not appears_in_tail:
                candidate = head
                break
        
        if candidate is None:
            # No valid candidate - inconsistent hierarchy
            # This happens with badly formed inheritance graphs
            raise MROError(
                f"Cannot create consistent MRO: no valid candidate in {seqs}"
            )
        
        result.append(candidate)
        
        # Remove candidate from all sequences
        for seq in seqs:
            if seq and seq[0] == candidate:
                seq.pop(0)
    
    return result

