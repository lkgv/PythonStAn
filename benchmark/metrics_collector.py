#!/usr/bin/env python3
"""Metrics collection utility for k-CFA pointer analysis benchmarking.

This module provides utilities to collect, organize, and compute analysis metrics
from pointer analysis results.
"""

from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional, List
import json


@dataclass
class AnalysisMetrics:
    """Container for comprehensive analysis metrics.
    
    Attributes:
        policy: Context sensitivity policy used
        project: Project name (flask, werkzeug, etc.)
        
        # Status
        success: Whether analysis completed successfully
        error_message: Error message if analysis failed
        
        # Timing metrics (seconds)
        total_time: Total analysis time
        pipeline_time: Time to load and process with Pipeline
        analysis_time: Time for pointer analysis only
        
        # Memory metrics (MB)
        peak_memory: Peak memory usage during analysis
        final_memory: Memory usage at completion
        
        # Solver metrics
        solver_iterations: Number of solver iterations
        time_per_iteration: Average time per iteration (ms)
        constraints_generated: Total constraints generated
        
        # State size metrics
        num_variables: Number of variables tracked
        num_objects: Number of unique objects
        num_heap_locations: Number of heap locations
        num_contexts: Number of contexts created
        
        # Call graph metrics
        call_edges: Number of call edges discovered
        call_sites: Number of call sites analyzed
        avg_callees_per_site: Average callees per call site
        reachable_functions: Number of reachable functions
        
        # Points-to precision metrics
        avg_points_to_size: Average points-to set size
        singleton_points_to: Number of singleton points-to sets
        empty_variables: Number of variables with empty points-to
        large_points_to_sets: Number of variables with >10 objects
        max_points_to_size: Maximum points-to set size
        
        # Unknown tracking metrics
        total_unknowns: Total unknown resolution failures
        unknown_callee_empty: Unknown due to empty callee
        unknown_callee_non_callable: Unknown due to non-callable
        unknown_function_not_in_registry: Unknown due to missing function
        unknown_missing_dependencies: Unknown due to missing dependencies
        unknown_dynamic_attribute: Unknown due to dynamic attribute access
        unknown_field_load_empty: Unknown due to empty field
        unknown_import_not_found: Unknown due to import failure
        unknown_alloc_context_failure: Unknown due to context failure
        unknown_translation_error: Unknown due to translation error
        
        # Class hierarchy metrics
        num_classes: Number of classes discovered
        avg_mro_length: Average MRO length
    """
    
    policy: str
    project: str
    
    # Status
    success: bool = False
    error_message: Optional[str] = None
    
    # Timing
    total_time: float = 0.0
    pipeline_time: float = 0.0
    analysis_time: float = 0.0
    
    # Memory (MB)
    peak_memory: float = 0.0
    final_memory: float = 0.0
    
    # Solver
    solver_iterations: int = 0
    time_per_iteration: float = 0.0
    constraints_generated: int = 0
    
    # State size
    num_variables: int = 0
    num_objects: int = 0
    num_heap_locations: int = 0
    num_contexts: int = 0
    
    # Call graph
    call_edges: int = 0
    call_sites: int = 0
    avg_callees_per_site: float = 0.0
    reachable_functions: int = 0
    
    # Points-to precision
    avg_points_to_size: float = 0.0
    singleton_points_to: int = 0
    empty_variables: int = 0
    large_points_to_sets: int = 0
    max_points_to_size: int = 0
    
    # Unknown tracking
    total_unknowns: int = 0
    unknown_callee_empty: int = 0
    unknown_callee_non_callable: int = 0
    unknown_function_not_in_registry: int = 0
    unknown_missing_dependencies: int = 0
    unknown_dynamic_attribute: int = 0
    unknown_field_load_empty: int = 0
    unknown_import_not_found: int = 0
    unknown_alloc_context_failure: int = 0
    unknown_translation_error: int = 0
    
    # Class hierarchy
    num_classes: int = 0
    avg_mro_length: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)
    
    def to_json(self, filepath: str) -> None:
        """Save metrics to JSON file."""
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AnalysisMetrics':
        """Create from dictionary."""
        return cls(**data)
    
    @classmethod
    def from_json(cls, filepath: str) -> 'AnalysisMetrics':
        """Load metrics from JSON file."""
        with open(filepath, 'r') as f:
            data = json.load(f)
        return cls.from_dict(data)


