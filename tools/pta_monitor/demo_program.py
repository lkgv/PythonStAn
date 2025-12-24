"""Demo program for testing PTA Monitor.

This small Python program demonstrates various call patterns that the pointer
analysis should handle. Use this to validate the monitor's functionality.
"""


class Animal:
    """Base class for animals."""
    
    def __init__(self, name: str):
        self.name = name
    
    def speak(self) -> str:
        return f"{self.name} makes a sound"


class Dog(Animal):
    """Dog class inheriting from Animal."""
    
    def speak(self) -> str:
        return f"{self.name} barks"
    
    def fetch(self, item: str) -> str:
        return f"{self.name} fetches {item}"


class Cat(Animal):
    """Cat class inheriting from Animal."""
    
    def speak(self) -> str:
        return f"{self.name} meows"
    
    def scratch(self) -> str:
        return f"{self.name} scratches"


def greet_animal(animal: Animal) -> str:
    """Function that takes an animal and makes it speak."""
    return animal.speak()


def create_pet(pet_type: str, name: str) -> Animal:
    """Factory function to create pets."""
    if pet_type == "dog":
        return Dog(name)
    elif pet_type == "cat":
        return Cat(name)
    else:
        return Animal(name)


def process_list(items: list) -> list:
    """Function to process a list of items."""
    result = []
    for item in items:
        result.append(item.upper())
    return result


def higher_order_func(func, value):
    """Higher-order function example."""
    return func(value)


def double(x):
    """Simple function to double a value."""
    return x * 2


def square(x):
    """Simple function to square a value."""
    return x * x


class Calculator:
    """Calculator class with method dispatch."""
    
    def __init__(self):
        self.operations = {
            'add': self._add,
            'sub': self._sub,
            'mul': self._mul,
        }
    
    def _add(self, a, b):
        return a + b
    
    def _sub(self, a, b):
        return a - b
    
    def _mul(self, a, b):
        return a * b
    
    def calculate(self, op: str, a, b):
        """Calculate using method dispatch."""
        if op in self.operations:
            return self.operations[op](a, b)
        return None


def main():
    """Main function demonstrating various call patterns."""
    
    # Simple class instantiation
    dog = Dog("Buddy")
    cat = Cat("Whiskers")
    
    # Method calls on instances
    print(dog.speak())
    print(cat.speak())
    
    # Polymorphic call through base type
    animals = [dog, cat]
    for animal in animals:
        print(greet_animal(animal))
    
    # Factory function
    pet1 = create_pet("dog", "Rex")
    pet2 = create_pet("cat", "Felix")
    print(pet1.speak())
    print(pet2.speak())
    
    # List operations
    words = ["hello", "world"]
    upper_words = process_list(words)
    print(upper_words)
    
    # Higher-order function calls
    result1 = higher_order_func(double, 5)
    result2 = higher_order_func(square, 4)
    print(f"Double: {result1}, Square: {result2}")
    
    # Method dispatch through dictionary
    calc = Calculator()
    print(calc.calculate('add', 10, 5))
    print(calc.calculate('mul', 3, 4))
    
    # Dog-specific method
    print(dog.fetch("ball"))
    
    # Cat-specific method
    print(cat.scratch())
    
    return 0


if __name__ == "__main__":
    main()

