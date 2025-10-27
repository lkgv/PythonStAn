"""Configuration for pointer analysis.

This module defines the configuration options for k-CFA pointer analysis.
"""

from dataclasses import dataclass
from typing import Optional, List

__all__ = ["Config"]


@dataclass
class Config:
    """Analysis configuration.
    
    Attributes:
        context_policy: Context sensitivity policy string
        max_iterations: Maximum solver iterations
        max_points_to_size: Widening threshold for points-to sets
        verbose: Enable verbose logging
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        enable_instrumentation: Enable performance instrumentation
        entry_points: Entry point functions
        build_class_hierarchy: Build class hierarchy and compute MRO
        use_mro_resolution: Use MRO for attribute resolution
        project_path: Project root path for module resolution
        library_paths: External library paths for import resolution
        max_import_depth: Maximum depth for transitive import analysis (0 = no imports, -1 = unlimited)
        track_unknowns: Enable tracking of unknown/unresolved calls and allocations
        log_unknown_details: If True, logs each unknown immediately (verbose mode required)
    """
    
    context_policy: str = "2-cfa"
    max_iterations: int = 10000
    max_points_to_size: Optional[int] = None
    verbose: bool = False
    log_level: str = "INFO"
    enable_instrumentation: bool = False
    entry_points: Optional[List[str]] = None
    build_class_hierarchy: bool = True
    use_mro_resolution: bool = True
    project_path: Optional[str] = None
    library_paths: Optional[List[str]] = None
    max_import_depth: int = 2
    track_unknowns: bool = True
    log_unknown_details: bool = False
    
    def __post_init__(self):
        """Validate configuration."""
        if self.max_iterations <= 0:
            raise ValueError("max_iterations must be positive")
        
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ValueError(f"Invalid log level: {self.log_level}")
        
        if self.max_points_to_size is not None and self.max_points_to_size <= 0:
            raise ValueError("max_points_to_size must be positive if set")
        
        if self.max_import_depth < -1:
            raise ValueError("max_import_depth must be >= -1 (-1 = unlimited, 0 = no imports)")

