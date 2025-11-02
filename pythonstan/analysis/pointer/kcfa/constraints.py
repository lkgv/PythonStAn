"""Pointer constraints and constraint management.

This module defines the constraint types used in the pointer analysis and
provides efficient storage and indexing for constraint-based solving.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Set, Dict, Type, Tuple, Optional, TYPE_CHECKING
from collections import defaultdict

if TYPE_CHECKING:
    from .variable import Variable
    from .heap_model import Field
    from .object import AllocSite

__all__ = [
    "Constraint",
    "CopyConstraint",
    "LoadConstraint",
    "StoreConstraint",
    "AllocConstraint",
    "CallConstraint",
    "ReturnConstraint",
    "ConstraintManager"
]


class Constraint(ABC):
    """Base class for all pointer constraints.
    
    Constraints represent facts about pointer flow in the program.
    """
    
    @abstractmethod
    def variables(self) -> Set['Variable']:
        """Get all variables involved in this constraint.
        
        Returns:
            Set of variables
        """
        pass
    
    @abstractmethod
    def __str__(self) -> str:
        """String representation for debugging."""
        pass


@dataclass(frozen=True)
class CopyConstraint(Constraint):
    """Copy constraint: target = source.
    
    Represents assignment from one variable to another.
    
    Attributes:
        source: Source variable
        target: Target variable
    """
    
    source: 'Variable'
    target: 'Variable'
    
    def variables(self) -> Set['Variable']:
        """Get variables involved."""
        return {self.source, self.target}
    
    def __str__(self) -> str:
        return f"{self.target} = {self.source}"


@dataclass(frozen=True)
class LoadConstraint(Constraint):
    """Load constraint: target = base.field.
    
    Represents loading a field from an object.
    
    Attributes:
        base: Base variable pointing to objects
        field: Field being loaded
        target: Target variable receiving field value
    """
    
    base: 'Variable'
    field: 'Field'
    target: 'Variable'
    
    def variables(self) -> Set['Variable']:
        """Get variables involved."""
        return {self.base, self.target}
    
    def __str__(self) -> str:
        return f"{self.target} = {self.base}{self.field}"


@dataclass(frozen=True)
class StoreConstraint(Constraint):
    """Store constraint: base.field = source.
    
    Represents storing to a field of an object.
    
    Attributes:
        base: Base variable pointing to objects
        field: Field being stored to
        source: Source variable being stored
    """
    
    base: 'Variable'
    field: 'Field'
    source: 'Variable'
    
    def variables(self) -> Set['Variable']:
        """Get variables involved."""
        return {self.base, self.source}
    
    def __str__(self) -> str:
        return f"{self.base}{self.field} = {self.source}"


@dataclass(frozen=True)
class AllocConstraint(Constraint):
    """Allocation constraint: target = new Object.
    
    Represents object allocation.
    
    Attributes:
        target: Target variable receiving new object
        alloc_site: Allocation site of new object
    """
    
    target: 'Variable'
    alloc_site: 'AllocSite'
    
    def variables(self) -> Set['Variable']:
        """Get variables involved."""
        return {self.target}
    
    def __str__(self) -> str:
        return f"{self.target} = new {self.alloc_site}"


@dataclass(frozen=True)
class CallConstraint(Constraint):
    """Call constraint: target = callee(args...).
    
    Represents function call. Actual call edges are resolved dynamically
    based on what callee points to.
    
    Attributes:
        callee: Variable holding callable object
        args: Tuple of argument variables
        target: Optional target variable for return value
        call_site: Unique call site identifier
    """
    
    callee: 'Variable'
    args: Tuple['Variable', ...]
    target: Optional['Variable']
    call_site: str
    
    def variables(self) -> Set['Variable']:
        """Get variables involved."""
        vars = {self.callee, *self.args}
        if self.target:
            vars.add(self.target)
        return vars
    
    def __str__(self) -> str:
        args_str = ", ".join(str(a) for a in self.args)
        if self.target:
            return f"{self.target} = {self.callee}({args_str})"
        return f"{self.callee}({args_str})"


@dataclass(frozen=True)
class ReturnConstraint(Constraint):
    """Return constraint: connect callee return to caller target.
    
    Represents flow of return value from callee to caller.
    
    Attributes:
        callee_return: Special $return variable in callee
        caller_target: Target variable in caller
    """
    
    callee_return: 'Variable'
    caller_target: 'Variable'
    
    def variables(self) -> Set['Variable']:
        """Get variables involved."""
        return {self.callee_return, self.caller_target}
    
    def __str__(self) -> str:
        return f"{self.caller_target} = return({self.callee_return})"


class ConstraintManager:
    """Efficient constraint storage and indexing.
    
    Provides fast lookup of constraints by variable and by type.
    """
    
    def __init__(self):
        """Initialize empty constraint manager."""
        self._constraints: Set[Constraint] = set()
        self._by_variable: Dict['Variable', Set[Constraint]] = defaultdict(set)
        self._by_type: Dict[Type[Constraint], Set[Constraint]] = defaultdict(set)
    
    def add(self, constraint: Constraint) -> bool:
        """Add constraint to manager.
        
        Args:
            constraint: Constraint to add
        
        Returns:
            True if constraint was new (not duplicate)
        """
        if constraint in self._constraints:
            return False
        
        self._constraints.add(constraint)
        
        # Index by variables
        if isinstance(constraint, LoadConstraint):
            self._by_variable[constraint.base].add(constraint)
        elif isinstance(constraint, StoreConstraint):
            self._by_variable[constraint.base].add(constraint)
        elif isinstance(constraint, CallConstraint):
            self._by_variable[constraint.callee].add(constraint)
            
        # Index by type
        self._by_type[type(constraint)].add(constraint)
        
        return True
    
    def remove(self, constraint: Constraint) -> bool:
        """Remove constraint from manager.
        
        Args:
            constraint: Constraint to remove
        
        Returns:
            True if constraint existed
        """
        if constraint not in self._constraints:
            return False
        
        self._constraints.remove(constraint)
        
        # Remove from variable index
        for var in constraint.variables():
            self._by_variable[var].discard(constraint)
        
        # Remove from type index
        self._by_type[type(constraint)].discard(constraint)
        
        return True
    
    def get_by_variable(self, var: 'Variable') -> Set[Constraint]:
        """Get all constraints involving variable.
        
        Args:
            var: Variable to query
        
        Returns:
            Set of constraints (empty if none)
        """
        return self._by_variable.get(var, set())
    
    def get_by_type(self, constraint_type: Type[Constraint]) -> Set[Constraint]:
        """Get all constraints of given type.
        
        Args:
            constraint_type: Type to query
        
        Returns:
            Set of constraints of that type
        """
        return self._by_type.get(constraint_type, set())
    
    def all(self) -> Set[Constraint]:
        """Get all constraints.
        
        Returns:
            Copy of all constraints
        """
        return self._constraints.copy()
    
    def __len__(self) -> int:
        """Get number of constraints."""
        return len(self._constraints)

