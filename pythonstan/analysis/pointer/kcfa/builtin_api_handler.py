"""Builtin function summaries for pointer analysis.

This module provides summaries for Python builtin functions that cannot be
analyzed directly. Summaries specify pointer effects conservatively.
"""

from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .constraints import Constraint
    from .variable import Variable
    from .context import AbstractContext
    from .config import Config

__all__ = ["BuiltinSummary", "BuiltinSummaryManager"]


class BuiltinSummary:
    """Summary of a builtin function's pointer effects.
    
    Summaries return constraints rather than directly mutating state,
    allowing them to be composed with the solver.
    """
    
    def __init__(self, name: str, conservative: bool = True):
        """Initialize builtin summary.
        
        Args:
            name: Function name
            conservative: If True, uses conservative approximation
        """
        self.name = name
        self.conservative = conservative
    
    def apply(
        self,
        target: Optional['Variable'],
        args: List['Variable'],
        context: 'AbstractContext'
    ) -> List['Constraint']:
        """Apply summary to generate constraints.
        
        Args:
            target: Target variable for return value (if any)
            args: Argument variables
            context: Current context
        
        Returns:
            List of constraints representing builtin's pointer effects
        """
        raise NotImplementedError(f"BuiltinSummary.apply not implemented for {self.name}")


class BuiltinSummaryManager:
    """Manager for builtin function summaries."""
    
    def __init__(self, config: 'Config'):
        """Initialize builtin summary manager.
        
        Args:
            config: Analysis configuration
        """
        self.config = config
        self._summaries = {}
        self._initialize_builtins()
    
    def get_summary(self, function_name: str) -> Optional[BuiltinSummary]:
        """Get summary for a function."""
        return self._summaries.get(function_name)
    
    def register_summary(self, summary: BuiltinSummary) -> None:
        """Register a function summary."""
        self._summaries[summary.name] = summary
    
    def has_summary(self, function_name: str) -> bool:
        """Check if function has a summary."""
        return function_name in self._summaries
    
    def _initialize_builtins(self) -> None:
        """Initialize summaries for Python builtin functions."""
        # Container constructors
        self.register_summary(ContainerSummary("list", "LIST"))
        self.register_summary(ContainerSummary("dict", "DICT"))
        self.register_summary(ContainerSummary("tuple", "TUPLE"))
        self.register_summary(ContainerSummary("set", "SET"))
        
        # Iterator functions - complex field manipulation
        self.register_summary(IteratorSummary("iter"))
        self.register_summary(NextSummary("next"))
        self.register_summary(EnumerateSummary("enumerate"))
        self.register_summary(ZipSummary("zip"))
        self.register_summary(ReversedSummary("reversed"))
        
        # Functional programming
        self.register_summary(MapSummary("map"))
        self.register_summary(FilterSummary("filter"))
        self.register_summary(SortedSummary("sorted"))
        
        # Type functions
        self.register_summary(ConstantSummary("isinstance", "OBJECT"))
        self.register_summary(ConstantSummary("issubclass", "OBJECT"))
        self.register_summary(ConstantSummary("type", "OBJECT"))
        self.register_summary(ConstantSummary("len", "OBJECT"))
        
        # Conversion functions
        self.register_summary(ConstantSummary("str", "OBJECT"))
        self.register_summary(ConstantSummary("int", "OBJECT"))
        self.register_summary(ConstantSummary("float", "OBJECT"))
        self.register_summary(ConstantSummary("bool", "OBJECT"))
        self.register_summary(ConstantSummary("bytes", "OBJECT"))
        
        # Introspection functions
        self.register_summary(GetAttrSummary("getattr"))
        self.register_summary(SetAttrSummary("setattr"))
        self.register_summary(ConstantSummary("hasattr", "OBJECT"))
        
        # I/O functions
        self.register_summary(ConstantSummary("open", "OBJECT"))
        self.register_summary(ConstantSummary("input", "OBJECT"))
        self.register_summary(VoidSummary("print"))
        
        # Numeric functions
        self.register_summary(ConstantSummary("abs", "OBJECT"))
        self.register_summary(ConstantSummary("round", "OBJECT"))
        self.register_summary(ConstantSummary("pow", "OBJECT"))
        self.register_summary(ConstantSummary("hash", "OBJECT"))
        self.register_summary(ConstantSummary("id", "OBJECT"))
        
        # Aggregate functions
        self.register_summary(ConstantSummary("min", "OBJECT"))
        self.register_summary(ConstantSummary("max", "OBJECT"))
        self.register_summary(ConstantSummary("sum", "OBJECT"))
        self.register_summary(ConstantSummary("any", "OBJECT"))
        self.register_summary(ConstantSummary("all", "OBJECT"))
        
        # Other common builtins
        self.register_summary(ConstantSummary("range", "OBJECT"))
        self.register_summary(ConstantSummary("super", "OBJECT"))
        
        # Decorator summaries
        self.register_summary(PropertySummary("property"))
        self.register_summary(StaticMethodSummary("staticmethod"))
        self.register_summary(ClassMethodSummary("classmethod"))


