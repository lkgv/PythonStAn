"""Configuration for k-CFA pointer analysis with 2-object sensitivity.

This module defines the configuration parameters for the k-CFA pointer analysis
with 2-object sensitivity. The defaults balance precision and performance for
typical Python programs.

Key tradeoffs:
- k=2: Provides good precision for call chains while keeping context finite
- obj_depth=2: Tracks allocation contexts for receivers up to depth 2
- attr-name field sensitivity: Distinguishes different attributes but treats
  container elements uniformly
- Container defaults: Lists/sets/tuples are element-insensitive, dicts are value-insensitive
"""

from typing import Dict, Literal, Optional

__all__ = ["KCFAConfig"]


class KCFAConfig:
    """Configuration for k-CFA pointer analysis with 2-object sensitivity.
    
    Attributes:
        k: Maximum call string length for context sensitivity (default: 2)
        obj_depth: Maximum depth for object sensitivity on receivers (default: 2)
        field_sensitivity_mode: How to handle field sensitivity:
            - "attr-name": Distinguish different attributes by name
            - "field-insensitive": Treat all fields uniformly
        containers: Mapping from container type to field treatment:
            - "elem": All elements share a single abstract field
            - "value": All values share a single abstract field
        timeouts: Analysis timeout in seconds (None for no timeout)
        max_heap_widening: Maximum heap size before widening (None for no limit)
        verbose: Enable detailed logging during analysis
    """
    
    def __init__(
        self,
        k: int = 2,
        obj_depth: int = 2,
        field_sensitivity_mode: Literal["attr-name", "field-insensitive"] = "attr-name",
        containers: Optional[Dict[str, Literal["elem", "value"]]] = None,
        timeouts: Optional[float] = None,
        max_heap_widening: Optional[int] = None,
        verbose: bool = False,
        build_class_hierarchy: bool = True,
        use_mro: bool = True
    ):
        """Initialize k-CFA configuration.
        
        Args:
            k: Call string length for context sensitivity
            obj_depth: Object sensitivity depth for receiver objects
            field_sensitivity_mode: Strategy for field sensitivity
            containers: Container type to field treatment mapping
            timeouts: Analysis timeout in seconds
            max_heap_widening: Heap size limit before widening
            verbose: Enable verbose logging
            build_class_hierarchy: Build and populate class hierarchy during analysis
            use_mro: Use Method Resolution Order (C3 linearization) for attribute resolution
        """
        self.k = k
        self.obj_depth = obj_depth
        self.field_sensitivity_mode = field_sensitivity_mode
        self.containers = containers or {
            "list": "elem",
            "set": "elem", 
            "tuple": "elem",
            "dict": "value"
        }
        self.timeouts = timeouts
        self.max_heap_widening = max_heap_widening
        self.verbose = verbose
        self.build_class_hierarchy = build_class_hierarchy
        self.use_mro = use_mro
        
    def __repr__(self) -> str:
        return (f"KCFAConfig(k={self.k}, obj_depth={self.obj_depth}, "
                f"field_mode={self.field_sensitivity_mode}, "
                f"verbose={self.verbose})")