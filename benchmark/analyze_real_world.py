#!/usr/bin/env python3
"""
Comprehensive 2-CFA Pointer Analysis for Real-World Projects.

This script analyzes Flask and Werkzeug with detailed metrics collection,
validation, and reporting for the k-CFA pointer analysis implementation.
"""

import os
import sys
import json
import time
import traceback
import tracemalloc
from pathlib import Path
from typing import Dict, List, Set, Any, Optional, Tuple
from dataclasses import dataclass, asdict, field
from datetime import datetime
from collections import defaultdict
import argparse

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pythonstan.world.pipeline import Pipeline
from pythonstan.analysis.pointer.kcfa2 import KCFA2PointerAnalysis, KCFAConfig


@dataclass
class ModuleAnalysisResult:
    """Result of analyzing a single module."""
    module_name: str
    success: bool
    duration: float
    error: Optional[str] = None
    error_type: Optional[str] = None
    lines_of_code: int = 0
    functions_analyzed: int = 0


@dataclass
class PointsToMetrics:
    """Points-to set metrics for precision measurement."""
    total_variables: int = 0
    total_points_to_sets: int = 0
    singleton_sets: int = 0
    empty_sets: int = 0
    avg_set_size: float = 0.0
    max_set_size: int = 0
    median_set_size: float = 0.0
    set_size_distribution: Dict[str, int] = None  # "1-5": count, "6-10": count, etc.
    
    def __post_init__(self):
        if self.set_size_distribution is None:
            self.set_size_distribution = {}


@dataclass
class FunctionMetrics:
    """Per-function analysis metrics."""
    function_name: str
    out_degree: int = 0  # Number of outgoing calls
    in_degree: int = 0   # Number of incoming calls (callers)
    objects_in_scope: int = 0
    objects_by_type: Dict[str, int] = None  # Type breakdown: obj, list, dict, func, class
    variables_tracked: int = 0
    precision: float = 0.0  # Singleton ratio for this function's variables
    contexts_seen: int = 0  # Number of contexts this function was analyzed in
    
    def __post_init__(self):
        if self.objects_by_type is None:
            self.objects_by_type = {}


@dataclass
class CallGraphMetrics:
    """Call graph metrics."""
    total_functions: int = 0
    total_call_sites: int = 0
    total_edges: int = 0
    polymorphic_call_sites: int = 0
    unreachable_functions: int = 0
    avg_out_degree: float = 0.0
    max_out_degree: int = 0
    call_resolution_rate: float = 0.0  # % of calls successfully resolved


@dataclass
class ClassHierarchyMetrics:
    """Class hierarchy and MRO metrics."""
    total_classes: int = 0
    classes_with_mro: int = 0
    diamond_patterns: int = 0
    max_mro_length: int = 0
    avg_mro_length: float = 0.0


@dataclass
class PerformanceMetrics:
    """Performance metrics."""
    total_duration: float = 0.0
    analysis_time: float = 0.0
    ir_construction_time: float = 0.0
    peak_memory_mb: float = 0.0
    peak_memory_current_mb: float = 0.0
    iterations_to_converge: int = 0
    throughput_loc_per_sec: float = 0.0
    per_module_time: Dict[str, float] = field(default_factory=dict)


@dataclass
class ObjectMetrics:
    """Object allocation and tracking metrics."""
    total_objects_created: int = 0
    avg_objects_per_variable: float = 0.0
    vars_with_no_objects: int = 0
    vars_with_singleton: int = 0
    vars_with_multiple: int = 0
    object_types: Dict[str, int] = field(default_factory=dict)  # Type -> count


@dataclass
class ProjectAnalysisReport:
    """Complete analysis report for a project."""
    project_name: str
    timestamp: str
    config: Dict[str, Any]
    
    # Module-level results
    modules_analyzed: int = 0
    modules_succeeded: int = 0
    modules_failed: int = 0
    module_results: List[ModuleAnalysisResult] = None
    
    # Aggregate metrics
    points_to_metrics: Optional[PointsToMetrics] = None
    call_graph_metrics: Optional[CallGraphMetrics] = None
    class_hierarchy_metrics: Optional[ClassHierarchyMetrics] = None
    performance_metrics: Optional[PerformanceMetrics] = None
    object_metrics: Optional[ObjectMetrics] = None
    
    # Per-function metrics
    function_metrics: Dict[str, FunctionMetrics] = None  # function_name -> metrics
    
    # Error tracking
    error_categories: Dict[str, List[str]] = None  # error_type -> [module_names]
    
    def __post_init__(self):
        if self.module_results is None:
            self.module_results = []
        if self.error_categories is None:
            self.error_categories = {}
        if self.function_metrics is None:
            self.function_metrics = {}