class ContainerSummary(BuiltinSummary):
    """Summary for container constructor builtins (list, dict, tuple, set)."""
    
    def __init__(self, name: str, kind: str):
        """Initialize container summary."""
        super().__init__(name, conservative=False)
        self.kind = kind
    
    def apply(
        self,
        target: Optional['Variable'],
        args: List['Variable'],
        context: 'AbstractContext'
    ) -> List['Constraint']:
        """Generate allocation constraint for new container."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        if not target:
            return []
        
        alloc_site = AllocSite(
            file="<builtin>",
            line=0,
            col=0,
            kind=getattr(AllocKind, self.kind),
            name=f"builtin_{self.name}"
        )
        
        return [AllocConstraint(target=target, alloc_site=alloc_site)]


class ConstantSummary(BuiltinSummary):
    """Summary for builtins that return constant-like objects."""
    
    def __init__(self, name: str, kind: str):
        """Initialize constant summary."""
        super().__init__(name, conservative=False)
        self.kind = kind
    
    def apply(
        self,
        target: Optional['Variable'],
        args: List['Variable'],
        context: 'AbstractContext'
    ) -> List['Constraint']:
        """Generate allocation constraint for return value."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        if not target:
            return []
        
        alloc_site = AllocSite(
            file="<builtin>",
            line=0,
            col=0,
            kind=AllocKind.OBJECT,
            name=f"builtin_{self.name}_result"
        )
        
        return [AllocConstraint(target=target, alloc_site=alloc_site)]


class IteratorSummary(BuiltinSummary):
    """Summary for iter() builtin - creates iterator pointing to container elements."""
    
    def __init__(self, name: str):
        super().__init__(name, conservative=False)
    
    def apply(
        self,
        target: Optional['Variable'],
        args: List['Variable'],
        context: 'AbstractContext'
    ) -> List['Constraint']:
        """Generate iterator object with elem field pointing to container elements."""
        from .constraints import AllocConstraint, LoadConstraint
        from .object import AllocSite, AllocKind
        from .heap_model import elem
        
        if not target or len(args) < 1:
            return []
        
        constraints = []
        
        # Create iterator object
        iter_alloc = AllocSite(
            file="<builtin>",
            line=0,
            col=0,
            kind=AllocKind.OBJECT,
            name="builtin_iter"
        )
        constraints.append(AllocConstraint(target=target, alloc_site=iter_alloc))
        
        # Link iterator to container elements
        container_var = args[0]
        constraints.append(LoadConstraint(
            base=container_var,
            field=elem(),
            target=target
        ))
        
        return constraints


class NextSummary(BuiltinSummary):
    """Summary for next() builtin - returns elements from iterator."""
    
    def __init__(self, name: str):
        super().__init__(name, conservative=False)
    
    def apply(
        self,
        target: Optional['Variable'],
        args: List['Variable'],
        context: 'AbstractContext'
    ) -> List['Constraint']:
        """Load elements from iterator."""
        from .constraints import LoadConstraint
        from .heap_model import elem
        
        if not target or len(args) < 1:
            return []
        
        # next(iterator) returns iterator.elem
        iterator_var = args[0]
        return [LoadConstraint(
            base=iterator_var,
            field=elem(),
            target=target
        )]


class EnumerateSummary(BuiltinSummary):
    """Summary for enumerate() builtin."""
    
    def __init__(self, name: str):
        super().__init__(name, conservative=False)
    
    def apply(
        self,
        target: Optional['Variable'],
        args: List['Variable'],
        context: 'AbstractContext'
    ) -> List['Constraint']:
        """Create enumerate iterator."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        if not target:
            return []
        
        enum_alloc = AllocSite(
            file="<builtin>",
            line=0,
            col=0,
            kind=AllocKind.OBJECT,
            name="builtin_enumerate"
        )
        
        return [AllocConstraint(target=target, alloc_site=enum_alloc)]


class ZipSummary(BuiltinSummary):
    """Summary for zip() builtin."""
    
    def __init__(self, name: str):
        super().__init__(name, conservative=False)
    
    def apply(
        self,
        target: Optional['Variable'],
        args: List['Variable'],
        context: 'AbstractContext'
    ) -> List['Constraint']:
        """Create zip iterator."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        if not target:
            return []
        
        zip_alloc = AllocSite(
            file="<builtin>",
            line=0,
            col=0,
            kind=AllocKind.OBJECT,
            name="builtin_zip"
        )
        
        return [AllocConstraint(target=target, alloc_site=zip_alloc)]


