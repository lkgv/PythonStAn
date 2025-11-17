#!/usr/bin/env python3
"""
AST Call Site Census Tool

Systematically counts all Call nodes in Python source files to establish
a baseline for how many calls exist before IR conversion and analysis.

This helps identify the gap between source calls and discovered call edges.
"""

import ast
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Set
from dataclasses import dataclass, asdict
from collections import defaultdict
import json


@dataclass
class CallSiteInfo:
    """Information about a single call site."""
    file: str
    line: int
    col: int
    func_name: str  # Function containing the call
    callee: str  # Callee if statically determinable
    call_type: str  # 'direct', 'attribute', 'subscript', 'other'


@dataclass
class ModuleCallStats:
    """Call statistics for a single module."""
    module_path: str
    total_calls: int = 0
    direct_calls: int = 0  # foo()
    attribute_calls: int = 0  # obj.method()
    subscript_calls: int = 0  # obj[key]()
    other_calls: int = 0  # (expr)(), lambda()(), etc.
    
    functions_defined: int = 0
    classes_defined: int = 0
    
    # Detailed call sites
    call_sites: List[CallSiteInfo] = None
    
    # Top callees
    callee_counts: Dict[str, int] = None
    
    def __post_init__(self):
        if self.call_sites is None:
            self.call_sites = []
        if self.callee_counts is None:
            self.callee_counts = {}


@dataclass
class ProjectCallStats:
    """Aggregated call statistics for entire project."""
    project_name: str
    modules_analyzed: int = 0
    modules_failed: int = 0
    
    total_calls: int = 0
    direct_calls: int = 0
    attribute_calls: int = 0
    subscript_calls: int = 0
    other_calls: int = 0
    
    functions_defined: int = 0
    classes_defined: int = 0
    
    # Per-module stats
    module_stats: Dict[str, ModuleCallStats] = None
    
    # Aggregated top callees
    top_callees: Dict[str, int] = None
    
    def __post_init__(self):
        if self.module_stats is None:
            self.module_stats = {}
        if self.top_callees is None:
            self.top_callees = {}


class CallSiteCensus(ast.NodeVisitor):
    """AST visitor that counts and categorizes call sites."""
    
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.calls: List[CallSiteInfo] = []
        self.call_type_counts = {
            'direct': 0,
            'attribute': 0,
            'subscript': 0,
            'other': 0
        }
        self.current_function = '<module>'
        self.function_stack: List[str] = []
        self.functions_count = 0
        self.classes_count = 0
        self.callee_counts: Dict[str, int] = defaultdict(int)
    
    def visit_FunctionDef(self, node: ast.FunctionDef):
        """Track function definitions and scope."""
        self.functions_count += 1
        self.function_stack.append(node.name)
        old_func = self.current_function
        self.current_function = node.name
        self.generic_visit(node)
        self.current_function = old_func
        self.function_stack.pop()
    
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        """Track async function definitions."""
        self.functions_count += 1
        self.function_stack.append(node.name)
        old_func = self.current_function
        self.current_function = node.name
        self.generic_visit(node)
        self.current_function = old_func
        self.function_stack.pop()
    
    def visit_ClassDef(self, node: ast.ClassDef):
        """Track class definitions."""
        self.classes_count += 1
        self.generic_visit(node)
    
    def visit_Call(self, node: ast.Call):
        """Analyze call sites."""
        # Determine call type and callee
        callee_name = '<unknown>'
        call_type = 'other'
        
        if isinstance(node.func, ast.Name):
            # Direct call: foo()
            callee_name = node.func.id
            call_type = 'direct'
        elif isinstance(node.func, ast.Attribute):
            # Attribute call: obj.method()
            callee_name = node.func.attr
            call_type = 'attribute'
        elif isinstance(node.func, ast.Subscript):
            # Subscript call: obj[key]()
            callee_name = '<subscript>'
            call_type = 'subscript'
        
        # Record call site
        call_site = CallSiteInfo(
            file=self.file_path,
            line=node.lineno,
            col=node.col_offset,
            func_name=self.current_function,
            callee=callee_name,
            call_type=call_type
        )
        self.calls.append(call_site)
        self.call_type_counts[call_type] += 1
        self.callee_counts[callee_name] += 1
        
        # Continue visiting children
        self.generic_visit(node)


