#!/usr/bin/env python3
"""
AI Analysis CLI script.

This script provides a command-line interface for running Abstract Interpretation
analysis with pointer analysis integration. It supports various input modes,
configuration options, and output formats.
"""

import ast
import os
import sys
import argparse
import tempfile
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

# Add pythonstan to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pythonstan.world.pipeline import Pipeline
from pythonstan.world import World
from pythonstan.analysis.ai import (
    AbstractInterpretationSolver, create_solver,
    MockPointerResults, create_abstract_state
)


# Test snippets for quick testing
QUICK_SNIPPETS = {
    "simple": '''
def add(x, y):
    return x + y

def main():
    a = 1
    b = 2
    result = add(a, b)
    return result

if __name__ == "__main__":
    main()
''',

    "conditional": '''
def conditional_func(x):
    if x > 0:
        return x * 2
    else:
        return -x

def main():
    result1 = conditional_func(5)
    result2 = conditional_func(-3)
    return result1, result2

if __name__ == "__main__":
    main()
''',

    "loop": '''
def sum_range(n):
    total = 0
    i = 0
    while i < n:
        total += i
        i += 1
    return total

def main():
    result = sum_range(10)
    return result

if __name__ == "__main__":
    main()
''',

    "objects": '''
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y
    
    def distance(self):
        return (self.x ** 2 + self.y ** 2) ** 0.5

def create_point(x, y):
    return Point(x, y)

def main():
    p1 = create_point(3, 4)
    p2 = Point(1, 2)
    d1 = p1.distance()
    d2 = p2.distance()
    return d1, d2

if __name__ == "__main__":
    main()
''',

    "containers": '''
def process_list(items):
    result = []
    for item in items:
        if isinstance(item, int):
            result.append(item * 2)
        else:
            result.append(str(item))
    return result

def process_dict(data):
    result = {}
    for key, value in data.items():
        result[key.upper()] = value + 1 if isinstance(value, int) else value
    return result

def main():
    nums = [1, 2, 3, "hello", 4.5]
    processed_nums = process_list(nums)
    
    data = {"a": 1, "b": 2, "c": "test"}
    processed_data = process_dict(data)
    
    return processed_nums, processed_data

if __name__ == "__main__":
    main()
'''
}


def create_temp_file(name: str, content: str) -> str:
    """Create a temporary test file and return its path."""
    test_dir = Path(tempfile.gettempdir()) / "pythonstan_ai_test"
    test_dir.mkdir(exist_ok=True)
    
    test_file = test_dir / f"{name}.py"
    test_file.write_text(content)
    return str(test_file)


def get_library_paths():
    """Get Python library paths for the current environment."""
    import site
    library_paths = []
    
    # Add standard library path
    library_paths.append(os.path.dirname(os.__file__))
    
    # Add site-packages
    for path in site.getsitepackages():
        library_paths.append(path)
    
    # Add user site-packages
    user_site = site.getusersitepackages()
    if user_site:
        library_paths.append(user_site)
    
    return library_paths


def create_pipeline_config(file_path: str, args) -> Dict[str, Any]:
    """Create pipeline configuration from arguments."""
    analyses = []
    
    # Always include basic transforms
    analyses.extend([
        {
            "name": "ir",
            "id": "IR",
            "description": "IR generation",
            "prev_analysis": [],
            "options": {"type": "transform"}
        },
        {
            "name": "block cfg",
            "id": "BlockCFG",
            "description": "block control flow graph analysis",
            "prev_analysis": ["ir"],
            "options": {"type": "transform"}
        },
        {
            "name": "cfg",
            "id": "CFG", 
            "description": "control flow graph analysis",
            "prev_analysis": ["block cfg"],
            "options": {"type": "transform"}
        }
    ])
    
    # Add pointer analysis if requested
    if not args.no_pointer:
        analyses.append({
            "name": f"pointer_analysis",
            "id": "PointerAnalysis",
            "description": "k-CFA pointer analysis",
            "prev_analysis": ["cfg"],
            "options": {
                "type": "pointer analysis",
                "k": args.k,
                "obj_depth": args.obj_depth,
                "field_sensitivity": args.field_sensitivity,
                "containers_mode": args.containers_mode,
                "verbose": args.verbose
            }
        })
    
    # Add dataflow analysis (liveness as example)
    df_options = {
        "type": "dataflow analysis",
        "ir": "cfg",
        "verbose": args.verbose
    }
    
    analyses.append({
        "name": "liveness_analysis",
        "id": "LivenessAnalysis",
        "description": "liveness analysis",
        "prev_analysis": ["pointer_analysis"] if not args.no_pointer else ["cfg"],
        "options": df_options
    })
    
    return {
        "filename": file_path,
        "project_path": str(Path(file_path).parent),
        "library_paths": get_library_paths(),
        "analysis": analyses
    }