class ReversedSummary(BuiltinSummary):
    """Summary for reversed() builtin."""
    
    def __init__(self, name: str):
        super().__init__(name, conservative=False)
    
    def apply(
        self,
        target: Optional['Variable'],
        args: List['Variable'],
        context: 'AbstractContext'
    ) -> List['Constraint']:
        """Create reverse iterator pointing to sequence elements."""
        from .constraints import AllocConstraint, LoadConstraint
        from .object import AllocSite, AllocKind
        from .heap_model import elem
        
        if not target or len(args) < 1:
            return []
        
        constraints = []
        
        # Create reverse iterator
        rev_alloc = AllocSite(
            file="<builtin>",
            line=0,
            col=0,
            kind=AllocKind.OBJECT,
            name="builtin_reversed"
        )
        constraints.append(AllocConstraint(target=target, alloc_site=rev_alloc))
        
        # Link to sequence elements
        sequence_var = args[0]
        constraints.append(LoadConstraint(
            base=sequence_var,
            field=elem(),
            target=target
        ))
        
        return constraints


class MapSummary(BuiltinSummary):
    """Summary for map() builtin."""
    
    def __init__(self, name: str):
        super().__init__(name, conservative=False)
    
    def apply(
        self,
        target: Optional['Variable'],
        args: List['Variable'],
        context: 'AbstractContext'
    ) -> List['Constraint']:
        """Create map iterator."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        if not target:
            return []
        
        map_alloc = AllocSite(
            file="<builtin>",
            line=0,
            col=0,
            kind=AllocKind.OBJECT,
            name="builtin_map"
        )
        
        return [AllocConstraint(target=target, alloc_site=map_alloc)]


class FilterSummary(BuiltinSummary):
    """Summary for filter() builtin."""
    
    def __init__(self, name: str):
        super().__init__(name, conservative=False)
    
    def apply(
        self,
        target: Optional['Variable'],
        args: List['Variable'],
        context: 'AbstractContext'
    ) -> List['Constraint']:
        """Create filter iterator pointing to iterable elements."""
        from .constraints import AllocConstraint, LoadConstraint
        from .object import AllocSite, AllocKind
        from .heap_model import elem
        
        if not target or len(args) < 2:
            return []
        
        constraints = []
        
        # Create filter iterator
        filter_alloc = AllocSite(
            file="<builtin>",
            line=0,
            col=0,
            kind=AllocKind.OBJECT,
            name="builtin_filter"
        )
        constraints.append(AllocConstraint(target=target, alloc_site=filter_alloc))
        
        # Elements come from iterable (second argument)
        iterable_var = args[1]
        constraints.append(LoadConstraint(
            base=iterable_var,
            field=elem(),
            target=target
        ))
        
        return constraints


class SortedSummary(BuiltinSummary):
    """Summary for sorted() builtin."""
    
    def __init__(self, name: str):
        super().__init__(name, conservative=False)
    
    def apply(
        self,
        target: Optional['Variable'],
        args: List['Variable'],
        context: 'AbstractContext'
    ) -> List['Constraint']:
        """Create list with elements from iterable."""
        from .constraints import AllocConstraint, LoadConstraint
        from .object import AllocSite, AllocKind
        from .heap_model import elem
        
        if not target or len(args) < 1:
            return []
        
        constraints = []
        
        # Create list object
        list_alloc = AllocSite(
            file="<builtin>",
            line=0,
            col=0,
            kind=AllocKind.LIST,
            name="builtin_sorted"
        )
        constraints.append(AllocConstraint(target=target, alloc_site=list_alloc))
        
        # Copy elements from iterable to list
        iterable_var = args[0]
        constraints.append(LoadConstraint(
            base=iterable_var,
            field=elem(),
            target=target
        ))
        
        return constraints


class GetAttrSummary(BuiltinSummary):
    """Summary for getattr() builtin."""
    
    def __init__(self, name: str):
        super().__init__(name, conservative=True)
    
    def apply(
        self,
        target: Optional['Variable'],
        args: List['Variable'],
        context: 'AbstractContext'
    ) -> List['Constraint']:
        """Conservative: return new object."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        if not target or len(args) < 2:
            return []
        
        getattr_alloc = AllocSite(
            file="<builtin>",
            line=0,
            col=0,
            kind=AllocKind.OBJECT,
            name="builtin_getattr_result"
        )
        
        return [AllocConstraint(target=target, alloc_site=getattr_alloc)]


