"""Program variables for pointer analysis.

This module defines the representation of program variables in the k-CFA pointer analysis.
Variables are context-sensitive and scoped.
"""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .context import AbstractContext

__all__ = ["VariableKind", "Scope", "Variable"]


class VariableKind(Enum):
    """Kinds of variables in Python programs."""
    
    LOCAL = "local"
    PARAMETER = "parameter"
    GLOBAL = "global"
    TEMPORARY = "temporary"
    CONSTANT = "constant"


@dataclass(frozen=True)
class Scope:
    """Function or module scope for variables.
    
    Attributes:
        name: Qualified scope name (e.g., "module.Class.method")
        kind: Type of scope
    """
    
    name: str
    kind: Literal["function", "method", "module", "class"]
    
    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class Variable:
    """Program variable with scope and context.
    
    A variable represents a program location that holds references to objects.
    Variables are context-qualified to enable context-sensitive analysis.
    
    Attributes:
        name: Variable name (unqualified)
        scope: Enclosing scope
        context: Analysis context
        kind: Variable kind (local, parameter, etc.)
    """
    
    name: str
    scope: Scope
    context: 'AbstractContext'
    kind: VariableKind = VariableKind.LOCAL
    
    def __str__(self) -> str:
        """String representation: scope::name@context"""
        return f"{self.scope.name}::{self.name}@{self.context}"
    
    @property
    def is_temporary(self) -> bool:
        """Check if this is a temporary variable."""
        return self.kind == VariableKind.TEMPORARY
    
    @property
    def is_global(self) -> bool:
        """Check if this is a global variable."""
        return self.kind == VariableKind.GLOBAL


class VariableFactory:
    """Helper for creating variables from IR information.
    
    This factory maintains consistent variable creation across the analysis.
    It is stateless and simply provides a convenient interface for Variable creation.
    """
    
    def __init__(self):
        """Initialize variable factory.
        
        The factory is stateless - no initialization needed.
        """
        pass
    
    def make_variable(
        self,
        name: str,
        scope: Scope,
        context: 'AbstractContext',
        kind: VariableKind = VariableKind.LOCAL
    ) -> Variable:
        """Create variable with given attributes.
        
        Args:
            name: Variable name
            scope: Enclosing scope
            context: Analysis context
            kind: Variable kind
        
        Returns:
            Created variable
        """
        return Variable(name=name, scope=scope, context=context, kind=kind)

