"""Configuration for k-CFA pointer analysis with multiple context sensitivity policies.

This module defines the configuration parameters for the pointer analysis.
The defaults balance precision and performance for typical Python programs.

Key configuration options:
- context_policy: Strategy for context sensitivity (2-cfa, 1-obj, 1-type, etc.)
- k: Call string length (for backward compatibility, overridden by context_policy)
- obj_depth: Object sensitivity depth (for backward compatibility)
- attr-name field sensitivity: Distinguishes different attributes but treats
  container elements uniformly
- Container defaults: Lists/sets/tuples are element-insensitive, dicts are value-insensitive
"""

from typing import Dict, Literal, Optional

__all__ = ["KCFAConfig"]


class KCFAConfig:
    """Configuration for pointer analysis with multiple context sensitivity policies.
    
    Attributes:
        context_policy: Context sensitivity policy string (e.g., "2-cfa", "1-obj", "1-type")
            Available policies:
            - "0-cfa": Context-insensitive (baseline)
            - "1-cfa", "2-cfa", "3-cfa": Call-string sensitivity
            - "1-obj", "2-obj", "3-obj": Object sensitivity
            - "1-type", "2-type", "3-type": Type sensitivity
            - "1-rcv", "2-rcv", "3-rcv": Receiver-object sensitivity
            - "1c1o", "2c1o", "1c2o": Hybrid (call + object)
        k: Maximum call string length (deprecated, use context_policy instead)
        obj_depth: Object sensitivity depth (deprecated, use context_policy instead)
        field_sensitivity_mode: How to handle field sensitivity:
            - "attr-name": Distinguish different attributes by name
            - "field-insensitive": Treat all fields uniformly
        containers: Mapping from container type to field treatment:
            - "elem": All elements share a single abstract field
            - "value": All values share a single abstract field
        timeouts: Analysis timeout in seconds (None for no timeout)
        max_heap_widening: Maximum heap size before widening (None for no limit)
        verbose: Enable detailed logging during analysis
        build_class_hierarchy: Build and populate class hierarchy during analysis
        use_mro: Use Method Resolution Order (C3 linearization) for attribute resolution
    """
    
    def __init__(
        self,
        context_policy: Optional[str] = None,
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
        """Initialize pointer analysis configuration.
        
        Args:
            context_policy: Context sensitivity policy string (None for default based on k)
            k: Call string length (backward compatibility, overridden by context_policy)
            obj_depth: Object sensitivity depth (backward compatibility)
            field_sensitivity_mode: Strategy for field sensitivity
            containers: Container type to field treatment mapping
            timeouts: Analysis timeout in seconds
            max_heap_widening: Heap size limit before widening
            verbose: Enable verbose logging
            build_class_hierarchy: Build and populate class hierarchy during analysis
            use_mro: Use Method Resolution Order (C3 linearization) for attribute resolution
        """
        # Set context policy (with backward compatibility)
        if context_policy is None:
            # Backward compatibility: derive from k
            if k == 0:
                context_policy = "0-cfa"
            elif k == 1:
                context_policy = "1-cfa"
            elif k == 2:
                context_policy = "2-cfa"
            elif k == 3:
                context_policy = "3-cfa"
            else:
                context_policy = "2-cfa"  # Default
        
        self.context_policy = context_policy
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
        return (f"KCFAConfig(policy={self.context_policy}, "
                f"field_mode={self.field_sensitivity_mode}, "
                f"verbose={self.verbose})")