class SetAttrSummary(BuiltinSummary):
    """Summary for setattr() builtin."""
    
    def __init__(self, name: str):
        super().__init__(name, conservative=True)
    
    def apply(
        self,
        target: Optional['Variable'],
        args: List['Variable'],
        context: 'AbstractContext'
    ) -> List['Constraint']:
        """No constraints - side effect only."""
        return []


class VoidSummary(BuiltinSummary):
    """Summary for functions with no return value."""
    
    def __init__(self, name: str):
        super().__init__(name, conservative=False)
    
    def apply(
        self,
        target: Optional['Variable'],
        args: List['Variable'],
        context: 'AbstractContext'
    ) -> List['Constraint']:
        """No constraints - void function."""
        return []


class PropertySummary(BuiltinSummary):
    """Summary for @property decorator.
    
    property(func) returns a property descriptor object that wraps the function.
    """
    
    def __init__(self, name: str):
        super().__init__(name, conservative=False)
    
    def apply(
        self,
        target: Optional['Variable'],
        args: List['Variable'],
        context: 'AbstractContext'
    ) -> List['Constraint']:
        """Property decorator returns property descriptor."""
        from .constraints import AllocConstraint, StoreConstraint
        from .object import AllocSite, AllocKind
        from .heap_model import attr
        
        if not target or len(args) < 1:
            return []
        
        constraints = []
        
        # Create property descriptor object
        prop_alloc = AllocSite(
            file="<builtin>",
            line=0,
            col=0,
            kind=AllocKind.OBJECT,
            name="property_descriptor"
        )
        constraints.append(AllocConstraint(target=target, alloc_site=prop_alloc))
        
        # Store getter function in fget field
        getter_var = args[0]
        constraints.append(StoreConstraint(
            base=target,
            field=attr("fget"),
            source=getter_var
        ))
        
        return constraints


class StaticMethodSummary(BuiltinSummary):
    """Summary for @staticmethod decorator.
    
    staticmethod(func) returns a static method descriptor that wraps the function.
    """
    
    def __init__(self, name: str):
        super().__init__(name, conservative=False)
    
    def apply(
        self,
        target: Optional['Variable'],
        args: List['Variable'],
        context: 'AbstractContext'
    ) -> List['Constraint']:
        """Static method decorator returns descriptor."""
        from .constraints import AllocConstraint, StoreConstraint
        from .object import AllocSite, AllocKind
        from .heap_model import attr
        
        if not target or len(args) < 1:
            return []
        
        constraints = []
        
        # Create static method descriptor object
        sm_alloc = AllocSite(
            file="<builtin>",
            line=0,
            col=0,
            kind=AllocKind.OBJECT,
            name="staticmethod_descriptor"
        )
        constraints.append(AllocConstraint(target=target, alloc_site=sm_alloc))
        
        # Store wrapped function
        func_var = args[0]
        constraints.append(StoreConstraint(
            base=target,
            field=attr("__func__"),
            source=func_var
        ))
        
        return constraints


class ClassMethodSummary(BuiltinSummary):
    """Summary for @classmethod decorator.
    
    classmethod(func) returns a class method descriptor that wraps the function.
    """
    
    def __init__(self, name: str):
        super().__init__(name, conservative=False)
    
    def apply(
        self,
        target: Optional['Variable'],
        args: List['Variable'],
        context: 'AbstractContext'
    ) -> List['Constraint']:
        """Class method decorator returns descriptor."""
        from .constraints import AllocConstraint, StoreConstraint
        from .object import AllocSite, AllocKind
        from .heap_model import attr
        
        if not target or len(args) < 1:
            return []
        
        constraints = []
        
        # Create class method descriptor object
        cm_alloc = AllocSite(
            file="<builtin>",
            line=0,
            col=0,
            kind=AllocKind.OBJECT,
            name="classmethod_descriptor"
        )
        constraints.append(AllocConstraint(target=target, alloc_site=cm_alloc))
        
        # Store wrapped function
        func_var = args[0]
        constraints.append(StoreConstraint(
            base=target,
            field=attr("__func__"),
            source=func_var
        ))
        
        return constraints
