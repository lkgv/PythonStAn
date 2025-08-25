"""
Benchmark program with closures and nested functions to test
how the analyzer handles lexical scoping, free variables, 
and nested function definitions.
"""

# Simple closure
def make_counter():
    """Create a counter function that remembers its state."""
    count = 0
    
    def counter():
        nonlocal count
        count += 1
        return count
    
    return counter

# Closure with parameters
def make_adder(x):
    """Create a function that adds x to its argument."""
    def adder(y):
        return x + y
    
    return adder

# Closure that modifies multiple variables
def make_accumulator(initial_value=0):
    """Create an accumulator that tracks sum, count, and average."""
    total = initial_value
    count = 0
    
    def accumulate(value):
        nonlocal total, count
        total += value
        count += 1
        return {
            'total': total,
            'count': count,
            'average': total / count if count > 0 else 0
        }
    
    return accumulate

# Multiple nested functions
def outer_function(x):
    """Function with multiple levels of nested functions."""
    
    def middle_function(y):
        
        def inner_function(z):
            # Access variables from all three scopes
            return x + y + z
        
        return inner_function
    
    return middle_function

# Function factory with shared state
def create_function_family():
    """Create a family of functions that share state."""
    shared_list = []
    
    def add_item(item):
        shared_list.append(item)
        return shared_list
    
    def remove_item(item):
        if item in shared_list:
            shared_list.remove(item)
        return shared_list
    
    def clear_items():
        shared_list.clear()
        return shared_list
    
    def get_items():
        return shared_list.copy()
    
    return {
        'add': add_item,
        'remove': remove_item,
        'clear': clear_items,
        'get': get_items
    }

# Mutually recursive functions
def create_even_odd_checkers():
    """Create mutually recursive functions."""
    
    def is_even(n):
        if n == 0:
            return True
        return is_odd(n - 1)
    
    def is_odd(n):
        if n == 0:
            return False
        return is_even(n - 1)
    
    return is_even, is_odd

# Function with local function redefinition
def function_redefiner(flag):
    """Redefine a local function based on a condition."""
    
    if flag:
        def local_func(x):
            return x * 2
    else:
        def local_func(x):
            return x * 3
    
    return local_func

# Closure with default arguments
def make_multiplier(factor=2):
    """Create a function that multiplies by a factor."""
    
    def multiplier(x, extra=0):
        return x * factor + extra
    
    return multiplier

def demonstrate_closures():
    """Function to demonstrate closures and nested functions."""
    
    # Simple counter
    counter = make_counter()
    for _ in range(3):
        print(f"Counter value: {counter()}")
    
    # Adder
    add_five = make_adder(5)
    print(f"5 + 10 = {add_five(10)}")
    
    # Accumulator
    acc = make_accumulator()
    numbers = [1, 3, 5, 7, 9]
    for num in numbers:
        result = acc(num)
    print(f"Accumulator result: {result}")
    
    # Nested functions
    nested = outer_function(10)(20)(30)
    print(f"Nested function result: {nested}")
    
    # Function family
    functions = create_function_family()
    functions['add']("apple")
    functions['add']("banana")
    functions['add']("cherry")
    functions['remove']("banana")
    print(f"Shared state: {functions['get']()}")
    
    # Mutually recursive functions
    is_even, is_odd = create_even_odd_checkers()
    print(f"Is 10 even? {is_even(10)}")
    print(f"Is 7 odd? {is_odd(7)}")
    
    # Function redefinition
    double = function_redefiner(True)
    triple = function_redefiner(False)
    print(f"Double 5: {double(5)}")
    print(f"Triple 5: {triple(5)}")
    
    # Multiplier with default args
    double_func = make_multiplier()
    triple_func = make_multiplier(3)
    print(f"Double 7 with extra 1: {double_func(7, 1)}")
    print(f"Triple 7: {triple_func(7)}")
    
    return counter, add_five, acc, functions

if __name__ == "__main__":
    demonstrate_closures() 