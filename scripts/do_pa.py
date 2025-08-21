#!/usr/bin/env python3
"""Pointer Analysis demo script.

This script demonstrates the k-CFA pointer analysis on complex
inter-procedural and multi-class examples.
"""

import ast
import os
import sys
import tempfile
from pathlib import Path

# Add pythonstan to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pythonstan.world.pipeline import Pipeline
from pythonstan.world import World


# Complex test cases with inter-procedural and multi-class scenarios
TEST_CASES = {
    "simple_interprocedural": '''
def create_data():
    data = {"key": "value"}
    return data

def process_data(data_dict):
    result = data_dict["key"]
    return result

def main():
    data = create_data()
    result = process_data(data)
    return result

if __name__ == "__main__":
    main()
''',

    "complex_oop": '''
class DataProcessor:
    def __init__(self, name):
        self.name = name
        self.data = []
    
    def add_data(self, item):
        self.data.append(item)
        return self
    
    def process(self):
        result = []
        for item in self.data:
            result.append(item.upper() if isinstance(item, str) else str(item))
        return result

class ResultHandler:
    def __init__(self, processor):
        self.processor = processor
        self.results = None
    
    def handle(self):
        self.results = self.processor.process()
        return self.results

def create_processor(name):
    return DataProcessor(name)

def setup_pipeline():
    processor = create_processor("main_processor")
    processor.add_data("hello")
    processor.add_data("world")
    processor.add_data(42)
    
    handler = ResultHandler(processor)
    return handler

def main():
    handler = setup_pipeline()
    results = handler.handle()
    return results

if __name__ == "__main__":
    main()
''',

    "higher_order_functions": '''
def apply_transform(data, transform_func):
    """Apply a transformation function to data."""
    return transform_func(data)

def create_multiplier(factor):
    """Create a multiplier function."""
    def multiply(x):
        return x * factor
    return multiply

def create_adder(amount):
    """Create an adder function."""
    def add(x):
        return x + amount
    return add

def compose_functions(f1, f2):
    """Compose two functions."""
    def composed(x):
        return f1(f2(x))
    return composed

def process_numbers(numbers):
    """Process a list of numbers with various transformations."""
    # Create transformation functions
    double = create_multiplier(2)
    add_ten = create_adder(10)
    
    # Compose functions
    double_then_add = compose_functions(add_ten, double)
    
    results = []
    for num in numbers:
        # Apply different transformations
        doubled = apply_transform(num, double)
        added = apply_transform(num, add_ten)
        composed = apply_transform(num, double_then_add)
        
        results.append({
            "original": num,
            "doubled": doubled,
            "added": added,
            "composed": composed
        })
    
    return results

def main():
    numbers = [1, 2, 3, 4, 5]
    results = process_numbers(numbers)
    return results

if __name__ == "__main__":
    main()
''',

    "complex_containers": '''
class Container:
    def __init__(self):
        self.data = {}
        self.items = []
        self.metadata = {"created": True}
    
    def add_item(self, key, value):
        self.data[key] = value
        self.items.append(value)
        return self
    
    def get_item(self, key):
        return self.data.get(key)
    
    def get_all_items(self):
        return list(self.items)

class NestedContainer:
    def __init__(self):
        self.containers = {}
        self.primary = Container()
    
    def create_container(self, name):
        container = Container()
        self.containers[name] = container
        return container
    
    def get_container(self, name):
        return self.containers.get(name, self.primary)
    
    def merge_containers(self, name1, name2):
        c1 = self.get_container(name1)
        c2 = self.get_container(name2)
        
        merged = Container()
        for item in c1.get_all_items():
            merged.items.append(item)
        for item in c2.get_all_items():
            merged.items.append(item)
        
        return merged

def create_complex_structure():
    """Create a complex nested structure."""
    nested = NestedContainer()
    
    # Add data to primary container
    nested.primary.add_item("first", "value1")
    nested.primary.add_item("second", [1, 2, 3])
    
    # Create named containers
    c1 = nested.create_container("container1")
    c1.add_item("data", {"nested": "dict"})
    c1.add_item("list", ["a", "b", "c"])
    
    c2 = nested.create_container("container2")
    c2.add_item("numbers", [10, 20, 30])
    c2.add_item("text", "hello world")
    
    # Merge containers
    merged = nested.merge_containers("container1", "container2")
    
    return nested, merged

def analyze_structure(nested, merged):
    """Analyze the created structure."""
    results = {}
    
    # Get data from various sources
    results["primary_items"] = nested.primary.get_all_items()
    results["c1_data"] = nested.get_container("container1").get_item("data")
    results["c2_numbers"] = nested.get_container("container2").get_item("numbers")
    results["merged_items"] = merged.get_all_items()
    
    return results

def main():
    nested, merged = create_complex_structure()
    results = analyze_structure(nested, merged)
    return results

if __name__ == "__main__":
    main()
''',

    "exception_handling": '''
class CustomError(Exception):
    def __init__(self, message, data=None):
        super().__init__(message)
        self.data = data or {}

class ErrorHandler:
    def __init__(self):
        self.errors = []
        self.recovery_data = {}
    
    def handle_error(self, error):
        self.errors.append(error)
        if hasattr(error, 'data'):
            self.recovery_data.update(error.data)
        return self.recovery_data

def risky_operation(data):
    """An operation that might fail."""
    if not data:
        raise CustomError("No data provided", {"attempted": data})
    
    if isinstance(data, str) and "error" in data:
        raise CustomError("Error in data", {"data": data})
    
    return {"processed": data, "status": "success"}

def safe_operation(data, handler):
    """Safely execute an operation with error handling."""
    try:
        result = risky_operation(data)
        return result
    except CustomError as e:
        recovery = handler.handle_error(e)
        return {"processed": None, "status": "error", "recovery": recovery}
    except Exception as e:
        generic_error = CustomError(f"Unexpected error: {e}", {"original": str(e)})
        recovery = handler.handle_error(generic_error)
        return {"processed": None, "status": "fatal", "recovery": recovery}

def process_batch(data_list):
    """Process a batch of data items."""
    handler = ErrorHandler()
    results = []
    
    for item in data_list:
        result = safe_operation(item, handler)
        results.append(result)
    
    return results, handler

def main():
    test_data = [
        "valid_data",
        "",  # Will cause error
        "data_with_error",  # Will cause error
        {"dict": "data"},
        None  # Will cause error
    ]
    
    results, handler = process_batch(test_data)
    return {
        "results": results,
        "errors": handler.errors,
        "recovery_data": handler.recovery_data
    }

if __name__ == "__main__":
    main()
'''
}


