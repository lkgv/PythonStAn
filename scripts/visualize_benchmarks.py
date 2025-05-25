#!/usr/bin/env python3
"""
Script to visualize the results of PythonStAn analyses on benchmark programs.
Generates visualizations for CFGs, call graphs, and other analyses.
"""
import os
import sys
import ast
import argparse
from pathlib import Path

from pythonstan.analysis.transform import ThreeAddressTransformer
from pythonstan.graph.cfg.builder import CFGBuilder, StmtCFGTransformer
from pythonstan.graph.cfg.visualize import draw_module, new_digraph
from pythonstan.analysis.dataflow import DataflowAnalysisDriver
from pythonstan.analysis.analysis import AnalysisConfig

# Get the absolute path to the project root
PROJECT_ROOT = Path(__file__).parent.parent.absolute()
BENCHMARK_DIR = PROJECT_ROOT / 'benchmark'
OUTPUT_DIR = PROJECT_ROOT / 'output'

def visualize_cfg(benchmark_file, output_dir=None):
    """Generate a visualization of the control flow graph for a benchmark file."""
    benchmark_path = os.path.abspath(benchmark_file)
    
    if not os.path.exists(benchmark_path):
        print(f"Benchmark file not found: {benchmark_path}")
        return False
    
    # Set up the output directory
    if output_dir is None:
        output_dir = OUTPUT_DIR / 'cfg'
    os.makedirs(output_dir, exist_ok=True)
    
    base_name = os.path.splitext(os.path.basename(benchmark_path))[0]
    output_path = os.path.join(output_dir, f"{base_name}_cfg")
    
    print(f"\n===== Generating CFG for {os.path.basename(benchmark_path)} =====")
    
    try:
        # Parse the file and transform to three-address form
        with open(benchmark_path, 'r') as f:
            source = f.read()
        
        ta_trans = ThreeAddressTransformer()
        src_ast = ast.parse(source)
        ta_src = ta_trans.visit(src_ast)
        
        # Build the CFG
        cfg_builder = CFGBuilder()
        cfg_mod = cfg_builder.build_module(ta_src.body)
        cfg_mod = StmtCFGTransformer().trans(cfg_mod)
        
        # Generate the visualization
        g = new_digraph('G', filename=f"{output_path}.gv")
        draw_module(cfg_mod, g)
        g.render(format='pdf', cleanup=True)
        
        print(f"CFG visualization saved to {output_path}.pdf")
        return True
    
    except Exception as e:
        print(f"Error generating CFG for {benchmark_path}: {e}")
        return False

def visualize_dataflow(benchmark_file, analysis_name, output_dir=None):
    """Generate a visualization of dataflow analysis results for a benchmark file."""
    benchmark_path = os.path.abspath(benchmark_file)
    
    if not os.path.exists(benchmark_path):
        print(f"Benchmark file not found: {benchmark_path}")
        return False
    
    # Set up the output directory
    if output_dir is None:
        output_dir = OUTPUT_DIR / 'dataflow'
    os.makedirs(output_dir, exist_ok=True)
    
    base_name = os.path.splitext(os.path.basename(benchmark_path))[0]
    output_path = os.path.join(output_dir, f"{base_name}_{analysis_name}")
    
    print(f"\n===== Generating {analysis_name} visualization for {os.path.basename(benchmark_path)} =====")
    
    try:
        # Parse the file and transform to three-address form
        with open(benchmark_path, 'r') as f:
            source = f.read()
        
        ta_trans = ThreeAddressTransformer()
        src_ast = ast.parse(source)
        ta_src = ta_trans.visit(src_ast)
        
        # Build the CFG
        cfg_builder = CFGBuilder()
        cfg_mod = cfg_builder.build_module(ta_src.body)
        cfg_mod = StmtCFGTransformer().trans(cfg_mod)
        
        # Set up the analysis
        if analysis_name == "reaching_definition":
            analysis_id = "ReachingDefinitionAnalysis"
            options = {'type': 'dataflow analysis', 'solver': 'WorklistSolver'}
        elif analysis_name == "liveness":
            analysis_id = "LivenessAnalysis"
            options = {'type': 'dataflow analysis'}
        else:
            print(f"Unsupported analysis type: {analysis_name}")
            return False
        
        # Run the analysis
        config = AnalysisConfig(analysis_name, analysis_id, options=options)
        df_driver = DataflowAnalysisDriver(config)
        df_driver.analyze(cfg_mod)
        results = df_driver.results
        
        # Prepare the visualization data
        info = {}
        for blk in results['in'].keys():
            res_in = results['in'].get(blk, {*()})
            res_out = results['out'].get(blk, {*()})
            
            if len(res_in) == 0:
                res_in_str = ""
            else:
                res_in_str = '\\n'.join([ast.unparse(x) if isinstance(x, ast.AST) else str(x) for x in res_in])
            
            if len(res_out) == 0:
                res_out_str = ""
            else:
                res_out_str = '\\n'.join([ast.unparse(x) if isinstance(x, ast.AST) else str(x) for x in res_out])
            
            info[blk] = f"{res_in_str} | {res_out_str}"
        
        # Generate the visualization
        g = new_digraph('G', filename=f"{output_path}.gv")
        draw_module(cfg_mod, g, info)
        g.render(format='pdf', cleanup=True)
        
        print(f"{analysis_name.capitalize()} visualization saved to {output_path}.pdf")
        return True
    
    except Exception as e:
        print(f"Error generating {analysis_name} visualization for {benchmark_path}: {e}")
        return False