def dump_ir(world: World, output_path: Optional[str] = None):
    """Dump IR representations."""
    output = []
    output.append("===== IR Dump =====")
    
    # Three Address Form
    output.append("\n----- Three Address Form -----")
    try:
        tac_ir = world.scope_manager.get_ir(world.entry_module, 'three address form')
        output.extend([ast.unparse(x) for x in tac_ir.body])
    except Exception as e:
        output.append(f"Error getting TAC IR: {e}")
    
    # Scopes and IR
    output.append("\n----- Scopes and IR -----")
    for scope in world.scope_manager.get_scopes():
        output.append(f'<Scope: {scope.get_qualname()}>')
        try:
            scope_ir = world.scope_manager.get_ir(scope, 'ir')
            output.extend([str(x) for x in scope_ir])
        except Exception as e:
            output.append(f"  Error getting scope IR: {e}")
        output.append("")
    
    content = '\n'.join(output)
    if output_path:
        with open(output_path, 'w') as f:
            f.write(content)
        print(f"IR dump saved to {output_path}")
    else:
        print(content)


def dump_cfg(world: World, output_path: Optional[str] = None):
    """Dump CFG information."""
    output = []
    output.append("===== CFG Dump =====")
    
    for scope in world.scope_manager.get_scopes():
        output.append(f"\n----- CFG for {scope.get_qualname()} -----")
        try:
            cfg = world.scope_manager.get_cfg(scope)
            if cfg:
                output.append(f"Basic blocks: {len(cfg.basic_blocks)}")
                output.append(f"Edges: {len(cfg.edges)}")
                
                for i, block in enumerate(cfg.basic_blocks):
                    output.append(f"  Block {i}: {block}")
                    
                for edge in cfg.edges:
                    output.append(f"  Edge: {edge}")
            else:
                output.append("  No CFG available")
        except Exception as e:
            output.append(f"  Error getting CFG: {e}")
    
    content = '\n'.join(output)
    if output_path:
        with open(output_path, 'w') as f:
            f.write(content)
        print(f"CFG dump saved to {output_path}")
    else:
        print(content)


def dump_callgraph(world: World, output_path: Optional[str] = None):
    """Dump call graph information."""
    output = []
    output.append("===== Call Graph Dump =====")
    
    try:
        call_graph = world.call_graph
        if call_graph:
            output.append(f"Total nodes: {len(call_graph.nodes)}")
            output.append(f"Total edges: {len(call_graph.edges)}")
            
            output.append("\n----- Nodes -----")
            for node in call_graph.nodes:
                output.append(f"  {node}")
            
            output.append("\n----- Edges -----")
            for edge in call_graph.edges:
                output.append(f"  {edge.caller} -> {edge.callee}")
        else:
            output.append("No call graph available")
    except Exception as e:
        output.append(f"Error getting call graph: {e}")
    
    content = '\n'.join(output)
    if output_path:
        with open(output_path, 'w') as f:
            f.write(content)
        print(f"Call graph dump saved to {output_path}")
    else:
        print(content)


