"""
Benchmark program with various call patterns for testing call graph analysis.
Includes direct calls, indirect calls through variables, decorators, and more.
"""

# Global function
def global_function(x):
    return x * 2

# Decorator example
def my_decorator(func):
    def wrapper(*args, **kwargs):
        print(f"Calling {func.__name__}")
        return func(*args, **kwargs)
    return wrapper

@my_decorator
def decorated_function(x, y):
    return x + y

# Higher-order functions
def apply_operation(func, value):
    return func(value)

def get_operation(op_type):
    if op_type == "double":
        return lambda x: x * 2
    elif op_type == "square":
        return lambda x: x ** 2
    else:
        return lambda x: x

# Recursive function
def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n-1)

# Class with methods
class Calculator:
    def __init__(self, initial_value=0):
        self.value = initial_value
        
    def add(self, x):
        self.value += x
        return self.value
        
    def multiply(self, x):
        self.value *= x
        return self.value
    
    def operation(self, op_type, x):
        if op_type == "add":
            return self.add(x)
        elif op_type == "multiply":
            return self.multiply(x)
        else:
            return self.value
    
    @staticmethod
    def static_method(x, y):
        return x + y

# Indirect calls
def indirect_calls():
    funcs = [global_function, factorial, Calculator.static_method]
    
    results = []
    for f in funcs:
        if f.__name__ == "static_method":
            results.append(f(10, 20))
        else:
            results.append(f(5))
    
    return results

# Main function to call all others
def main():
    # Direct calls
    global_function(10)
    decorated_function(5, 10)
    
    # Higher-order function calls
    double_func = get_operation("double")
    apply_operation(double_func, 10)
    
    # Recursive call
    factorial(5)
    
    # Method calls
    calc = Calculator(5)
    calc.add(10)
    calc.multiply(2)
    calc.operation("add", 5)
    
    # Static method call
    Calculator.static_method(10, 20)
    
    # Indirect calls
    indirect_calls()

if __name__ == "__main__":
    main() 