def visualize_all_benchmarks(analyses=None, output_dir=None):
    """Generate visualizations for all benchmark files."""
    success_count = 0
    failure_count = 0
    
    if not os.path.isdir(BENCHMARK_DIR):
        print(f"Benchmark directory not found: {BENCHMARK_DIR}")
        return
    
    # Get all Python files in the benchmark directory
    benchmark_files = list(BENCHMARK_DIR.glob("*.py"))
    
    print(f"Found {len(benchmark_files)} benchmark files")
    
    if analyses is None or "cfg" in analyses:
        print("\n----- Generating CFG Visualizations -----")
        cfg_output_dir = OUTPUT_DIR / 'cfg' if output_dir is None else Path(output_dir) / 'cfg'
        
        for benchmark_file in benchmark_files:
            try:
                success = visualize_cfg(benchmark_file, cfg_output_dir)
                if success:
                    success_count += 1
                else:
                    failure_count += 1
            except Exception as e:
                print(f"Error visualizing CFG for {benchmark_file}: {e}")
                failure_count += 1
    
    dataflow_analyses = ["reaching_definition", "liveness"]
    for analysis_name in dataflow_analyses:
        if analyses is None or analysis_name in analyses:
            print(f"\n----- Generating {analysis_name.capitalize()} Visualizations -----")
            df_output_dir = OUTPUT_DIR / 'dataflow' if output_dir is None else Path(output_dir) / 'dataflow'
            
            for benchmark_file in benchmark_files:
                try:
                    success = visualize_dataflow(benchmark_file, analysis_name, df_output_dir)
                    if success:
                        success_count += 1
                    else:
                        failure_count += 1
                except Exception as e:
                    print(f"Error visualizing {analysis_name} for {benchmark_file}: {e}")
                    failure_count += 1
    
    print(f"\n===== Visualization Summary =====")
    print(f"Total operations: {success_count + failure_count}")
    print(f"Successful: {success_count}")
    print(f"Failed: {failure_count}")

def main():
    parser = argparse.ArgumentParser(description="Visualize PythonStAn analyses on benchmark programs")
    parser.add_argument(
        "benchmark", nargs="?",
        help="Specific benchmark file to visualize (if not specified, visualizes all benchmarks)"
    )
    parser.add_argument(
        "--analyses", "-a", nargs="+", choices=["cfg", "reaching_definition", "liveness"],
        help="Specific analyses to visualize (default: all)"
    )
    parser.add_argument(
        "--output-dir", "-o",
        help="Directory to save visualizations"
    )
    parser.add_argument(
        "--list", "-l", action="store_true",
        help="List available benchmarks and exit"
    )
    
    args = parser.parse_args()
    
    if args.list:
        print("Available benchmarks:")
        if os.path.isdir(BENCHMARK_DIR):
            for benchmark_file in sorted(BENCHMARK_DIR.glob("*.py")):
                print(f"  {benchmark_file.name}")
        else:
            print(f"  Benchmark directory not found: {BENCHMARK_DIR}")
        return
    
    # Create the output directory if it doesn't exist
    output_dir = args.output_dir if args.output_dir else OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)
    
    if args.benchmark:
        # Visualize a specific benchmark
        benchmark_path = args.benchmark
        if not os.path.isabs(benchmark_path):
            # If a relative path is given, check in the benchmark directory
            potential_path = BENCHMARK_DIR / benchmark_path
            if potential_path.exists():
                benchmark_path = potential_path
        
        if args.analyses is None or "cfg" in args.analyses:
            visualize_cfg(benchmark_path, Path(output_dir) / 'cfg')
        
        if args.analyses is None or any(a in args.analyses for a in ["reaching_definition", "liveness"]):
            df_analyses = ["reaching_definition", "liveness"] if args.analyses is None else \
                [a for a in args.analyses if a in ["reaching_definition", "liveness"]]
            
            for analysis in df_analyses:
                visualize_dataflow(benchmark_path, analysis, Path(output_dir) / 'dataflow')
    else:
        # Visualize all benchmarks
        visualize_all_benchmarks(args.analyses, output_dir)

if __name__ == "__main__":
    main() 