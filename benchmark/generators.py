"""
Benchmark program with generators, iterators, and coroutines to test
how the analyzer handles these Python features.
"""

# Simple generator function
def count_up_to(n):
    """Generate numbers from 0 up to n."""
    i = 0
    while i < n:
        yield i
        i += 1

# Generator with yield from
def chain_generators(gen1, gen2):
    """Chain multiple generators together."""
    yield from gen1
    yield from gen2

# Generator expression
def generate_squares(n):
    """Return a generator expression for squares."""
    return (x**2 for x in range(n))

# Generator with send method
def echo_generator():
    """Echo back values sent to the generator."""
    value = yield "Ready"
    while True:
        value = yield value

# Generator with throw and close handling
def generator_with_exception_handling():
    """Handle exceptions thrown into the generator."""
    try:
        yield 1
        yield 2
        yield 3
    except ValueError:
        yield "ValueError caught"
    except Exception as e:
        yield f"Other exception caught: {e}"
    finally:
        yield "Generator closing"

# Custom iterator class
class Fibonacci:
    """Iterator that generates Fibonacci sequence up to n."""
    
    def __init__(self, n):
        self.n = n
        self.a, self.b = 0, 1
        self.count = 0
    
    def __iter__(self):
        return self
    
    def __next__(self):
        if self.count >= self.n:
            raise StopIteration
        
        result = self.a
        self.a, self.b = self.b, self.a + self.b
        self.count += 1
        
        return result

# Class with __getitem__ for iteration
class CustomRange:
    """Class that supports iteration through __getitem__."""
    
    def __init__(self, start, end, step=1):
        self.start = start
        self.end = end
        self.step = step
    
    def __getitem__(self, index):
        value = self.start + index * self.step
        if value >= self.end:
            raise IndexError
        return value

# Infinite generator
def infinite_counter(start=0, step=1):
    """Generate an infinite sequence of numbers."""
    count = start
    while True:
        yield count
        count += step

# Generator pipeline
def pipeline_example(numbers):
    """Example of a generator pipeline."""
    
    def filter_even(nums):
        for n in nums:
            if n % 2 == 0:
                yield n
    
    def multiply_by_three(nums):
        for n in nums:
            yield n * 3
    
    def add_ten(nums):
        for n in nums:
            yield n + 10
    
    # Create pipeline
    even_numbers = filter_even(numbers)
    multiplied = multiply_by_three(even_numbers)
    result = add_ten(multiplied)
    
    return result

# Generator that yields another generator
def nested_generator(n):
    """Generator that yields another generator."""
    def inner_generator(count):
        for i in range(count):
            yield f"Inner {i}"
    
    for i in range(n):
        yield inner_generator(i + 1)

# Multiple yield points
def multiple_yield_points(items):
    """Generator with multiple yield points and conditionals."""
    for item in items:
        if item % 3 == 0:
            yield f"Divisible by 3: {item}"
        elif item % 2 == 0:
            yield f"Even: {item}"
        else:
            yield f"Odd: {item}"

# Generator with local state
def accumulator_generator():
    """Generator that maintains state between yields."""
    total = 0
    count = 0
    
    while True:
        value = yield
        
        if value is None:
            break
        
        total += value
        count += 1
        
        yield {
            "total": total,
            "count": count,
            "average": total / count
        }

def demonstrate_generators():
    """Function to demonstrate generators and iterators."""
    
    # Simple generator
    print("Simple generator:")
    for num in count_up_to(5):
        print(num, end=" ")
    print()
    
    # Chain generators
    print("\nChained generators:")
    gen1 = (x for x in range(3))
    gen2 = (x * x for x in range(3, 6))
    for num in chain_generators(gen1, gen2):
        print(num, end=" ")
    print()
    
    # Generator expression
    print("\nGenerator expression:")
    squares = generate_squares(5)
    for square in squares:
        print(square, end=" ")
    print()
    
    # Echo generator
    print("\nEcho generator:")
    echo = echo_generator()
    print(next(echo))  # "Ready"
    print(echo.send("Hello"))  # "Hello"
    print(echo.send(42))  # 42
    
    # Generator with exception handling
    print("\nGenerator with exception handling:")
    gen = generator_with_exception_handling()
    print(next(gen))
    print(next(gen))
    print(gen.throw(ValueError))
    try:
        gen.close()
    except StopIteration:
        pass
    
    # Custom iterator
    print("\nFibonacci iterator:")
    fib = Fibonacci(7)
    for num in fib:
        print(num, end=" ")
    print()
    
    # Custom range
    print("\nCustom range:")
    for num in CustomRange(1, 10, 2):
        print(num, end=" ")
    print()
    
    # Infinite generator (limited)
    print("\nInfinite generator (limited):")
    counter = infinite_counter()
    for _ in range(5):
        print(next(counter), end=" ")
    print()
    
    # Generator pipeline
    print("\nGenerator pipeline:")
    numbers = range(10)
    for result in pipeline_example(numbers):
        print(result, end=" ")
    print()
    
    # Nested generator
    print("\nNested generator:")
    for inner_gen in nested_generator(3):
        for value in inner_gen:
            print(value, end=" ")
    print()
    
    # Multiple yield points
    print("\nMultiple yield points:")
    for result in multiple_yield_points(range(1, 7)):
        print(result)
    
    # Accumulator generator
    print("\nAccumulator generator:")
    acc = accumulator_generator()
    next(acc)  # Prime the generator
    for i in range(1, 5):
        acc.send(i)
        result = next(acc)
        print(f"After sending {i}: {result}")
    
    return {
        "simple_generator": list(count_up_to(5)),
        "generator_expression": list(generate_squares(5)),
        "fibonacci": list(Fibonacci(7)),
        "custom_range": list(CustomRange(1, 10, 2)),
        "pipeline_result": list(pipeline_example(range(10)))
    }

if __name__ == "__main__":
    demonstrate_generators() 