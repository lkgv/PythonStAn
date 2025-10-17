"""Name resolution for temporary variables in pointer analysis.

This module provides utilities to resolve temporary variable names (like tmp_10)
to their actual object identities by tracking assignment chains and allocation sites.

The goal is to make analysis results more human-readable by annotating temporary
variables with their actual names.
"""

from typing import Dict, Optional, Tuple, Set

__all__ = ["NameResolver"]


class NameResolver:
    """Tracks assignment chains to resolve temporary variables to actual names.
    
    PythonStAn IR uses temporary variables extensively:
    - tmp_10 = Dog("Buddy")  # tmp_10 should be known as Dog instance
    - tmp_20 = Cat          # tmp_20 should be known as Cat class
    - tmp_30 = tmp_10.bark  # tmp_30 should be known as bound method
    
    This class tracks assignments and allocations to maintain mappings from
    temporary variables to actual identities.
    """
    
    def __init__(self):
        """Initialize name resolver."""
        # Map: (var_name, context_str) -> actual_name
        self._name_map: Dict[Tuple[str, str], str] = {}
        
        # Map: allocation_site -> source_name
        # Tracks what name was associated with each allocation
        self._alloc_names: Dict[str, str] = {}
        
        # Map: (var_name, context_str) -> allocation_site
        # Tracks what allocation site a variable points to
        self._var_allocs: Dict[Tuple[str, str], str] = {}
        
    def record_assignment(self, target: str, source: str, context_str: str = "[]"):
        """Record assignment: target = source.
        
        Propagates actual names through assignment chains.
        
        Args:
            target: Target variable name
            source: Source variable name
            context_str: String representation of context
        """
        # If source has a known name, propagate it
        source_key = (source, context_str)
        target_key = (target, context_str)
        
        if source_key in self._name_map:
            # Source has an actual name - propagate to target
            actual = self._name_map[source_key]
            self._name_map[target_key] = actual
        elif not source.startswith('tmp_'):
            # Source is not a temp variable - use it as actual name
            self._name_map[target_key] = source
            
        # Propagate allocation site info
        if source_key in self._var_allocs:
            alloc_site = self._var_allocs[source_key]
            self._var_allocs[target_key] = alloc_site
            
    def record_allocation(
        self, 
        target: str, 
        alloc_site: str, 
        alloc_type: str,
        context_str: str = "[]"
    ):
        """Record allocation: target = new Object().
        
        Associates allocation sites with variable names and types.
        
        Args:
            target: Target variable receiving allocated object
            alloc_site: Allocation site ID
            alloc_type: Type of allocation (obj, func, class, etc.)
            context_str: String representation of context
        """
        target_key = (target, context_str)
        
        # Track allocation site for this variable
        self._var_allocs[target_key] = alloc_site
        
        # Extract meaningful name from allocation site or target
        if alloc_type in ('func', 'class'):
            # For functions/classes, target is often the actual name
            if not target.startswith('tmp_'):
                self._name_map[target_key] = target
                self._alloc_names[alloc_site] = target
            else:
                # Try to extract name from allocation site
                # Format: "file.py:line:col:type"
                # We'll mark it as an anonymous function/class for now
                self._name_map[target_key] = f"<anonymous_{alloc_type}>"
        elif alloc_type == 'obj':
            # For objects, we'd need constructor info to know the class
            # This is tracked separately through constructor calls
            pass
        else:
            # For other types (list, dict, etc.), use type as name
            self._name_map[target_key] = f"<{alloc_type}>"
            
    def record_constructor_call(
        self,
        target: str,
        constructor_name: str,
        context_str: str = "[]"
    ):
        """Record constructor call: target = ClassName(args).
        
        Associates the constructed object with its class name.
        
        Args:
            target: Variable receiving the new instance
            constructor_name: Name of the class/constructor
            context_str: String representation of context
        """
        target_key = (target, context_str)
        
        # Record that target is an instance of constructor_name
        if constructor_name and not constructor_name.startswith('tmp_'):
            instance_name = f"{constructor_name}_instance"
            self._name_map[target_key] = instance_name
            
    def record_attribute_load(
        self,
        target: str,
        source: str,
        attr_name: str,
        context_str: str = "[]"
    ):
        """Record attribute load: target = source.attr_name.
        
        Args:
            target: Target variable
            source: Source object
            attr_name: Attribute name
            context_str: String representation of context
        """
        target_key = (target, context_str)
        source_key = (source, context_str)
        
        # Build descriptive name
        if source_key in self._name_map:
            source_actual = self._name_map[source_key]
            self._name_map[target_key] = f"{source_actual}.{attr_name}"
        elif not source.startswith('tmp_'):
            self._name_map[target_key] = f"{source}.{attr_name}"
        else:
            # Source name unknown - just use attribute name
            self._name_map[target_key] = attr_name
            
    def resolve(self, var_name: str, context_str: str = "[]") -> Optional[str]:
        """Resolve variable to its actual name.
        
        Args:
            var_name: Variable name to resolve
            context_str: String representation of context
            
        Returns:
            Actual name if known, None otherwise
        """
        key = (var_name, context_str)
        return self._name_map.get(key)
        
    def get_allocation_site(self, var_name: str, context_str: str = "[]") -> Optional[str]:
        """Get allocation site for a variable.
        
        Args:
            var_name: Variable name
            context_str: String representation of context
            
        Returns:
            Allocation site ID if known, None otherwise
        """
        key = (var_name, context_str)
        return self._var_allocs.get(key)
        
    def get_allocation_name(self, alloc_site: str) -> Optional[str]:
        """Get the name associated with an allocation site.
        
        Args:
            alloc_site: Allocation site ID
            
        Returns:
            Name associated with the allocation, None if unknown
        """
        return self._alloc_names.get(alloc_site)
        
    def get_all_resolved(self) -> Dict[str, str]:
        """Get all resolved names.
        
        Returns:
            Dictionary mapping "var@context" -> actual_name
        """
        result = {}
        for (var, ctx), name in self._name_map.items():
            if var != name and var.startswith('tmp_'):
                # Only include temp vars that resolved to something different
                key = f"{var}@{ctx}"
                result[key] = name
        return result