def dump_ai_state(results: Dict[str, Any], output_path: str):
    """Dump AI analysis state to JSON."""
    try:
        # Convert results to JSON-serializable format
        def make_serializable(obj):
            """Convert objects to JSON-serializable format."""
            if isinstance(obj, (str, int, float, bool, type(None))):
                return obj
            elif isinstance(obj, (list, tuple)):
                return [make_serializable(item) for item in obj]
            elif isinstance(obj, dict):
                result = {}
                for k, v in obj.items():
                    # Convert key to string if not already serializable
                    if isinstance(k, (str, int, float, bool)):
                        key = k
                    else:
                        key = str(k)
                    result[key] = make_serializable(v)
                return result
            elif hasattr(obj, '__dict__'):
                # For objects with __dict__, try to serialize their attributes
                return {
                    "_type": type(obj).__name__,
                    "_repr": str(obj),
                    "attributes": make_serializable(getattr(obj, '__dict__', {}))
                }
            else:
                # For other objects, just use string representation
                return {
                    "_type": type(obj).__name__,
                    "_repr": str(obj)
                }
        
        serializable_results = make_serializable(results)
        
        with open(output_path, 'w') as f:
            json.dump(serializable_results, f, indent=2, default=str)
        print(f"AI state dump saved to {output_path}")
    except Exception as e:
        print(f"Error saving AI state dump: {e}")