class MetricsCollector:
    """Collects metrics from analysis results and statistics."""
    
    @staticmethod
    def collect_from_statistics(
        stats: Dict[str, Any],
        policy: str,
        project: str,
        timings: Dict[str, float],
        memory: Dict[str, float]
    ) -> AnalysisMetrics:
        """Collect metrics from analysis statistics.
        
        Args:
            stats: Statistics dictionary from result.get_statistics()
            policy: Context sensitivity policy
            project: Project name
            timings: Dictionary with timing information
            memory: Dictionary with memory information
        
        Returns:
            AnalysisMetrics object with collected metrics
        """
        metrics = AnalysisMetrics(policy=policy, project=project)
        metrics.success = True
        
        # Timing
        metrics.total_time = timings.get('total', 0.0)
        metrics.pipeline_time = timings.get('pipeline', 0.0)
        metrics.analysis_time = timings.get('analysis', 0.0)
        
        # Memory (convert bytes to MB)
        metrics.peak_memory = memory.get('peak', 0) / (1024 * 1024)
        metrics.final_memory = memory.get('final', 0) / (1024 * 1024)
        
        # State size (from state.get_statistics())
        metrics.num_variables = stats.get('num_variables', 0)
        metrics.num_objects = stats.get('num_objects', 0)
        metrics.num_heap_locations = stats.get('num_heap_locations', 0)
        
        # Solver metrics
        metrics.solver_iterations = stats.get('iterations', 0)
        metrics.constraints_generated = stats.get('constraints_added', 0)
        
        if metrics.solver_iterations > 0 and metrics.analysis_time > 0:
            metrics.time_per_iteration = (metrics.analysis_time * 1000) / metrics.solver_iterations
        
        # Context metrics
        metrics.num_contexts = stats.get('num_contexts', 0)
        
        # Call graph metrics
        metrics.call_edges = stats.get('num_call_edges', 0)
        metrics.call_sites = stats.get('call_sites', 0)
        metrics.reachable_functions = stats.get('reachable_functions', 0)
        
        if metrics.call_sites > 0:
            metrics.avg_callees_per_site = metrics.call_edges / metrics.call_sites
        
        # Points-to precision
        metrics.avg_points_to_size = stats.get('avg_points_to_size', 0.0)
        metrics.singleton_points_to = stats.get('singleton_points_to', 0)
        metrics.empty_variables = stats.get('empty_variables', 0)
        metrics.large_points_to_sets = stats.get('large_points_to_sets', 0)
        metrics.max_points_to_size = stats.get('max_points_to_size', 0)
        
        # Unknown tracking (from unknown_tracker.get_summary())
        metrics.total_unknowns = stats.get('total_unknowns', 0)
        metrics.unknown_callee_empty = stats.get('unknown_callee_empty', 0)
        metrics.unknown_callee_non_callable = stats.get('unknown_callee_non_callable', 0)
        metrics.unknown_function_not_in_registry = stats.get('unknown_function_not_in_registry', 0)
        metrics.unknown_missing_dependencies = stats.get('unknown_missing_dependencies', 0)
        metrics.unknown_dynamic_attribute = stats.get('unknown_dynamic_attribute', 0)
        metrics.unknown_field_load_empty = stats.get('unknown_field_load_empty', 0)
        metrics.unknown_import_not_found = stats.get('unknown_import_not_found', 0)
        metrics.unknown_alloc_context_failure = stats.get('unknown_alloc_context_failure', 0)
        metrics.unknown_translation_error = stats.get('unknown_translation_error', 0)
        
        # Class hierarchy
        metrics.num_classes = stats.get('num_classes', 0)
        metrics.avg_mro_length = stats.get('avg_mro_length', 0.0)
        
        return metrics
    
    @staticmethod
    def create_failed_metrics(
        policy: str,
        project: str,
        error_message: str,
        timings: Dict[str, float],
        memory: Dict[str, float]
    ) -> AnalysisMetrics:
        """Create metrics for failed analysis.
        
        Args:
            policy: Context sensitivity policy
            project: Project name
            error_message: Error message
            timings: Dictionary with timing information
            memory: Dictionary with memory information
        
        Returns:
            AnalysisMetrics object marked as failed
        """
        metrics = AnalysisMetrics(policy=policy, project=project)
        metrics.success = False
        metrics.error_message = error_message
        metrics.total_time = timings.get('total', 0.0)
        metrics.peak_memory = memory.get('peak', 0) / (1024 * 1024)
        return metrics


def save_results(results: List[AnalysisMetrics], filepath: str) -> None:
    """Save list of results to JSON file.
    
    Args:
        results: List of AnalysisMetrics objects
        filepath: Output file path
    """
    data = [r.to_dict() for r in results]
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)


def load_results(filepath: str) -> List[AnalysisMetrics]:
    """Load results from JSON file.
    
    Args:
        filepath: Input file path
    
    Returns:
        List of AnalysisMetrics objects
    """
    with open(filepath, 'r') as f:
        data = json.load(f)
    return [AnalysisMetrics.from_dict(item) for item in data]