def create_test_file(name: str, content: str) -> str:
    """Create a temporary test file and return its path."""
    # Create a temporary directory for test files
    test_dir = Path(tempfile.gettempdir()) / "pythonstan_pa_test"
    test_dir.mkdir(exist_ok=True)
    
    test_file = test_dir / f"{name}.py"
    test_file.write_text(content)
    return str(test_file)


def create_pointer_analysis_config(test_name: str, test_file: str) -> dict:
    """Create configuration for pointer analysis."""
    return {
        "filename": test_file,
        "project_path": str(Path(test_file).parent),
        "library_paths": [
            "/usr/lib/python3.9",
            "/usr/lib/python3.9/site-packages"
        ],
        "analysis": [
            {
                "name": f"pointer_analysis_{test_name}",
                "id": "PointerAnalysis",
                "description": f"k-CFA pointer analysis for {test_name}",
                "prev_analysis": [],
                "options": {
                    "type": "pointer analysis",
                    "k": 2,
                    "obj_depth": 2,
                    "field_sensitivity": "attr",
                    "verbose": True
                }
            }
        ]
    }


def run_pointer_analysis(test_name: str, test_content: str):
    """Run pointer analysis on a test case."""
    print(f"\n{'='*60}")
    print(f"Running Pointer Analysis: {test_name}")
    print(f"{'='*60}")
    
    try:
        # Create test file
        test_file = create_test_file(test_name, test_content)
        print(f"Created test file: {test_file}")
        
        # Create configuration
        config = create_pointer_analysis_config(test_name, test_file)
        
        # Create and run pipeline
        print("Initializing analysis pipeline...")
        pipeline = Pipeline(config=config)
        
        print("Running analysis...")
        pipeline.run()
        
        # Get world and results
        world = pipeline.get_world()
        print(f"Analysis completed for {len(world.scope_manager.get_scopes())} scopes")
        
        # Print scope information
        print("\nAnalyzed scopes:")
        for scope in world.scope_manager.get_scopes():
            print(f"  - {scope.get_qualname()}")
        
        # Get analysis results
        analysis_manager = pipeline.analysis_manager
        if hasattr(analysis_manager, 'get_results'):
            # Debug: print available result keys
            print(f"Available result keys: {list(analysis_manager.results.keys())}")
            
            analysis_name = f"pointer_analysis_{test_name}"
            try:
                results = analysis_manager.get_results(analysis_name)
            except KeyError:
                print(f"Result key '{analysis_name}' not found, trying to find pointer analysis results...")
                # Try to find any pointer analysis results
                pointer_results = None
                for key, value in analysis_manager.results.items():
                    if "pointer" in key.lower():
                        pointer_results = value
                        print(f"Found pointer analysis results under key: {key}")
                        break
                results = pointer_results
                
            if results:
                print(f"\nPointer Analysis Results:")
                print(f"  Total scopes analyzed: {len(results)}")
                
                for scope, scope_results in results.items():
                    print(f"\n  Scope: {scope.get_qualname()}")
                    if "statistics" in scope_results:
                        stats = scope_results["statistics"]
                        print(f"    Statistics: {stats}")
                    
                    if "error" in scope_results:
                        print(f"    Error: {scope_results['error']}")
                    else:
                        points_to = scope_results.get("points_to", {})
                        call_graph = scope_results.get("call_graph", {})
                        print(f"    Points-to entries: {len(points_to)}")
                        print(f"    Call graph edges: {call_graph.get('total_cs_edges', 0)}")
                        if call_graph:
                            print(f"    Call sites: {call_graph.get('unique_call_sites', 0)}")
                            print(f"    Functions: {call_graph.get('unique_functions', 0)}")
                            print(f"    Contexts: {call_graph.get('contexts_with_calls', 0)}")
                        
                        # Show some points-to information
                        if points_to:
                            print("    Sample points-to information:")
                            for i, (var, pts) in enumerate(points_to.items()):
                                if i >= 3:  # Limit output
                                    break
                                print(f"      {var} -> {pts}")
            else:
                print("No results available")
        else:
            print("Analysis manager does not support result retrieval")
        
        return True
        
    except Exception as e:
        print(f"Error running pointer analysis for {test_name}: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main function to run all test cases."""
    print("PythonStAn k-CFA Pointer Analysis Demo")
    print("======================================")
    
    success_count = 0
    total_count = len(TEST_CASES)
    
    for test_name, test_content in TEST_CASES.items():
        try:
            success = run_pointer_analysis(test_name, test_content)
            if success:
                success_count += 1
        except KeyboardInterrupt:
            print("\nInterrupted by user")
            break
        except Exception as e:
            print(f"Unexpected error in {test_name}: {e}")
    
    print(f"\n{'='*60}")
    print(f"Summary: {success_count}/{total_count} test cases completed successfully")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