def run_ai_analysis(file_path: str, args) -> bool:
    """Run AI analysis on the given file."""
    print(f"\n{'='*60}")
    print(f"Running AI Analysis: {os.path.basename(file_path)}")
    print(f"{'='*60}")
    
    try:
        # Create configuration
        config = create_pipeline_config(file_path, args)
        
        if args.verbose:
            print(f"Configuration: {json.dumps(config, indent=2)}")
        
        # Create and run pipeline
        print("Initializing analysis pipeline...")
        start_time = time.time()
        pipeline = Pipeline(config=config)
        
        print("Running analysis...")
        pipeline.run()
        end_time = time.time()
        
        print(f"Analysis completed in {end_time - start_time:.2f} seconds")
        
        # Get world and results
        world = pipeline.get_world()
        analysis_manager = pipeline.analysis_manager
        
        print(f"Analyzed {len(world.scope_manager.get_scopes())} scopes")
        
        # Print basic scope information
        if args.verbose:
            print("\nAnalyzed scopes:")
            for scope in world.scope_manager.get_scopes():
                print(f"  - {scope.get_qualname()}")
        
        # Handle dumps
        if args.dump_ir:
            output_path = args.dump_ir if args.dump_ir != True else None
            dump_ir(world, output_path)
        
        if args.dump_cfg:
            output_path = args.dump_cfg if args.dump_cfg != True else None
            dump_cfg(world, output_path)
        
        if args.dump_callgraph:
            output_path = args.dump_callgraph if args.dump_callgraph != True else None
            dump_callgraph(world, output_path)
        
        # Get dataflow analysis results
        if hasattr(analysis_manager, 'get_results'):
            try:
                liveness_results = analysis_manager.get_results("liveness_analysis")
                if liveness_results:
                    print(f"\nDataflow Analysis Results:")
                    print(f"  Analysis completed successfully")
                    
                    if args.dump_ai_state:
                        dump_ai_state(liveness_results, args.dump_ai_state)
                    
                    # Print summary statistics
                    if isinstance(liveness_results, dict):
                        for scope_name, scope_results in liveness_results.items():
                            print(f"  Scope {scope_name}:")
                            if hasattr(scope_results, '__len__'):
                                print(f"    Result entries: {len(scope_results)}")
                            print(f"    Result type: {type(scope_results).__name__}")
                else:
                    print("No dataflow analysis results available")
            except KeyError:
                print("Dataflow analysis results not found in pipeline")
            except Exception as e:
                print(f"Error retrieving dataflow results: {e}")
        
        return True
        
    except Exception as e:
        print(f"Error running AI analysis: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return False


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Run Abstract Interpretation analysis with pointer analysis integration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Input modes:
  --file PATH      Analyze a Python file
  --module NAME    Analyze a Python module  
  --snippet NAME   Use built-in test snippet ({', '.join(QUICK_SNIPPETS.keys())})

Pointer analysis configuration:
  --k NUM          Context sensitivity depth (default: 2)
  --obj-depth NUM  Object sensitivity depth (default: 2)
  --field-sensitivity MODE  Field sensitivity mode: attr, elem, value, full (default: attr)
  --containers-mode MODE    Container handling mode (default: precise)
  --no-pointer     Skip pointer analysis (use mock results)

AI analysis configuration:
  --verbose        Enable verbose output
  --timeout NUM    Analysis timeout in seconds (default: 300)
  --widening       Enable widening for faster convergence

Filters:
  --function NAME  Target specific function for analysis
  --entry NAME     Set entry point function name

Output options:
  --dump-ir [PATH]        Dump IR representations (optionally to file)
  --dump-cfg [PATH]       Dump CFG information (optionally to file)
  --dump-callgraph [PATH] Dump call graph (optionally to file)
  --dump-ai-state PATH    Dump AI analysis state to JSON file

Examples:
  {sys.argv[0]} --snippet simple --verbose
  {sys.argv[0]} --file example.py --k 3 --dump-ai-state results.json
  {sys.argv[0]} --module mymodule --function main --no-pointer
"""
    )
    
    # Input modes (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--file", help="Python file to analyze")
    input_group.add_argument("--module", help="Python module to analyze")
    input_group.add_argument("--snippet", choices=list(QUICK_SNIPPETS.keys()),
                            help="Built-in test snippet to analyze")
    
    # Pointer analysis config
    parser.add_argument("--k", type=int, default=2,
                       help="Context sensitivity depth (default: 2)")
    parser.add_argument("--obj-depth", type=int, default=2,
                       help="Object sensitivity depth (default: 2)")
    parser.add_argument("--field-sensitivity", choices=["attr", "elem", "value", "full"],
                       default="attr", help="Field sensitivity mode (default: attr)")
    parser.add_argument("--containers-mode", default="precise",
                       help="Container handling mode (default: precise)")
    parser.add_argument("--no-pointer", action="store_true",
                       help="Skip pointer analysis (use mock results)")
    
    # AI analysis config
    parser.add_argument("--verbose", action="store_true",
                       help="Enable verbose output")
    parser.add_argument("--timeout", type=int, default=300,
                       help="Analysis timeout in seconds (default: 300)")
    parser.add_argument("--widening", action="store_true",
                       help="Enable widening for faster convergence")
    
    # Filters
    parser.add_argument("--function", help="Target specific function for analysis")
    parser.add_argument("--entry", help="Set entry point function name")
    
    # Output options
    parser.add_argument("--dump-ir", nargs="?", const=True, metavar="PATH",
                       help="Dump IR representations (optionally to file)")
    parser.add_argument("--dump-cfg", nargs="?", const=True, metavar="PATH",
                       help="Dump CFG information (optionally to file)")
    parser.add_argument("--dump-callgraph", nargs="?", const=True, metavar="PATH",
                       help="Dump call graph (optionally to file)")
    parser.add_argument("--dump-ai-state", metavar="PATH",
                       help="Dump AI analysis state to JSON file")
    
    args = parser.parse_args()
    
    # Determine input file
    if args.file:
        file_path = os.path.abspath(args.file)
        if not os.path.exists(file_path):
            print(f"Error: File not found: {file_path}")
            return 1
    elif args.module:
        # Create a temporary file that imports the module
        content = f"import {args.module}\n"
        file_path = create_temp_file(f"module_{args.module}", content)
    elif args.snippet:
        content = QUICK_SNIPPETS[args.snippet]
        file_path = create_temp_file(f"snippet_{args.snippet}", content)
    
    # Run analysis
    success = run_ai_analysis(file_path, args)
    
    # Clean up temporary files if created
    if args.module or args.snippet:
        try:
            os.unlink(file_path)
        except Exception:
            pass
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
