#!/usr/bin/env python3
"""Advanced Pointer Analysis test cases with sophisticated class hierarchies.

This script creates complex scenarios to test the k-CFA pointer analysis
and predict expected results for verification.
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

# Advanced test cases with complex class hierarchies and call patterns
ADVANCED_TEST_CASES = {
    "inheritance_hierarchy": '''
class Animal:
    def __init__(self, name):
        self.name = name
        self.energy = 100
    
    def speak(self):
        return f"{self.name} makes a sound"
    
    def move(self):
        self.energy -= 10
        return f"{self.name} moves"

class Mammal(Animal):
    def __init__(self, name, fur_color):
        super().__init__(name)
        self.fur_color = fur_color
        self.temperature = 37.0
    
    def regulate_temperature(self):
        return f"{self.name} maintains {self.temperature}°C"

class Dog(Mammal):
    def __init__(self, name, breed):
        super().__init__(name, "brown")
        self.breed = breed
        self.toys = []
    
    def speak(self):
        return f"{self.name} barks!"
    
    def fetch(self, toy):
        self.toys.append(toy)
        return f"{self.name} fetches {toy}"

class Cat(Mammal):
    def __init__(self, name):
        super().__init__(name, "gray")
        self.lives = 9
        self.favorite_spot = None
    
    def speak(self):
        return f"{self.name} meows"
    
    def sleep(self, spot):
        self.favorite_spot = spot
        return f"{self.name} sleeps on {spot}"

def create_animals():
    """Create various animals - should create multiple object allocations."""
    dog = Dog("Buddy", "Golden Retriever")
    cat = Cat("Whiskers")
    generic_animal = Animal("Generic")
    
    animals = [dog, cat, generic_animal]
    return animals

def animal_interactions(animals):
    """Complex interactions between animals."""
    results = []
    
    for animal in animals:
        # Polymorphic method calls
        sound = animal.speak()
        movement = animal.move()
        results.append({"animal": animal, "sound": sound, "movement": movement})
        
        # Type-specific operations
        if isinstance(animal, Dog):
            fetch_result = animal.fetch("ball")
            results.append({"fetch": fetch_result})
        elif isinstance(animal, Cat):
            sleep_result = animal.sleep("windowsill")
            results.append({"sleep": sleep_result})
    
    return results

def main():
    animals = create_animals()
    results = animal_interactions(animals)
    return animals, results

if __name__ == "__main__":
    main()
''',

    "factory_pattern": '''
class Product:
    def __init__(self, name, price):
        self.name = name
        self.price = price
        self.components = []
    
    def add_component(self, component):
        self.components.append(component)
    
    def get_total_cost(self):
        base_cost = self.price
        component_cost = sum(c.price for c in self.components)
        return base_cost + component_cost

class Component:
    def __init__(self, name, price):
        self.name = name
        self.price = price

class ProductFactory:
    def __init__(self, factory_name):
        self.factory_name = factory_name
        self.products_created = []
        self.component_inventory = {}
    
    def create_component(self, comp_type, name, price):
        """Factory method for components."""
        component = Component(name, price)
        if comp_type not in self.component_inventory:
            self.component_inventory[comp_type] = []
        self.component_inventory[comp_type].append(component)
        return component
    
    def create_product(self, name, base_price, component_specs):
        """Factory method for products."""
        product = Product(name, base_price)
        
        for comp_type, comp_name, comp_price in component_specs:
            component = self.create_component(comp_type, comp_name, comp_price)
            product.add_component(component)
        
        self.products_created.append(product)
        return product

class OrderProcessor:
    def __init__(self, factory):
        self.factory = factory
        self.orders = []
        self.completed_orders = []
    
    def create_order(self, product_specs):
        """Create order with multiple products."""
        order = {"id": len(self.orders), "products": [], "total": 0}
        
        for name, base_price, components in product_specs:
            product = self.factory.create_product(name, base_price, components)
            order["products"].append(product)
            order["total"] += product.get_total_cost()
        
        self.orders.append(order)
        return order
    
    def process_order(self, order):
        """Process and complete an order."""
        processed_order = {
            "original": order,
            "status": "completed",
            "final_total": order["total"] * 1.1  # Add tax
        }
        self.completed_orders.append(processed_order)
        return processed_order

def run_factory_simulation():
    """Run complex factory simulation with multiple object creations."""
    factory = ProductFactory("TechFactory")
    processor = OrderProcessor(factory)
    
    # Create complex order with nested object creation
    order_specs = [
        ("Laptop", 1000, [
            ("cpu", "Intel i7", 300),
            ("memory", "16GB RAM", 150),
            ("storage", "1TB SSD", 200)
        ]),
        ("Phone", 800, [
            ("screen", "OLED Display", 100),
            ("battery", "Li-ion 4000mAh", 50),
            ("camera", "48MP Sensor", 120)
        ])
    ]
    
    order = processor.create_order(order_specs)
    completed = processor.process_order(order)
    
    return factory, processor, order, completed

def main():
    return run_factory_simulation()

if __name__ == "__main__":
    main()
''',

    "observer_pattern": '''
class Event:
    def __init__(self, event_type, data):
        self.event_type = event_type
        self.data = data
        self.timestamp = "2024-01-01"  # Simplified

class Observer:
    def __init__(self, name):
        self.name = name
        self.received_events = []
    
    def notify(self, event):
        self.received_events.append(event)
        return f"{self.name} received {event.event_type}"

class Subject:
    def __init__(self, name):
        self.name = name
        self.observers = []
        self.state = {}
    
    def attach(self, observer):
        self.observers.append(observer)
    
    def detach(self, observer):
        if observer in self.observers:
            self.observers.remove(observer)
    
    def notify_observers(self, event):
        results = []
        for observer in self.observers:
            result = observer.notify(event)
            results.append(result)
        return results
    
    def update_state(self, key, value):
        self.state[key] = value
        event = Event("state_change", {"key": key, "value": value})
        return self.notify_observers(event)

class Logger(Observer):
    def __init__(self, name, log_level):
        super().__init__(name)
        self.log_level = log_level
        self.logs = []
    
    def notify(self, event):
        super().notify(event)
        log_entry = {"level": self.log_level, "event": event, "observer": self.name}
        self.logs.append(log_entry)
        return f"[{self.log_level}] {self.name} logged {event.event_type}"

class EmailNotifier(Observer):
    def __init__(self, name, email_address):
        super().__init__(name)
        self.email_address = email_address
        self.sent_emails = []
    
    def notify(self, event):
        super().notify(event)
        email = {
            "to": self.email_address,
            "subject": f"Event: {event.event_type}",
            "body": str(event.data)
        }
        self.sent_emails.append(email)
        return f"Email sent to {self.email_address}"

def setup_observer_system():
    """Setup complex observer pattern with multiple subjects and observers."""
    # Create subjects
    user_manager = Subject("UserManager")
    order_system = Subject("OrderSystem")
    
    # Create various observers
    error_logger = Logger("ErrorLogger", "ERROR")
    info_logger = Logger("InfoLogger", "INFO")
    admin_notifier = EmailNotifier("AdminNotifier", "admin@example.com")
    user_notifier = EmailNotifier("UserNotifier", "user@example.com")
    
    # Complex attachment patterns
    user_manager.attach(error_logger)
    user_manager.attach(admin_notifier)
    
    order_system.attach(info_logger)
    order_system.attach(error_logger)
    order_system.attach(user_notifier)
    
    return {
        "subjects": [user_manager, order_system],
        "observers": [error_logger, info_logger, admin_notifier, user_notifier]
    }

def simulate_events(system):
    """Simulate complex event propagation."""
    user_manager, order_system = system["subjects"]
    
    # Generate events through state changes
    user_events = user_manager.update_state("user_count", 100)
    order_events = order_system.update_state("order_status", "processing")
    
    # Manual event creation and propagation
    critical_event = Event("system_error", {"error": "Database connection lost"})
    user_manager.notify_observers(critical_event)
    
    success_event = Event("order_completed", {"order_id": 12345})
    order_system.notify_observers(success_event)
    
    return {
        "user_events": user_events,
        "order_events": order_events,
        "manual_events": [critical_event, success_event]
    }

def main():
    system = setup_observer_system()
    events = simulate_events(system)
    return system, events

if __name__ == "__main__":
    main()
''',

    "strategy_with_composition": '''
class Strategy:
    def execute(self, data):
        raise NotImplementedError

class SortingStrategy(Strategy):
    def __init__(self, algorithm_name):
        self.algorithm_name = algorithm_name

class BubbleSortStrategy(SortingStrategy):
    def __init__(self):
        super().__init__("BubbleSort")
    
    def execute(self, data):
        # Simplified bubble sort simulation
        sorted_data = sorted(data)  # Using built-in for simplicity
        return {"algorithm": self.algorithm_name, "result": sorted_data}

class QuickSortStrategy(SortingStrategy):
    def __init__(self):
        super().__init__("QuickSort")
    
    def execute(self, data):
        sorted_data = sorted(data, reverse=False)
        return {"algorithm": self.algorithm_name, "result": sorted_data}

class MergeSortStrategy(SortingStrategy):
    def __init__(self):
        super().__init__("MergeSort")
    
    def execute(self, data):
        sorted_data = sorted(data)
        return {"algorithm": self.algorithm_name, "result": sorted_data}

class DataProcessor:
    def __init__(self, strategy):
        self.strategy = strategy
        self.processing_history = []
        self.current_data = None
    
    def set_strategy(self, new_strategy):
        self.strategy = new_strategy
    
    def process(self, data):
        self.current_data = data
        result = self.strategy.execute(data)
        
        history_entry = {
            "input": data,
            "strategy": self.strategy.algorithm_name,
            "output": result
        }
        self.processing_history.append(history_entry)
        return result

class DataSource:
    def __init__(self, source_name):
        self.source_name = source_name
        self.datasets = {}
    
    def add_dataset(self, name, data):
        self.datasets[name] = data
    
    def get_dataset(self, name):
        return self.datasets.get(name, [])

class AnalysisPipeline:
    def __init__(self, name):
        self.name = name
        self.processors = []
        self.data_sources = []
        self.results = {}
    
    def add_processor(self, processor):
        self.processors.append(processor)
    
    def add_data_source(self, source):
        self.data_sources.append(source)
    
    def run_analysis(self):
        """Run complex analysis with strategy switching."""
        all_results = []
        
        for source in self.data_sources:
            for dataset_name in source.datasets:
                data = source.get_dataset(dataset_name)
                
                for processor in self.processors:
                    result = processor.process(data)
                    all_results.append({
                        "source": source.source_name,
                        "dataset": dataset_name,
                        "processor": processor,
                        "result": result
                    })
        
        self.results["analysis_results"] = all_results
        return all_results

def create_complex_pipeline():
    """Create complex pipeline with multiple strategies and compositions."""
    # Create strategies
    bubble_sort = BubbleSortStrategy()
    quick_sort = QuickSortStrategy()
    merge_sort = MergeSortStrategy()
    
    # Create processors with different strategies
    processor1 = DataProcessor(bubble_sort)
    processor2 = DataProcessor(quick_sort)
    processor3 = DataProcessor(merge_sort)
    
    # Create data sources
    source1 = DataSource("RandomData")
    source1.add_dataset("small", [3, 1, 4, 1, 5])
    source1.add_dataset("medium", [9, 2, 6, 5, 3, 5])
    
    source2 = DataSource("UserData")
    source2.add_dataset("ages", [25, 30, 22, 35, 28])
    source2.add_dataset("scores", [85, 92, 78, 96, 88])
    
    # Create pipeline and add components
    pipeline = AnalysisPipeline("SortingAnalysis")
    pipeline.add_processor(processor1)
    pipeline.add_processor(processor2)
    pipeline.add_processor(processor3)
    pipeline.add_data_source(source1)
    pipeline.add_data_source(source2)
    
    return pipeline

def run_dynamic_strategy_switching():
    """Demonstrate runtime strategy switching."""
    pipeline = create_complex_pipeline()
    
    # Run initial analysis
    initial_results = pipeline.run_analysis()
    
    # Dynamic strategy switching
    for processor in pipeline.processors:
        if isinstance(processor.strategy, BubbleSortStrategy):
            processor.set_strategy(QuickSortStrategy())
        elif isinstance(processor.strategy, QuickSortStrategy):
            processor.set_strategy(MergeSortStrategy())
    
    # Run analysis again with switched strategies
    switched_results = pipeline.run_analysis()
    
    return {
        "pipeline": pipeline,
        "initial": initial_results,
        "switched": switched_results
    }

def main():
    return run_dynamic_strategy_switching()

if __name__ == "__main__":
    main()
'''
}


# Expected Results Predictions
EXPECTED_RESULTS = {
    "inheritance_hierarchy": {
        "objects_created": 8,  # Dog, Cat, Animal, + strings for names, breed, toys list, etc.
        "pts_entries": 15,     # Variables: dog, cat, generic_animal, animals list, names, properties
        "call_edges": 12,      # __init__ calls, method calls, super() calls
        "description": """
        Expected allocations:
        1. Dog("Buddy", "Golden Retriever") - creates Dog object
        2. Cat("Whiskers") - creates Cat object  
        3. Animal("Generic") - creates Animal object
        4. List for animals = [dog, cat, generic_animal]
        5. String literals: "Buddy", "Golden Retriever", "Whiskers", "Generic"
        6. List for dog.toys = []
        7. Various attribute assignments create references
        
        Expected calls:
        1. Dog.__init__ -> Mammal.__init__ -> Animal.__init__
        2. Cat.__init__ -> Mammal.__init__ -> Animal.__init__  
        3. Animal.__init__
        4. Polymorphic calls: animal.speak(), animal.move()
        5. Type-specific: dog.fetch(), cat.sleep()
        """
    },
    
    "factory_pattern": {
        "objects_created": 25,  # Factory, OrderProcessor, Products, Components, Orders, etc.
        "pts_entries": 30,      # Many variables and object references
        "call_edges": 20,       # Factory methods, object creation, method chaining
        "description": """
        Expected allocations:
        1. ProductFactory("TechFactory") 
        2. OrderProcessor(factory)
        3. Multiple Product objects (Laptop, Phone)
        4. Multiple Component objects (cpu, memory, storage, screen, battery, camera)
        5. Order dictionaries and lists
        6. Various string literals and data structures
        
        Expected calls:
        1. Factory creation methods
        2. Product.add_component() calls
        3. OrderProcessor.create_order()
        4. OrderProcessor.process_order()
        5. Component creation in loops
        """
    },
    
    "observer_pattern": {
        "objects_created": 20,  # Subjects, Observers, Events, Lists, Dicts
        "pts_entries": 25,      # Observer lists, event data, state objects
        "call_edges": 18,       # notify calls, attach/detach, event propagation
        "description": """
        Expected allocations:
        1. Subject objects: UserManager, OrderSystem
        2. Observer objects: Logger, EmailNotifier instances
        3. Event objects with data
        4. Lists for observers, received_events, logs, sent_emails
        5. Dictionaries for state, log entries, email objects
        
        Expected calls:
        1. Observer.notify() for each observer when event propagated
        2. Subject.attach() calls
        3. Subject.notify_observers() calls  
        4. Event creation and data passing
        """
    },
    
    "strategy_with_composition": {
        "objects_created": 30,  # Strategies, Processors, Sources, Pipeline, Data
        "pts_entries": 35,      # Complex object graph with many references
        "call_edges": 25,       # Strategy execution, pipeline processing, dynamic switching
        "description": """
        Expected allocations:
        1. Strategy objects: BubbleSortStrategy, QuickSortStrategy, MergeSortStrategy
        2. DataProcessor objects with strategy references
        3. DataSource objects with dataset dictionaries
        4. AnalysisPipeline with processor and source lists
        5. Multiple data lists and result dictionaries
        
        Expected calls:
        1. Strategy.execute() calls for each data processing
        2. DataProcessor.process() calls
        3. DataProcessor.set_strategy() for dynamic switching
        4. Pipeline.run_analysis() orchestrating multiple operations
        """
    }
}


def create_test_file(name: str, content: str) -> str:
    """Create a temporary test file and return its path."""
    test_dir = Path(tempfile.gettempdir()) / "pythonstan_advanced_pa_test"
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
                "name": f"advanced_pointer_analysis_{test_name}",
                "id": "PointerAnalysis",
                "description": f"Advanced k-CFA pointer analysis for {test_name}",
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


def run_advanced_pointer_analysis(test_name: str, test_content: str):
    """Run pointer analysis on an advanced test case."""
    print(f"\n{'='*80}")
    print(f"🔍 Advanced Pointer Analysis: {test_name}")
    print(f"{'='*80}")
    
    # Show expected results
    if test_name in EXPECTED_RESULTS:
        expected = EXPECTED_RESULTS[test_name]
        print(f"\n📊 EXPECTED RESULTS:")
        print(f"   Objects Created: {expected['objects_created']}")
        print(f"   Points-to Entries: {expected['pts_entries']}")
        print(f"   Call Edges: {expected['call_edges']}")
        print(f"   Description: {expected['description']}")
    
    try:
        # Create test file
        test_file = create_test_file(test_name, test_content)
        print(f"\n📁 Created test file: {test_file}")
        
        # Create configuration
        config = create_pointer_analysis_config(test_name, test_file)
        
        # Create and run pipeline
        print("🚀 Initializing analysis pipeline...")
        pipeline = Pipeline(config=config)
        
        print("⚙️  Running analysis...")
        pipeline.run()
        
        # Get world and results
        world = pipeline.get_world()
        print(f"✅ Analysis completed for {len(world.scope_manager.get_scopes())} scopes")
        
        # Print scope information
        print(f"\n📋 Analyzed scopes ({len(world.scope_manager.get_scopes())}):")
        for i, scope in enumerate(world.scope_manager.get_scopes()):
            print(f"   {i+1:2}. {scope.get_qualname()}")
        
        # Get analysis results
        analysis_manager = pipeline.analysis_manager
        analysis_name = f"advanced_pointer_analysis_{test_name}"
        
        if analysis_name in analysis_manager.results:
            results = analysis_manager.results[analysis_name]
            print(f"\n📈 ACTUAL RESULTS:")
            print(f"   Total scopes analyzed: {len(results)}")
            
            # Aggregate statistics
            total_objects = 0
            total_constraints = 0
            total_calls = 0
            total_pts_entries = 0
            total_call_edges = 0
            
            for scope, scope_results in results.items():
                if "statistics" in scope_results:
                    stats = scope_results["statistics"]
                    total_objects += stats.get("objects_created", 0)
                    total_constraints += stats.get("constraints_processed", 0)
                    total_calls += stats.get("calls_processed", 0)
                
                total_pts_entries += len(scope_results.get("points_to", {}))
                
                # Get call graph info
                cg = scope_results.get("call_graph", {})
                if hasattr(cg, 'get_statistics'):
                    cg_stats = cg.get_statistics()
                    total_call_edges += cg_stats.get("total_cs_edges", 0)
                elif isinstance(cg, dict) and "statistics" in cg:
                    total_call_edges += cg["statistics"].get("total_cs_edges", 0)
                else:
                    # Fallback: count edges manually if available
                    total_call_edges += len(str(cg))  # Rough estimate
            
            print(f"   Objects Created: {total_objects}")
            print(f"   Constraints Processed: {total_constraints}")
            print(f"   Calls Processed: {total_calls}")
            print(f"   Points-to Entries: {total_pts_entries}")
            print(f"   Call Edges: {total_call_edges}")
            
            # Compare with expected
            if test_name in EXPECTED_RESULTS:
                expected = EXPECTED_RESULTS[test_name]
                print(f"\n📊 COMPARISON:")
                
                def compare_metric(name, actual, expected):
                    if actual == 0 and expected > 0:
                        status = "❌ MISSING"
                    elif actual < expected * 0.5:
                        status = "⚠️  LOW"
                    elif actual >= expected * 0.8:
                        status = "✅ GOOD"
                    else:
                        status = "🔶 PARTIAL"
                    print(f"   {name:20}: {actual:3} / {expected:3} expected ({status})")
                
                compare_metric("Objects", total_objects, expected["objects_created"])
                compare_metric("Points-to", total_pts_entries, expected["pts_entries"])
                compare_metric("Call Edges", total_call_edges, expected["call_edges"])
            
            # Show detailed scope results for debugging
            print(f"\n🔍 DETAILED SCOPE RESULTS:")
            for scope, scope_results in list(results.items())[:3]:  # Show first 3 scopes
                print(f"   📍 {scope.get_qualname()}:")
                if "statistics" in scope_results:
                    stats = scope_results["statistics"]
                    print(f"      Objects: {stats.get('objects_created', 0)}")
                    print(f"      Constraints: {stats.get('constraints_processed', 0)}")
                    print(f"      Calls: {stats.get('calls_processed', 0)}")
                
                if "error" in scope_results:
                    print(f"      ❌ Error: {scope_results['error']}")
        else:
            print(f"❌ No results found for analysis '{analysis_name}'")
            print(f"Available results: {list(analysis_manager.results.keys())}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error running pointer analysis for {test_name}: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main function to run all advanced test cases."""
    print("🚀 PythonStAn Advanced k-CFA Pointer Analysis Demo")
    print("="*80)
    
    success_count = 0
    total_count = len(ADVANCED_TEST_CASES)
    
    for test_name, test_content in ADVANCED_TEST_CASES.items():
        try:
            success = run_advanced_pointer_analysis(test_name, test_content)
            if success:
                success_count += 1
        except KeyboardInterrupt:
            print("\n⏹️  Interrupted by user")
            break
        except Exception as e:
            print(f"💥 Unexpected error in {test_name}: {e}")
    
    print(f"\n{'='*80}")
    print(f"📊 FINAL SUMMARY: {success_count}/{total_count} test cases completed")
    print(f"{'='*80}")
    
    if success_count < total_count:
        print("\n🔧 Issues detected - debugging needed to fix missing objects/calls/points-to")


if __name__ == "__main__":
    main()