def analyze_file(file_path: Path) -> ModuleCallStats:
    """Analyze a single Python file for call sites.
    
    Args:
        file_path: Path to Python file
        
    Returns:
        ModuleCallStats with call information
    """
    stats = ModuleCallStats(module_path=str(file_path))
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        tree = ast.parse(source, filename=str(file_path))
        census = CallSiteCensus(str(file_path))
        census.visit(tree)
        
        # Populate stats
        stats.total_calls = len(census.calls)
        stats.direct_calls = census.call_type_counts['direct']
        stats.attribute_calls = census.call_type_counts['attribute']
        stats.subscript_calls = census.call_type_counts['subscript']
        stats.other_calls = census.call_type_counts['other']
        stats.functions_defined = census.functions_count
        stats.classes_defined = census.classes_count
        stats.call_sites = census.calls
        stats.callee_counts = dict(census.callee_counts)
        
    except SyntaxError as e:
        print(f"  Syntax error in {file_path}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"  Error analyzing {file_path}: {e}", file=sys.stderr)
    
    return stats


def analyze_project(project_path: Path, project_name: str = None,
                   exclude_patterns: List[str] = None) -> ProjectCallStats:
    """Analyze all Python files in a project.
    
    Args:
        project_path: Root path of project
        project_name: Name of project (defaults to directory name)
        exclude_patterns: Patterns to exclude (e.g., 'test', '__pycache__')
        
    Returns:
        ProjectCallStats with aggregated statistics
    """
    if project_name is None:
        project_name = project_path.name
    
    if exclude_patterns is None:
        exclude_patterns = ['test', 'tests', '__pycache__', '.git', '.venv', 'venv']
    
    stats = ProjectCallStats(project_name=project_name)
    
    print(f"\nAnalyzing project: {project_name}")
    print(f"Root path: {project_path}")
    print("=" * 80)
    
    # Find all Python files
    python_files = []
    for py_file in project_path.rglob("*.py"):
        # Check if any part of path matches exclude patterns
        if any(pattern in str(py_file) for pattern in exclude_patterns):
            continue
        python_files.append(py_file)
    
    print(f"Found {len(python_files)} Python files")
    print()
    
    # Analyze each file
    for i, py_file in enumerate(python_files, 1):
        if i % 10 == 0 or i == len(python_files):
            print(f"  Progress: {i}/{len(python_files)} files...", end='\r')
        
        module_stats = analyze_file(py_file)
        
        if module_stats.total_calls > 0:
            stats.modules_analyzed += 1
            relative_path = str(py_file.relative_to(project_path))
            stats.module_stats[relative_path] = module_stats
            
            # Aggregate counts
            stats.total_calls += module_stats.total_calls
            stats.direct_calls += module_stats.direct_calls
            stats.attribute_calls += module_stats.attribute_calls
            stats.subscript_calls += module_stats.subscript_calls
            stats.other_calls += module_stats.other_calls
            stats.functions_defined += module_stats.functions_defined
            stats.classes_defined += module_stats.classes_defined
            
            # Aggregate callee counts
            for callee, count in module_stats.callee_counts.items():
                if callee not in stats.top_callees:
                    stats.top_callees[callee] = 0
                stats.top_callees[callee] += count
    
    print()  # New line after progress
    print("=" * 80)
    
    return stats


def print_stats(stats: ProjectCallStats):
    """Print formatted statistics."""
    print(f"\nProject: {stats.project_name}")
    print("=" * 80)
    print(f"Modules analyzed:    {stats.modules_analyzed}")
    print(f"Functions defined:   {stats.functions_defined}")
    print(f"Classes defined:     {stats.classes_defined}")
    print()
    print(f"Total call sites:    {stats.total_calls}")
    print(f"  Direct calls:      {stats.direct_calls:6d} ({100*stats.direct_calls/stats.total_calls:.1f}%)")
    print(f"  Attribute calls:   {stats.attribute_calls:6d} ({100*stats.attribute_calls/stats.total_calls:.1f}%)")
    print(f"  Subscript calls:   {stats.subscript_calls:6d} ({100*stats.subscript_calls/stats.total_calls:.1f}%)")
    print(f"  Other calls:       {stats.other_calls:6d} ({100*stats.other_calls/stats.total_calls:.1f}%)")
    print()
    
    # Top callees
    if stats.top_callees:
        print("Top 50 Most Called Functions:")
        sorted_callees = sorted(stats.top_callees.items(), key=lambda x: x[1], reverse=True)[:50]
        for callee, count in sorted_callees:
            print(f"  {count:6d}  {callee}")
    
    # Modules with most calls
    print("\nTop 20 Modules by Call Count:")
    sorted_modules = sorted(stats.module_stats.items(), 
                           key=lambda x: x[1].total_calls, reverse=True)[:20]
    for module_path, module_stats in sorted_modules:
        print(f"  {module_stats.total_calls:6d}  {module_path}")


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Count call sites in Python projects')
    parser.add_argument('project_path', type=Path, help='Project directory')
    parser.add_argument('--name', help='Project name (defaults to directory name)')
    parser.add_argument('--output', type=Path, help='Output JSON file')
    parser.add_argument('--exclude', nargs='+', help='Patterns to exclude')
    
    args = parser.parse_args()
    
    # Run census
    stats = analyze_project(
        args.project_path, 
        project_name=args.name,
        exclude_patterns=args.exclude
    )
    
    # Print results
    print_stats(stats)
    
    # Save to file if requested
    if args.output:
        # Convert to JSON-serializable format (exclude detailed call_sites)
        output_data = {
            'project_name': stats.project_name,
            'modules_analyzed': stats.modules_analyzed,
            'total_calls': stats.total_calls,
            'direct_calls': stats.direct_calls,
            'attribute_calls': stats.attribute_calls,
            'subscript_calls': stats.subscript_calls,
            'other_calls': stats.other_calls,
            'functions_defined': stats.functions_defined,
            'classes_defined': stats.classes_defined,
            'top_callees': dict(sorted(stats.top_callees.items(), 
                                      key=lambda x: x[1], reverse=True)[:100]),
            'module_stats': {
                path: {
                    'total_calls': ms.total_calls,
                    'direct_calls': ms.direct_calls,
                    'attribute_calls': ms.attribute_calls,
                    'functions_defined': ms.functions_defined,
                    'classes_defined': ms.classes_defined
                }
                for path, ms in stats.module_stats.items()
            }
        }
        
        with open(args.output, 'w') as f:
            json.dump(output_data, f, indent=2)
        print(f"\nResults saved to: {args.output}")


if __name__ == '__main__':
    main()


