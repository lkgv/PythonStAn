"""
Benchmark program with object-oriented programming patterns to test
how the analyzer handles class hierarchies, inheritance, polymorphism,
method resolution, and other OOP features.
"""

# Base class
class Animal:
    """Base animal class with common attributes and methods."""
    
    # Class variable
    species_count = 0
    
    def __init__(self, name, age):
        # Instance variables
        self.name = name
        self.age = age
        Animal.species_count += 1
    
    def make_sound(self):
        """Base method meant to be overridden."""
        return "..."
    
    def describe(self):
        """Method that uses instance variables."""
        return f"{self.name} is {self.age} years old"
    
    @classmethod
    def get_species_count(cls):
        """Class method that accesses class variable."""
        return cls.species_count
    
    @staticmethod
    def is_adult(age):
        """Static method that doesn't use instance or class variables."""
        return age >= 1


# Derived class with method overriding
class Dog(Animal):
    """Dog class that inherits from Animal."""
    
    def __init__(self, name, age, breed):
        # Call parent constructor
        super().__init__(name, age)
        self.breed = breed
    
    def make_sound(self):
        """Override the parent method."""
        return "Woof!"
    
    def describe(self):
        """Override and extend parent method."""
        basic_description = super().describe()
        return f"{basic_description} and is a {self.breed}"


# Another derived class
class Cat(Animal):
    """Cat class that inherits from Animal."""
    
    def __init__(self, name, age, color):
        super().__init__(name, age)
        self.color = color
    
    def make_sound(self):
        return "Meow!"
    
    def describe(self):
        basic_description = super().describe()
        return f"{basic_description} and has {self.color} fur"


# Multiple inheritance
class Pet:
    """Interface-like class for pets."""
    
    def __init__(self, owner):
        self.owner = owner
    
    def get_owner(self):
        return self.owner


class HouseCat(Cat, Pet):
    """Class that uses multiple inheritance."""
    
    def __init__(self, name, age, color, owner):
        Cat.__init__(self, name, age, color)
        Pet.__init__(self, owner)
    
    def describe(self):
        cat_description = Cat.describe(self)
        return f"{cat_description} and belongs to {self.owner}"


# Class with properties
class Temperature:
    """Class that demonstrates properties."""
    
    def __init__(self, celsius=0):
        self._celsius = celsius
    
    @property
    def celsius(self):
        return self._celsius
    
    @celsius.setter
    def celsius(self, value):
        if value < -273.15:
            raise ValueError("Temperature below absolute zero is not possible")
        self._celsius = value
    
    @property
    def fahrenheit(self):
        return (self.celsius * 9/5) + 32
    
    @fahrenheit.setter
    def fahrenheit(self, value):
        self.celsius = (value - 32) * 5/9


# Class with special methods
class Vector:
    """Class that demonstrates special methods."""
    
    def __init__(self, x, y):
        self.x = x
        self.y = y
    
    def __add__(self, other):
        """Vector addition."""
        return Vector(self.x + other.x, self.y + other.y)
    
    def __sub__(self, other):
        """Vector subtraction."""
        return Vector(self.x - other.x, self.y - other.y)
    
    def __mul__(self, scalar):
        """Scalar multiplication."""
        return Vector(self.x * scalar, self.y * scalar)
    
    def __eq__(self, other):
        """Vector equality."""
        return self.x == other.x and self.y == other.y
    
    def __str__(self):
        """String representation."""
        return f"Vector({self.x}, {self.y})"
    
    def __repr__(self):
        """Formal string representation."""
        return f"Vector({self.x}, {self.y})"


def demonstrate_oop():
    """Function to demonstrate OOP features."""
    
    # Create instances
    dog = Dog("Buddy", 3, "Golden Retriever")
    cat = Cat("Whiskers", 2, "gray")
    house_cat = HouseCat("Fluffy", 4, "white", "Alice")
    
    # Polymorphism
    animals = [dog, cat, house_cat]
    for animal in animals:
        print(f"{animal.name} says {animal.make_sound()}")
        print(animal.describe())
    
    # Class methods and variables
    print(f"Total animals created: {Animal.get_species_count()}")
    
    # Static methods
    print(f"Is dog an adult? {Animal.is_adult(dog.age)}")
    
    # Properties
    temp = Temperature(25)
    print(f"{temp.celsius}°C = {temp.fahrenheit}°F")
    temp.fahrenheit = 100
    print(f"{temp.celsius}°C = {temp.fahrenheit}°F")
    
    # Special methods
    v1 = Vector(1, 2)
    v2 = Vector(3, 4)
    v3 = v1 + v2
    print(f"{v1} + {v2} = {v3}")
    
    return animals, temp, v3


if __name__ == "__main__":
    demonstrate_oop() 