class RealWorldAnalyzer:
    """Analyzer for real-world Python projects."""
    
    def __init__(self, project_path: Path, project_name: str, config: KCFAConfig):
        self.project_path = project_path
        self.project_name = project_name
        self.config = config
        self.report = ProjectAnalysisReport(
            project_name=project_name,
            timestamp=datetime.now().isoformat(),
            config=self._serialize_config(config)
        )
        
    def _serialize_config(self, config: KCFAConfig) -> Dict[str, Any]:
        """Serialize config for JSON output."""
        return {
            "k": config.k,
            "obj_depth": config.obj_depth,
            "field_sensitivity_mode": config.field_sensitivity_mode,
            "build_class_hierarchy": config.build_class_hierarchy,
            "use_mro": config.use_mro,
            "verbose": config.verbose
        }
    
    def find_python_modules(self, src_dir: Path, include_deps: bool = False, 
                           dep_names: Optional[List[str]] = None) -> List[Path]:
        """Find all Python modules in source directory.
        
        Args:
            src_dir: Source directory to search
            include_deps: Whether to include dependency libraries
            dep_names: List of dependency package names to include (if include_deps=True)
        
        Returns:
            List of Python module paths
        """
        modules = []
        
        # Find project modules
        for path in src_dir.rglob("*.py"):
            if not any(part.startswith('.') or part == '__pycache__' for part in path.parts):
                modules.append(path)
        
        # Find dependency modules (if requested)
        if include_deps and dep_names:
            # Look for .venv or venv directories
            venv_candidates = [
                src_dir.parent.parent / ".venv",
                src_dir.parent.parent / "venv",
                self.project_path.parent / ".venv",
                self.project_path.parent / "venv"
            ]
            
            for venv_dir in venv_candidates:
                if not venv_dir.exists():
                    continue
                
                # Find site-packages (try different Python versions)
                site_packages_candidates = list(venv_dir.glob("lib/python*/site-packages"))
                if not site_packages_candidates:
                    continue
                
                site_packages = site_packages_candidates[0]
                
                if self.config.verbose:
                    print(f"\nSearching for dependencies in: {site_packages}")
                
                for dep_name in dep_names:
                    dep_path = site_packages / dep_name
                    if dep_path.exists() and dep_path.is_dir():
                        dep_modules = []
                        for path in dep_path.rglob("*.py"):
                            # Skip test directories and examples in dependencies
                            # Only check path parts AFTER site-packages to avoid filtering .venv
                            try:
                                relative_parts = path.relative_to(dep_path).parts
                                # Skip if path contains test/examples directories or __pycache__
                                # But allow all .py files including those starting with _
                                if not any(part == '__pycache__' or 
                                         part == 'tests' or part == 'test' or part == 'examples' 
                                         for part in relative_parts):
                                    dep_modules.append(path)
                            except ValueError:
                                # If relative_to fails, skip
                                continue
                        
                        if self.config.verbose:
                            print(f"  Found {len(dep_modules)} modules in {dep_name}")
                        
                        modules.extend(dep_modules)
                
                # Only use first found venv
                break
        
        return sorted(modules)
    
    def count_lines_of_code(self, file_path: Path) -> int:
        """Count non-empty, non-comment lines in a Python file."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            # Simple heuristic: count non-empty lines that aren't just comments
            count = 0
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith('#'):
                    count += 1
            return count
        except Exception:
            return 0
    
    def analyze_module(self, module_path: Path, debug: bool = False, 
                      unified_functions: Optional[Dict[str, Any]] = None) -> Tuple[ModuleAnalysisResult, Optional[KCFA2PointerAnalysis]]:
        """Analyze a single Python module.
        
        Args:
            unified_functions: Global function table for cross-module resolution (two-pass mode)
        """
        module_name = module_path.stem
        # Try to get relative path, but handle dependencies outside project
        try:
            rel_module_name = str(module_path.relative_to(self.project_path))
        except ValueError:
            # Dependency module - use a shorter name
            rel_module_name = str(Path(*module_path.parts[-3:]))
        
        result = ModuleAnalysisResult(
            module_name=rel_module_name,
            success=False,
            duration=0.0,
            lines_of_code=self.count_lines_of_code(module_path)
        )
        
        analysis = None
        start_time = time.time()
        
        try:
            # Create pipeline configuration with lazy IR construction
            pipeline_config = {
                "filename": str(module_path),
                "project_path": str(self.project_path),
                "library_paths": self._get_library_paths(),
                "analysis": [],  # We'll run pointer analysis directly
                "lazy_ir_construction": True  # Only process target module, skip imports
            }
            
            # Run IR construction
            pipeline = Pipeline(config=pipeline_config)
            pipeline.run()
            
            # Get IR module and scope manager
            world = pipeline.get_world()
            ir_module = world.entry_module
            scope_manager = world.scope_manager
            
            if debug or self.config.verbose:
                print(f"\n{'='*60}")
                print(f"DEBUG: Module {module_name}")
                print(f"{'='*60}")
                print(f"IR module type: {type(ir_module)}")
                print(f"IR module name: {getattr(ir_module, 'name', 'NO NAME')}")
                print(f"IR module qualname: {getattr(ir_module, 'qualname', 'NO QUALNAME')}")
            
            # Get functions and classes from scope manager (CORRECT WAY)
            functions = []
            classes = []
            functions_with_ir = []
            if hasattr(scope_manager, 'get_subscopes'):
                subscopes = scope_manager.get_subscopes(ir_module)
                from pythonstan.ir import IRFunc, IRClass
                functions = [scope for scope in subscopes if isinstance(scope, IRFunc)]
                classes = [scope for scope in subscopes if isinstance(scope, IRClass)]
                
                # Extract methods from classes (recursive subscope extraction)
                class_methods = []
                for cls in classes:
                    cls_subscopes = scope_manager.get_subscopes(cls)
                    methods = [scope for scope in cls_subscopes if isinstance(scope, IRFunc)]
                    class_methods.extend(methods)
                
                # Combine module-level functions and class methods
                all_functions = functions + class_methods
                result.functions_analyzed = len(all_functions)
                
                if debug or self.config.verbose:
                    print(f"Subscopes found: {len(subscopes)}")
                    print(f"Module-level functions found: {len(functions)}")
                    print(f"Classes found: {len(classes)}")
                    print(f"Class methods found: {len(class_methods)}")
                    print(f"Total functions (with methods): {len(all_functions)}")
                    for i, func in enumerate(functions[:3]):
                        print(f"  Function {i+1}: {func.name} ({func.qualname})")
                    if len(functions) > 3:
                        print(f"  ... and {len(functions)-3} more module functions")
                    for i, cls in enumerate(classes[:3]):
                        print(f"  Class {i+1}: {cls.name} ({cls.qualname})")
                        cls_methods = [m for m in class_methods if m.qualname.startswith(cls.qualname)]
                        if cls_methods:
                            for j, method in enumerate(cls_methods[:2]):
                                print(f"    Method {j+1}: {method.name} ({method.qualname})")
                            if len(cls_methods) > 2:
                                print(f"    ... and {len(cls_methods)-2} more methods")
                    if len(classes) > 3:
                        print(f"  ... and {len(classes)-3} more classes")
                
                # Use all functions (module + methods) for processing
                functions = all_functions
                
                # Get IR format (TAC/CFG) for each function and attach it to the function
                # The pipeline runs transformations and stores them in scope_manager
                for func in functions:
                    # Try to get CFG first (most complete), then TAC, then fallback to raw func
                    func_ir = None
                    found_fmt = None
                    for fmt in ['cfg', 'block cfg', 'three address', 'ir']:
                        func_ir = scope_manager.get_ir(func, fmt)
                        if func_ir is not None:
                            found_fmt = fmt
                            if debug or self.config.verbose:
                                print(f"  Found {fmt} for {func.name}")
                            break
                    
                    # Attach the IR as an attribute so iter_function_events can find it
                    if func_ir is not None:
                        # Store the CFG/TAC on the function object for later retrieval
                        func._cfg = func_ir
                        func._ir_format = found_fmt
                        functions_with_ir.append(func)
                    else:
                        if debug or self.config.verbose:
                            print(f"  WARNING: No IR format found for {func.name}, using raw function")
                        functions_with_ir.append(func)
            else:
                if debug or self.config.verbose:
                    print("WARNING: scope_manager has no get_subscopes method")
            
            # Run pointer analysis
            if debug or self.config.verbose:
                print(f"\nStarting pointer analysis with {len(functions_with_ir)} functions...")
            
            analysis = KCFA2PointerAnalysis(self.config)
            
            # Plan analysis with functions (pass IR representations)
            # NOTE: plan() MUST be called first - it initializes _functions dict
            if functions_with_ir:
                analysis.plan(functions_with_ir)
            else:
                # Fallback: try to plan with module
                if debug or self.config.verbose:
                    print("WARNING: No functions with IR, trying module directly")
                analysis.plan(ir_module)
            
            # Also process module-level events (for class definitions, etc.)
            if hasattr(analysis, 'plan_module'):
                analysis.plan_module(ir_module)
            
            # Process class definitions
            if classes and hasattr(analysis, 'plan_classes'):
                analysis.plan_classes(classes)
            
            # TWO-PASS MODE: Inject unified function table AFTER planning
            # This prevents plan() from overwriting the injected functions
            if unified_functions:
                if debug or self.config.verbose:
                    print(f"\n  DEBUG: Function table injection (AFTER planning):")
                    print(f"    Unified table size: {len(unified_functions)} functions")
                    print(f"    Before injection: {len(analysis._functions)} local functions")
                
                # Save local function count for reporting
                local_func_count = len(analysis._functions)
                
                # CRITICAL: Verify CFG is attached to unified functions
                funcs_with_cfg = 0
                for qualname, func in unified_functions.items():
                    if hasattr(func, '_cfg') and func._cfg is not None:
                        funcs_with_cfg += 1
                
                if debug or self.config.verbose:
                    print(f"    Unified functions with CFG: {funcs_with_cfg}/{len(unified_functions)}")
                
                # Merge unified functions with local functions
                # Unified functions use qualname as key, local uses name as key
                # We need to support BOTH lookup strategies
                analysis._functions.update(unified_functions)
                
                if debug or self.config.verbose:
                    after_count = len(analysis._functions)
                    print(f"    After injection: {after_count} functions (+{after_count - local_func_count} from other modules)")
                    # Sample function names
                    sample_names = list(analysis._functions.keys())[:8]
                    print(f"    Sample functions: {sample_names}")
            
            analysis.initialize()
            analysis.run()
            
            if debug or self.config.verbose:
                print(f"\n{'='*60}")
                print(f"ANALYSIS RESULTS DEBUG")
                print(f"{'='*60}")
                print(f"Analysis complete: {analysis._analysis_complete}")
                print(f"Functions registered: {len(analysis._functions)}")
                print(f"  Function names: {list(analysis._functions.keys())[:5]}")
                print(f"Env entries: {len(analysis._env)}")
                print(f"  Sample env keys: {list(analysis._env.keys())[:3]}")
                print(f"Heap entries: {len(analysis._heap)}")
                print(f"  Sample heap keys: {list(analysis._heap.keys())[:3]}")
                print(f"Contexts: {len(analysis._contexts)}")
                print(f"Statistics: {analysis._statistics}")
                
                if hasattr(analysis, '_call_graph'):
                    cg_stats = analysis._call_graph.get_statistics()
                    print(f"Call graph stats: {cg_stats}")
                
                if hasattr(analysis, '_class_hierarchy') and analysis._class_hierarchy:
                    print(f"Class hierarchy bases: {len(analysis._class_hierarchy._bases) if hasattr(analysis._class_hierarchy, '_bases') else 'N/A'}")
                    print(f"Class hierarchy MRO cache: {len(analysis._class_hierarchy._mro_cache) if hasattr(analysis._class_hierarchy, '_mro_cache') else 'N/A'}")
                
                # Report unresolved calls
                if hasattr(analysis, '_unresolved_calls') and analysis._unresolved_calls:
                    print(f"\nUnresolved calls: {len(analysis._unresolved_calls)} unique callees")
                    # Show top 20 unresolved
                    sorted_unresolved = sorted(analysis._unresolved_calls.items(), key=lambda x: -x[1])
                    print(f"Top unresolved callees:")
                    for i, (callee, count) in enumerate(sorted_unresolved[:20], 1):
                        print(f"  {i:2d}. {callee} ({count} call sites)")
                    if len(sorted_unresolved) > 20:
                        print(f"  ... and {len(sorted_unresolved)-20} more")
                
                # Report method reference tracking
                method_refs_tracked = len(getattr(analysis, '_method_refs', {}))
                method_ref_hits = getattr(analysis, '_method_ref_hits', 0)
                if method_refs_tracked > 0:
                    print(f"\nMethod reference tracking:")
                    print(f"  Total method refs tracked: {method_refs_tracked}")
                    print(f"  Method refs successfully resolved: {method_ref_hits}")
                    print(f"  Resolution rate: {100.0 * method_ref_hits / method_refs_tracked if method_refs_tracked > 0 else 0:.1f}%")
                
                print(f"{'='*60}\n")
            
            result.success = True
            
        except Exception as e:
            result.error = str(e)
            result.error_type = type(e).__name__
            print(f"  ERROR in {module_name}: {e}")
            if self.config.verbose or debug:
                traceback.print_exc()
        
        result.duration = time.time() - start_time
        return result, analysis
    
    def _get_library_paths(self) -> List[str]:
        """Get Python library paths."""
        import site
        paths = [os.path.dirname(os.__file__)]
        paths.extend(site.getsitepackages())
        user_site = site.getusersitepackages()
        if user_site:
            paths.append(user_site)
        return paths
    
    def collect_all_functions(self, modules: List[Path]) -> Dict[str, Any]:
        """Pass 1: Collect all function signatures from all modules (lightweight)."""
        print(f"\n{'='*70}")
        print(f"PASS 1: Collecting function signatures from {len(modules)} modules...")
        print(f"{'='*70}\n")
        
        all_functions = {}
        
        for i, module_path in enumerate(modules, 1):
            try:
                rel_path = module_path.relative_to(self.project_path)
            except ValueError:
                rel_path = Path(*module_path.parts[-3:])
            
            print(f"[{i}/{len(modules)}] Scanning {rel_path}...", end=' ', flush=True)
            
            try:
                # Create pipeline for IR extraction only
                pipeline_config = {
                    "filename": str(module_path),
                    "project_path": str(self.project_path),
                    "library_paths": self._get_library_paths(),
                    "analysis": [],
                    "lazy_ir_construction": True
                }
                
                pipeline = Pipeline(config=pipeline_config)
                pipeline.run()
                
                world = pipeline.get_world()
                ir_module = world.entry_module
                scope_manager = world.scope_manager
                
                # Extract functions
                from pythonstan.ir import IRFunc, IRClass
                if hasattr(scope_manager, 'get_subscopes'):
                    subscopes = scope_manager.get_subscopes(ir_module)
                    module_funcs = [s for s in subscopes if isinstance(s, IRFunc)]
                    classes = [s for s in subscopes if isinstance(s, IRClass)]
                    
                    # Extract methods from classes
                    for cls in classes:
                        cls_subscopes = scope_manager.get_subscopes(cls)
                        methods = [s for s in cls_subscopes if isinstance(s, IRFunc)]
                        module_funcs.extend(methods)
                    
                    # CRITICAL: Attach CFG/TAC to each function for cross-module analysis
                    for func in module_funcs:
                        # Try to get CFG first (most complete), then TAC, then fallback
                        func_ir = None
                        found_fmt = None
                        for fmt in ['cfg', 'block cfg', 'three address', 'ir']:
                            func_ir = scope_manager.get_ir(func, fmt)
                            if func_ir is not None:
                                found_fmt = fmt
                                break
                        
                        # Attach the IR as an attribute so iter_function_events can find it
                        if func_ir is not None:
                            func._cfg = func_ir
                            func._ir_format = found_fmt
                        
                        # Store functions by qualname
                        if hasattr(func, 'qualname'):
                            all_functions[func.qualname] = func
                    
                    print(f"✓ ({len(module_funcs)} functions)")
                else:
                    print(f"✓ (0 functions)")
                    
            except Exception as e:
                print(f"✗ ({type(e).__name__})")
        
        print(f"\n{'='*70}")
        print(f"PASS 1 COMPLETE: Collected {len(all_functions)} unique functions")
        print(f"{'='*70}")
        
        # DEBUG: Show sample of collected functions
        print(f"\nDEBUG: Sample of collected functions:")
        for i, qualname in enumerate(list(all_functions.keys())[:20]):
            print(f"  {i+1:3d}. {qualname}")
        if len(all_functions) > 20:
            print(f"  ... and {len(all_functions)-20} more")
        
        # Count by type/pattern
        module_level = sum(1 for q in all_functions.keys() if '.' not in q)
        class_methods = sum(1 for q in all_functions.keys() if '.' in q)
        print(f"\nFunction breakdown:")
        print(f"  Module-level functions: {module_level}")
        print(f"  Class methods: {class_methods}")
        print(f"  Total: {len(all_functions)}")
        print(f"{'='*70}\n")
        
        return all_functions
    
    def analyze_project_incremental(self, modules: List[Path], 
                                     max_modules: Optional[int] = None,
                                     debug: bool = False,
                                     dep_info: str = "",
                                     two_pass: bool = False) -> List[KCFA2PointerAnalysis]:
        """Analyze project modules incrementally.
        
        Args:
            two_pass: If True, use two-pass analysis for cross-module call resolution
        """
        if max_modules:
            modules = modules[:max_modules]
        
        # TWO-PASS ANALYSIS: Collect all functions first
        unified_function_table = None
        if two_pass:
            unified_function_table = self.collect_all_functions(modules)
        
        print(f"\n{'='*70}")
        print(f"{'PASS 2: ' if two_pass else ''}Analyzing {self.project_name}")
        print(f"Total modules to analyze: {len(modules)}")
        if dep_info:
            print(f"Dependencies: {dep_info}")
        print(f"Configuration: k={self.config.k}, obj_depth={self.config.obj_depth}")
        if two_pass:
            print(f"Two-pass mode: ENABLED (cross-module resolution)")
        if debug:
            print(f"DEBUG MODE ENABLED")
        print(f"{'='*70}\n")
        
        # Start memory tracking
        tracemalloc.start()
        
        all_analyses = []
        
        for i, module_path in enumerate(modules, 1):
            # Try to get relative path, but handle dependencies outside project
            try:
                rel_path = module_path.relative_to(self.project_path)
            except ValueError:
                # Dependency module - use a shorter name
                rel_path = Path(*module_path.parts[-3:])  # e.g., site-packages/jinja2/utils.py
            print(f"[{i}/{len(modules)}] Analyzing {rel_path}...", end=' ', flush=True)
            
            module_start_time = time.time()
            result, analysis = self.analyze_module(module_path, debug=debug, 
                                                   unified_functions=unified_function_table)
            module_duration = time.time() - module_start_time
            
            # Track per-module timing
            self.report.performance_metrics = self.report.performance_metrics or PerformanceMetrics()
            self.report.performance_metrics.per_module_time[str(rel_path)] = module_duration
            
            self.report.module_results.append(result)
            if analysis:
                all_analyses.append(analysis)
            
            if result.success:
                print(f"✓ ({result.duration:.2f}s, {result.functions_analyzed} funcs)")
                self.report.modules_succeeded += 1
            else:
                print(f"✗ ({result.error_type})")
                self.report.modules_failed += 1
                
                # Track error categories
                if result.error_type not in self.report.error_categories:
                    self.report.error_categories[result.error_type] = []
                self.report.error_categories[result.error_type].append(result.module_name)
        
        self.report.modules_analyzed = len(modules)
        
        # Get peak memory usage
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        
        # Update performance metrics
        if self.report.performance_metrics:
            self.report.performance_metrics.peak_memory_mb = peak / (1024 * 1024)
            self.report.performance_metrics.peak_memory_current_mb = current / (1024 * 1024)
        
        return all_analyses
    
    def compute_aggregate_metrics(self, all_analyses: List[KCFA2PointerAnalysis]):
        """Compute aggregate metrics from module results and analysis objects."""
        # Performance metrics - preserve existing metrics or create new
        perf = self.report.performance_metrics or PerformanceMetrics()
        perf.total_duration = sum(r.duration for r in self.report.module_results)
        perf.analysis_time = perf.total_duration
        
        total_loc = sum(r.lines_of_code for r in self.report.module_results if r.success)
        if perf.total_duration > 0:
            perf.throughput_loc_per_sec = total_loc / perf.total_duration
        
        self.report.performance_metrics = perf
        
        # Compute points-to metrics from analysis results
        pts_metrics = PointsToMetrics()
        all_sizes = []
        
        for analysis in all_analyses:
            # Access internal state
            if hasattr(analysis, '_env'):
                pts_metrics.total_variables += len(analysis._env)
                for (ctx, var), pts in analysis._env.items():
                    if pts:
                        pts_metrics.total_points_to_sets += 1
                        size = len(pts)
                        all_sizes.append(size)
                        if size == 0:
                            pts_metrics.empty_sets += 1
                        elif size == 1:
                            pts_metrics.singleton_sets += 1
        
        if all_sizes:
            pts_metrics.avg_set_size = sum(all_sizes) / len(all_sizes)
            pts_metrics.max_set_size = max(all_sizes)
            all_sizes.sort()
            pts_metrics.median_set_size = all_sizes[len(all_sizes) // 2]
            
            # Compute size distribution
            bins = [(1, 1), (2, 5), (6, 10), (11, 20), (21, 50), (51, float('inf'))]
            for low, high in bins:
                count = sum(1 for s in all_sizes if low <= s <= high)
                if count > 0:
                    label = f"{low}-{high}" if high != float('inf') else f"{low}+"
                    pts_metrics.set_size_distribution[label] = count
        
        self.report.points_to_metrics = pts_metrics
        
        # Compute call graph metrics
        cg_metrics = CallGraphMetrics()
        
        # FIXED: Count unique functions across all analyses (avoid double-counting due to injection)
        all_unique_functions = set()
        total_call_sites = 0
        
        for analysis in all_analyses:
            if hasattr(analysis, '_functions'):
                all_unique_functions.update(analysis._functions.keys())
            if hasattr(analysis, '_call_graph'):
                cg = analysis._call_graph
                # Use get_statistics() to get edge count
                if hasattr(cg, 'get_statistics'):
                    stats = cg.get_statistics()
                    cg_metrics.total_edges += stats.get('total_cs_edges', 0)
                    total_call_sites += stats.get('unique_call_sites', 0)
                elif hasattr(cg, '_cs_call_graph'):
                    # Fallback: count edges directly
                    cg_metrics.total_edges += sum(len(targets) for targets in cg._cs_call_graph.values())
                    total_call_sites += len(cg._cs_call_graph)
        
        cg_metrics.total_functions = len(all_unique_functions)
        cg_metrics.total_call_sites = total_call_sites
        
        self.report.call_graph_metrics = cg_metrics
        
        # Compute per-function metrics
        self._compute_function_metrics(all_analyses)
        
        # Compute object metrics
        self._compute_object_metrics(all_analyses)
        
        # Compute class hierarchy metrics
        ch_metrics = ClassHierarchyMetrics()
        for analysis in all_analyses:
            if hasattr(analysis, '_class_hierarchy') and analysis._class_hierarchy:
                ch = analysis._class_hierarchy
                # Count classes from _bases (all registered classes)
                if hasattr(ch, '_bases'):
                    ch_metrics.total_classes += len(ch._bases)
                    
                    # Eagerly compute MRO for all classes to populate cache and get metrics
                    # Create a snapshot of class IDs to avoid dict modification during iteration
                    if hasattr(ch, 'get_mro'):
                        class_ids = list(ch._bases.keys())
                        for class_id in class_ids:
                            try:
                                mro = ch.get_mro(class_id)
                                if mro:
                                    ch_metrics.classes_with_mro += 1
                            except Exception:
                                # MRO computation may fail for unresolved base classes
                                pass
                    
                    # Collect MRO statistics from cache (now populated)
                    if hasattr(ch, '_mro_cache'):
                        mro_lengths = [len(m) for m in ch._mro_cache.values() if m]
                        if mro_lengths:
                            ch_metrics.max_mro_length = max(mro_lengths)
                            ch_metrics.avg_mro_length = sum(mro_lengths) / len(mro_lengths)
        
        self.report.class_hierarchy_metrics = ch_metrics
        
        # Compute object-call correlation after function metrics are computed
        self._compute_object_call_correlation()
    
    def _compute_function_metrics(self, all_analyses: List[KCFA2PointerAnalysis]):
        """Compute per-function metrics for ALL functions (not just those in call graph)."""
        func_metrics_map: Dict[str, FunctionMetrics] = {}
        
        for analysis in all_analyses:
            # FIRST: Initialize metrics for ALL functions discovered by analysis
            if hasattr(analysis, '_functions'):
                for func_name in analysis._functions.keys():
                    if func_name not in func_metrics_map:
                        func_metrics_map[func_name] = FunctionMetrics(function_name=func_name)
            
            # SECOND: Collect call graph metrics (in-degree, out-degree)
            if hasattr(analysis, '_call_graph'):
                cg = analysis._call_graph
                
                if hasattr(cg, '_cs_call_graph'):
                    # Track out-degrees and in-degrees
                    out_degree_map = defaultdict(set)  # function -> {callees}
                    in_degree_map = defaultdict(set)   # function -> {callers}
                    contexts_per_func = defaultdict(set)  # function -> {contexts}
                    
                    for (caller_ctx, call_site), targets in cg._cs_call_graph.items():
                        caller_fn = call_site.fn
                        contexts_per_func[caller_fn].add(str(caller_ctx))
                        
                        for callee_ctx, callee_fn in targets:
                            out_degree_map[caller_fn].add(callee_fn)
                            in_degree_map[callee_fn].add(caller_fn)
                            contexts_per_func[callee_fn].add(str(callee_ctx))
                    
                    # Update metrics with call graph data
                    all_functions_in_cg = set(out_degree_map.keys()) | set(in_degree_map.keys())
                    for func_name in all_functions_in_cg:
                        if func_name not in func_metrics_map:
                            func_metrics_map[func_name] = FunctionMetrics(function_name=func_name)
                        
                        metrics = func_metrics_map[func_name]
                        metrics.out_degree = len(out_degree_map[func_name])
                        metrics.in_degree = len(in_degree_map[func_name])
                        metrics.contexts_seen = len(contexts_per_func[func_name])
            
            # THIRD: Collect per-function variable and object metrics
            if hasattr(analysis, '_env'):
                func_vars_map = defaultdict(list)  # function -> [(ctx, var, pts)]
                func_objects_map = defaultdict(lambda: defaultdict(set))  # function -> type -> {objects}
                
                for (ctx, var), pts in analysis._env.items():
                    # Try to determine which function this variable belongs to
                    func_name = self._infer_function_from_context(ctx, var)
                    if func_name:
                        func_vars_map[func_name].append((ctx, var, pts))
                        
                        # Collect objects by type
                        if hasattr(pts, 'objects'):
                            for obj in pts.objects:
                                obj_type = self._classify_object_type(obj)
                                func_objects_map[func_name][obj_type].add(obj)
                
                # Update function metrics with variable and object data
                for func_name, vars_list in func_vars_map.items():
                    if func_name not in func_metrics_map:
                        func_metrics_map[func_name] = FunctionMetrics(function_name=func_name)
                    
                    metrics = func_metrics_map[func_name]
                    metrics.variables_tracked = len(vars_list)
                    
                    # Compute precision (singleton ratio)
                    singleton_count = sum(1 for _, _, pts in vars_list if len(pts) == 1)
                    if vars_list:
                        metrics.precision = 100.0 * singleton_count / len(vars_list)
                    
                    # Count objects by type
                    if func_name in func_objects_map:
                        for obj_type, objs in func_objects_map[func_name].items():
                            metrics.objects_by_type[obj_type] = len(objs)
                        metrics.objects_in_scope = sum(metrics.objects_by_type.values())
        
        self.report.function_metrics = func_metrics_map
    
    def _classify_object_type(self, obj) -> str:
        """Classify an object by type from its allocation ID."""
        if hasattr(obj, 'alloc_id'):
            alloc_id = str(obj.alloc_id)
            # Extract type from allocation ID
            if ':' in alloc_id:
                parts = alloc_id.split(':')
                # Look for recognizable type names
                for part in parts:
                    if part in ['list', 'dict', 'tuple', 'set', 'obj', 'func', 'class', 'str', 'int']:
                        return part
                # Use second part if available
                if len(parts) >= 2 and not parts[1].isdigit():
                    return parts[1]
        
        return "obj"  # Default to generic object
    
    def _compute_object_call_correlation(self):
        """Compute correlation between object counts and call edges per function."""
        if not self.report.function_metrics:
            return
        
        # Extract data points: (objects, calls) for each function
        data_points = []
        for func_name, metrics in self.report.function_metrics.items():
            objects = metrics.objects_in_scope
            calls = metrics.out_degree + metrics.in_degree
            data_points.append((objects, calls))
        
        if len(data_points) < 2:
            return
        
        # Compute Pearson correlation coefficient
        objects_list = [p[0] for p in data_points]
        calls_list = [p[1] for p in data_points]
        
        n = len(data_points)
        sum_objects = sum(objects_list)
        sum_calls = sum(calls_list)
        sum_objects_sq = sum(x*x for x in objects_list)
        sum_calls_sq = sum(y*y for y in calls_list)
        sum_products = sum(x*y for x, y in data_points)
        
        numerator = n * sum_products - sum_objects * sum_calls
        denominator = ((n * sum_objects_sq - sum_objects**2) * (n * sum_calls_sq - sum_calls**2)) ** 0.5
        
        correlation = 0.0
        if denominator != 0:
            correlation = numerator / denominator
        
        # Store correlation in object metrics
        if not self.report.object_metrics:
            self.report.object_metrics = ObjectMetrics()
        
        # Add correlation as a custom attribute (not in dataclass, but in JSON report)
        self.correlation_coefficient = correlation
        
        # Also compute distribution statistics for reporting
        self.object_call_scatter_data = {
            'correlation_coefficient': correlation,
            'data_points': data_points[:100],  # Sample for scatter plot
            'stats': {
                'functions_with_no_calls': sum(1 for _, c in data_points if c == 0),
                'functions_with_no_objects': sum(1 for o, _ in data_points if o == 0),
                'avg_objects': sum_objects / n if n > 0 else 0,
                'avg_calls': sum_calls / n if n > 0 else 0,
                'max_objects': max(objects_list) if objects_list else 0,
                'max_calls': max(calls_list) if calls_list else 0
            }
        }
    
    def _infer_function_from_context(self, ctx, var: str) -> Optional[str]:
        """Infer function name from context or variable name.
        
        This is a heuristic - may not always be accurate.
        """
        # If context has call string, use last call's function
        if hasattr(ctx, 'call_string') and ctx.call_string:
            return ctx.call_string[-1].fn
        
        # If variable looks like a parameter, try to infer from name
        # This is very approximate
        return None
    
    def _compute_object_metrics(self, all_analyses: List[KCFA2PointerAnalysis]):
        """Compute object allocation and tracking metrics."""
        obj_metrics = ObjectMetrics()
        
        # Collect all objects from heap and environment
        all_objects = set()
        vars_by_obj_count = defaultdict(int)  # object_count -> num_vars
        
        for analysis in all_analyses:
            # Count objects from heap
            if hasattr(analysis, '_heap'):
                for (obj, field), pts in analysis._heap.items():
                    all_objects.add(obj)
                    if hasattr(pts, 'objects'):
                        all_objects.update(pts.objects)
            
            # Count objects from environment and compute variable statistics
            if hasattr(analysis, '_env'):
                for (ctx, var), pts in analysis._env.items():
                    if hasattr(pts, 'objects'):
                        obj_count = len(pts.objects)
                        vars_by_obj_count[obj_count] += 1
                        all_objects.update(pts.objects)
        
        obj_metrics.total_objects_created = len(all_objects)
        
        # Compute variable statistics
        total_vars = sum(vars_by_obj_count.values())
        if total_vars > 0:
            total_objects_tracked = sum(count * num_vars for count, num_vars in vars_by_obj_count.items())
            obj_metrics.avg_objects_per_variable = total_objects_tracked / total_vars
            
            obj_metrics.vars_with_no_objects = vars_by_obj_count[0]
            obj_metrics.vars_with_singleton = vars_by_obj_count[1]
            obj_metrics.vars_with_multiple = sum(count for obj_count, count in vars_by_obj_count.items() if obj_count > 1)
        
        # Classify object types (from allocation IDs)
        for obj in all_objects:
            if hasattr(obj, 'alloc_id'):
                alloc_id = str(obj.alloc_id)
                # Try to extract type from allocation ID
                # Common formats: "alloc:<type>:..." or just line numbers
                obj_type = "unknown"
                if ':' in alloc_id:
                    parts = alloc_id.split(':')
                    # Look for recognizable type names
                    for part in parts:
                        if part in ['list', 'dict', 'tuple', 'set', 'obj', 'func', 'class', 'str', 'int']:
                            obj_type = part
                            break
                    else:
                        # Use second part if it looks like a type
                        if len(parts) >= 2 and not parts[1].isdigit():
                            obj_type = parts[1]
                        else:
                            obj_type = "alloc"  # Generic allocation
                else:
                    # Just a number - likely line number
                    obj_type = "alloc"
                
                obj_metrics.object_types[obj_type] = obj_metrics.object_types.get(obj_type, 0) + 1
        
        self.report.object_metrics = obj_metrics
    
    def generate_report_markdown(self, output_path: Path):
        """Generate comprehensive markdown report."""
        lines = []
        
        # Header
        lines.append(f"# {self.project_name.upper()} - 2-CFA Pointer Analysis Report")
        lines.append(f"\n**Generated:** {self.report.timestamp}\n")
        
        # Configuration
        lines.append("## Analysis Configuration\n")
        lines.append(f"- **k**: {self.config.k} (call-string sensitivity)")
        lines.append(f"- **obj_depth**: {self.config.obj_depth} (object sensitivity)")
        lines.append(f"- **Field sensitivity**: {self.config.field_sensitivity_mode}")
        lines.append(f"- **MRO enabled**: {self.config.use_mro}")
        lines.append(f"- **Class hierarchy**: {self.config.build_class_hierarchy}")
        
        # Results Summary
        lines.append("\n## Results Summary\n")
        lines.append(f"- **Duration**: {self.report.performance_metrics.total_duration:.2f} seconds")
        lines.append(f"- **Modules analyzed**: {self.report.modules_analyzed}")
        lines.append(f"- **Modules succeeded**: {self.report.modules_succeeded}")
        lines.append(f"- **Modules failed**: {self.report.modules_failed}")
        
        success_rate = 0.0
        if self.report.modules_analyzed > 0:
            success_rate = 100.0 * self.report.modules_succeeded / self.report.modules_analyzed
        lines.append(f"- **Success rate**: {success_rate:.1f}%")
        
        # Performance
        if self.report.performance_metrics:
            perf = self.report.performance_metrics
            lines.append(f"- **Throughput**: {perf.throughput_loc_per_sec:.1f} LOC/sec")
        
        # Points-to Metrics
        if self.report.points_to_metrics and self.report.points_to_metrics.total_variables > 0:
            lines.append("\n## Points-to Analysis Metrics\n")
            pts = self.report.points_to_metrics
            lines.append(f"- **Total variables tracked**: {pts.total_variables}")
            lines.append(f"- **Non-empty points-to sets**: {pts.total_points_to_sets}")
            lines.append(f"- **Singleton sets**: {pts.singleton_sets} ({100.0*pts.singleton_sets/pts.total_points_to_sets if pts.total_points_to_sets > 0 else 0:.1f}%)")
            lines.append(f"- **Empty sets**: {pts.empty_sets}")
            lines.append(f"- **Average set size**: {pts.avg_set_size:.2f}")
            lines.append(f"- **Maximum set size**: {pts.max_set_size}")
            lines.append(f"- **Median set size**: {pts.median_set_size:.1f}")
            
            if pts.set_size_distribution:
                lines.append("\n### Points-to Set Size Distribution\n")
                lines.append("| Size Range | Count |")
                lines.append("|------------|-------|")
                for range_label, count in sorted(pts.set_size_distribution.items()):
                    lines.append(f"| {range_label} | {count} |")
        
        # Call Graph Metrics
        if self.report.call_graph_metrics and self.report.call_graph_metrics.total_functions > 0:
            lines.append("\n## Call Graph Metrics\n")
            cg = self.report.call_graph_metrics
            lines.append(f"- **Total functions**: {cg.total_functions}")
            lines.append(f"- **Total call edges**: {cg.total_edges}")
            if cg.total_functions > 0:
                lines.append(f"- **Average out-degree**: {cg.avg_out_degree:.2f}")
        
        # Class Hierarchy Metrics
        if self.report.class_hierarchy_metrics and self.report.class_hierarchy_metrics.total_classes > 0:
            lines.append("\n## Class Hierarchy Metrics\n")
            ch = self.report.class_hierarchy_metrics
            lines.append(f"- **Total classes**: {ch.total_classes}")
            lines.append(f"- **Classes with MRO**: {ch.classes_with_mro}")
            lines.append(f"- **Maximum MRO length**: {ch.max_mro_length}")
            lines.append(f"- **Average MRO length**: {ch.avg_mro_length:.2f}")
        
        # Object Metrics
        if self.report.object_metrics and self.report.object_metrics.total_objects_created > 0:
            lines.append("\n## Object Metrics\n")
            obj = self.report.object_metrics
            lines.append(f"- **Total objects created**: {obj.total_objects_created}")
            lines.append(f"- **Average objects per variable**: {obj.avg_objects_per_variable:.2f}")
            lines.append(f"- **Variables with no objects**: {obj.vars_with_no_objects}")
            lines.append(f"- **Variables with singleton**: {obj.vars_with_singleton}")
            lines.append(f"- **Variables with multiple objects**: {obj.vars_with_multiple}")
            
            if obj.object_types:
                lines.append("\n### Object Type Distribution\n")
                lines.append("| Type | Count |")
                lines.append("|------|-------|")
                for obj_type, count in sorted(obj.object_types.items(), key=lambda x: -x[1]):
                    lines.append(f"| {obj_type} | {count} |")
        
        # Object-Call Correlation
        if hasattr(self, 'object_call_scatter_data'):
            lines.append("\n## Object-Call Correlation Analysis\n")
            corr_data = self.object_call_scatter_data
            lines.append(f"- **Pearson Correlation Coefficient**: {corr_data['correlation_coefficient']:.3f}")
            lines.append(f"- **Functions with no calls**: {corr_data['stats']['functions_with_no_calls']}")
            lines.append(f"- **Functions with no objects**: {corr_data['stats']['functions_with_no_objects']}")
            lines.append(f"- **Average objects per function**: {corr_data['stats']['avg_objects']:.1f}")
            lines.append(f"- **Average calls per function**: {corr_data['stats']['avg_calls']:.1f}")
            lines.append(f"- **Max objects in a function**: {corr_data['stats']['max_objects']}")
            lines.append(f"- **Max calls in a function**: {corr_data['stats']['max_calls']}")
            
            # Interpretation
            corr_val = corr_data['correlation_coefficient']
            if abs(corr_val) < 0.1:
                interpretation = "negligible correlation"
            elif abs(corr_val) < 0.3:
                interpretation = "weak correlation"
            elif abs(corr_val) < 0.5:
                interpretation = "moderate correlation"
            elif abs(corr_val) < 0.7:
                interpretation = "strong correlation"
            else:
                interpretation = "very strong correlation"
            
            lines.append(f"\n**Interpretation**: {interpretation} between object count and call edges")
        
        # Function Metrics (Top functions by various criteria)
        if self.report.function_metrics and len(self.report.function_metrics) > 0:
            lines.append("\n## Function Metrics\n")
            funcs = list(self.report.function_metrics.values())
            
            lines.append(f"- **Total functions tracked**: {len(funcs)}")
            
            # Distribution by out-degree
            lines.append("\n### Distribution by Outgoing Calls\n")
            out_degree_dist = {
                "0 calls": sum(1 for f in funcs if f.out_degree == 0),
                "1 call": sum(1 for f in funcs if f.out_degree == 1),
                "2-5 calls": sum(1 for f in funcs if 2 <= f.out_degree <= 5),
                "6-10 calls": sum(1 for f in funcs if 6 <= f.out_degree <= 10),
                "11+ calls": sum(1 for f in funcs if f.out_degree >= 11)
            }
            lines.append("| Call Count | Functions |")
            lines.append("|------------|-----------|")
            for category, count in out_degree_dist.items():
                pct = 100.0 * count / len(funcs) if funcs else 0
                lines.append(f"| {category} | {count} ({pct:.1f}%) |")
            
            # Distribution by objects
            lines.append("\n### Distribution by Object Count\n")
            obj_dist = {
                "0 objects": sum(1 for f in funcs if f.objects_in_scope == 0),
                "1-10 objects": sum(1 for f in funcs if 1 <= f.objects_in_scope <= 10),
                "11-50 objects": sum(1 for f in funcs if 11 <= f.objects_in_scope <= 50),
                "51-100 objects": sum(1 for f in funcs if 51 <= f.objects_in_scope <= 100),
                "101+ objects": sum(1 for f in funcs if f.objects_in_scope >= 101)
            }
            lines.append("| Object Count | Functions |")
            lines.append("|--------------|-----------|")
            for category, count in obj_dist.items():
                pct = 100.0 * count / len(funcs) if funcs else 0
                lines.append(f"| {category} | {count} ({pct:.1f}%) |")
            
            # Top functions by out-degree
            top_callers = sorted(funcs, key=lambda f: f.out_degree, reverse=True)[:10]
            if top_callers:
                lines.append("\n### Top Functions by Out-Degree (Most Calls)\n")
                lines.append("| Function | Out-Degree | In-Degree | Objects | Contexts |")
                lines.append("|----------|------------|-----------|---------|----------|")
                for func in top_callers:
                    lines.append(f"| {func.function_name} | {func.out_degree} | {func.in_degree} | {func.objects_in_scope} | {func.contexts_seen} |")
            
            # Top functions by in-degree
            top_callees = sorted(funcs, key=lambda f: f.in_degree, reverse=True)[:10]
            if top_callees:
                lines.append("\n### Top Functions by In-Degree (Most Called)\n")
                lines.append("| Function | In-Degree | Out-Degree | Objects | Variables |")
                lines.append("|----------|-----------|------------|---------|-----------|")
                for func in top_callees:
                    lines.append(f"| {func.function_name} | {func.in_degree} | {func.out_degree} | {func.objects_in_scope} | {func.variables_tracked} |")
            
            # Top functions by objects
            top_by_objects = sorted(funcs, key=lambda f: f.objects_in_scope, reverse=True)[:10]
            if top_by_objects:
                lines.append("\n### Top Functions by Object Count\n")
                lines.append("| Function | Objects | Out-Degree | In-Degree | Variables |")
                lines.append("|----------|---------|------------|-----------|-----------|")
                for func in top_by_objects:
                    lines.append(f"| {func.function_name} | {func.objects_in_scope} | {func.out_degree} | {func.in_degree} | {func.variables_tracked} |")
        
        # Performance Details (memory)
        if self.report.performance_metrics:
            perf = self.report.performance_metrics
            if perf.peak_memory_mb > 0:
                lines.append("\n## Memory Usage\n")
                lines.append(f"- **Peak memory**: {perf.peak_memory_mb:.2f} MB")
                lines.append(f"- **Current memory**: {perf.peak_memory_current_mb:.2f} MB")
        
        # Error Analysis
        if self.report.error_categories:
            lines.append("\n## Error Analysis\n")
            lines.append("| Error Type | Count | Affected Modules |")
            lines.append("|-----------|-------|------------------|")
            
            for error_type, modules in sorted(self.report.error_categories.items()):
                module_list = ", ".join(modules[:3])
                if len(modules) > 3:
                    module_list += f" (+{len(modules)-3} more)"
                lines.append(f"| {error_type} | {len(modules)} | {module_list} |")
        
        # Successful Modules
        lines.append("\n## Successfully Analyzed Modules\n")
        successful = [r for r in self.report.module_results if r.success]
        if successful:
            lines.append("| Module | Duration (s) | Functions | LOC |")
            lines.append("|--------|-------------|-----------|-----|")
            for result in successful[:20]:  # Show first 20
                lines.append(f"| {result.module_name} | {result.duration:.2f} | {result.functions_analyzed} | {result.lines_of_code} |")
            
            if len(successful) > 20:
                lines.append(f"\n*...and {len(successful)-20} more modules*")
        
        # Failed Modules
        if self.report.modules_failed > 0:
            lines.append("\n## Failed Modules\n")
            failed = [r for r in self.report.module_results if not r.success]
            lines.append("| Module | Error Type | Error Message |")
            lines.append("|--------|-----------|---------------|")
            for result in failed:
                error_msg = result.error[:50] + "..." if result.error and len(result.error) > 50 else result.error
                lines.append(f"| {result.module_name} | {result.error_type} | {error_msg} |")
        
        # Write report
        output_path.write_text('\n'.join(lines))
        print(f"\nMarkdown report saved to: {output_path}")
    
    def generate_report_json(self, output_path: Path):
        """Generate JSON report for programmatic access."""
        report_dict = {
            "project_name": self.report.project_name,
            "timestamp": self.report.timestamp,
            "config": self.report.config,
            "modules_analyzed": self.report.modules_analyzed,
            "modules_succeeded": self.report.modules_succeeded,
            "modules_failed": self.report.modules_failed,
            "module_results": [asdict(r) for r in self.report.module_results],
            "error_categories": self.report.error_categories,
            "performance_metrics": asdict(self.report.performance_metrics) if self.report.performance_metrics else {},
            "points_to_metrics": asdict(self.report.points_to_metrics) if self.report.points_to_metrics else {},
            "call_graph_metrics": asdict(self.report.call_graph_metrics) if self.report.call_graph_metrics else {},
            "class_hierarchy_metrics": asdict(self.report.class_hierarchy_metrics) if self.report.class_hierarchy_metrics else {},
            "object_metrics": asdict(self.report.object_metrics) if self.report.object_metrics else {},
            "function_metrics": {name: asdict(metrics) for name, metrics in self.report.function_metrics.items()} if self.report.function_metrics else {},
            "object_call_correlation": getattr(self, 'object_call_scatter_data', None)
        }
        
        output_path.write_text(json.dumps(report_dict, indent=2))
        print(f"JSON report saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze Flask/Werkzeug with 2-CFA pointer analysis"
    )
    parser.add_argument(
        "project",
        choices=["flask", "werkzeug", "both"],
        help="Project to analyze"
    )
    parser.add_argument(
        "--max-modules",
        type=int,
        default=None,
        help="Maximum number of modules to analyze (default: analyze all)"
    )
    parser.add_argument(
        "--k",
        type=int,
        default=2,
        help="Call string depth (default: 2)"
    )
    parser.add_argument(
        "--obj-depth",
        type=int,
        default=2,
        help="Object sensitivity depth (default: 2)"
    )
    parser.add_argument(
        "--include-deps",
        action="store_true",
        help="Include dependency libraries from .venv/site-packages"
    )
    parser.add_argument(
        "--deps",
        type=str,
        help="Comma-separated list of dependency packages to include (requires --include-deps)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output with detailed diagnostics"
    )
    parser.add_argument(
        "--two-pass",
        action="store_true",
        help="Enable two-pass analysis for cross-module call resolution (FIX for low call edges)"
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Output detailed diagnostic information about unresolved nodes"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent / "reports",
        help="Output directory for reports"
    )
    
    args = parser.parse_args()
    
    # Ensure output directory exists
    args.output_dir.mkdir(parents=True, exist_ok=True)
    
    # Configure analysis
    config = KCFAConfig(
        k=args.k,
        obj_depth=args.obj_depth,
        field_sensitivity_mode="attr-name",
        build_class_hierarchy=True,
        use_mro=True,
        verbose=args.verbose
    )
    
    # Determine projects to analyze
    projects_to_analyze = []
    if args.project in ["flask", "both"]:
        projects_to_analyze.append({
            "name": "flask",
            "path": PROJECT_ROOT / "benchmark" / "projects" / "flask" / "src" / "flask"
        })
    if args.project in ["werkzeug", "both"]:
        projects_to_analyze.append({
            "name": "werkzeug",
            "path": PROJECT_ROOT / "benchmark" / "projects" / "werkzeug" / "src" / "werkzeug"
        })
    
    # Analyze each project
    for project_info in projects_to_analyze:
        project_name = project_info["name"]
        project_path = project_info["path"]
        
        if not project_path.exists():
            print(f"ERROR: Project path not found: {project_path}")
            continue
        
        # Create analyzer
        analyzer = RealWorldAnalyzer(project_path, project_name, config)
        
        # Determine dependencies to include
        dep_names = []
        if args.include_deps:
            if args.deps:
                dep_names = [d.strip() for d in args.deps.split(',')]
            else:
                # Default dependencies for each project
                if project_name == "flask":
                    dep_names = ["werkzeug", "jinja2", "click", "itsdangerous", "markupsafe"]
                elif project_name == "werkzeug":
                    dep_names = ["markupsafe"]
        
        # Find modules
        modules = analyzer.find_python_modules(project_path, 
                                              include_deps=args.include_deps, 
                                              dep_names=dep_names)
        
        project_modules = [m for m in modules if str(project_path) in str(m)]
        dep_modules = [m for m in modules if str(project_path) not in str(m)]
        
        print(f"\nFound {len(project_modules)} Python modules in {project_name}")
        if dep_modules:
            print(f"Found {len(dep_modules)} dependency modules")
            dep_info = f"{len(dep_modules)} dep modules from {', '.join(dep_names)}"
        else:
            dep_info = ""
        
        # Analyze incrementally
        all_analyses = analyzer.analyze_project_incremental(modules, 
                                                           max_modules=args.max_modules, 
                                                           debug=args.debug,
                                                           dep_info=dep_info,
                                                           two_pass=args.two_pass)
        
        # Compute metrics
        analyzer.compute_aggregate_metrics(all_analyses)
        
        # Generate diagnostic output if requested
        if args.diagnose:
            print(f"\n{'='*70}")
            print(f"DIAGNOSTIC OUTPUT")
            print(f"{'='*70}\n")
            
            # Collect diagnostics from all analyses
            all_diagnostics = []
            for analysis in all_analyses:
                if analysis:
                    diag = analysis.get_diagnostic_info()
                    all_diagnostics.append(diag)
            
            # Save diagnostic JSON
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            diag_path = args.output_dir / f"{project_name}_diagnostics_{timestamp}.json"
            with open(diag_path, 'w') as f:
                json.dump({
                    'project': project_name,
                    'analyses': all_diagnostics,
                    'summary': {
                        'total_analyses': len(all_diagnostics),
                        'total_unresolved_calls': sum(len(d.get('unresolved_calls', {})) for d in all_diagnostics),
                        'total_empty_tmp_vars': sum(d.get('empty_tmp_variables', 0) for d in all_diagnostics),
                        'total_method_refs_tracked': sum(len(d.get('method_refs_tracked', {})) for d in all_diagnostics),
                        'total_method_refs_resolved': sum(d.get('method_refs_resolved', 0) for d in all_diagnostics),
                    }
                }, f, indent=2)
            
            print(f"Diagnostic output saved to: {diag_path}")
            print(f"Summary:")
            print(f"  Total unresolved calls: {sum(len(d.get('unresolved_calls', {})) for d in all_diagnostics)}")
            print(f"  Total empty $tmp variables: {sum(d.get('empty_tmp_variables', 0) for d in all_diagnostics)}")
            print(f"  Method refs tracked: {sum(len(d.get('method_refs_tracked', {})) for d in all_diagnostics)}")
            print(f"  Method refs resolved: {sum(d.get('method_refs_resolved', 0) for d in all_diagnostics)}")
            print()
        
        # Generate reports
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        md_path = args.output_dir / f"{project_name}_analysis_report_{timestamp}.md"
        json_path = args.output_dir / f"{project_name}_analysis_report_{timestamp}.json"
        
        analyzer.generate_report_markdown(md_path)
        analyzer.generate_report_json(json_path)
        
        print(f"\n{'='*70}")
        print(f"Analysis of {project_name} complete!")
        print(f"Success rate: {100.0 * analyzer.report.modules_succeeded / analyzer.report.modules_analyzed:.1f}%")
        print(f"{'='*70}\n")


if __name__ == "__main__":
    main()

