"""
Benchmark program with extensive type annotations to test
how the analyzer handles Python type hints and type inference.
"""
from typing import (
    List, Dict, Tuple, Set, Optional, Union, Any, Callable,
    TypeVar, Generic, Iterable, Iterator, Sequence, Mapping,
    cast, ClassVar, Final, Protocol, Literal, TypedDict,
    NamedTuple, overload, NoReturn, NewType
)
from dataclasses import dataclass
from enum import Enum, auto
import sys

# Type aliases
UserId = NewType('UserId', int)
UserName = NewType('UserName', str)
UserDict = Dict[UserId, UserName]

# Typed dictionary
class MovieInfo(TypedDict):
    title: str
    director: str
    year: int
    rating: Optional[float]

# Named tuple
class Point(NamedTuple):
    x: float
    y: float
    label: Optional[str] = None

# Enum class
class Color(Enum):
    RED = auto()
    GREEN = auto()
    BLUE = auto()

# Data class with type annotations
@dataclass
class Person:
    name: str
    age: int
    email: Optional[str] = None
    active: bool = True
    
    # Class variable with type annotation
    instance_count: ClassVar[int] = 0
    
    # Final variable that cannot be reassigned
    id_prefix: Final[str] = "PERSON"
    
    def __post_init__(self) -> None:
        Person.instance_count += 1

# Generic type variable
T = TypeVar('T')
K = TypeVar('K')
V = TypeVar('V')

# Generic class
class Box(Generic[T]):
    def __init__(self, content: T) -> None:
        self.content: T = content
    
    def get(self) -> T:
        return self.content
    
    def set(self, content: T) -> None:
        self.content = content

# Protocol class for duck typing
class Drawable(Protocol):
    def draw(self) -> None: ...

# Class implementing a protocol
class Circle:
    def __init__(self, radius: float) -> None:
        self.radius: float = radius
    
    def draw(self) -> None:
        print(f"Drawing circle with radius {self.radius}")

# Function with overloads
@overload
def process_input(input_value: int) -> int: ...

@overload
def process_input(input_value: str) -> str: ...

def process_input(input_value: Union[int, str]) -> Union[int, str]:
    if isinstance(input_value, int):
        return input_value * 2
    else:
        return input_value.upper()

# Function with complex type annotations
def complex_func(
    a: List[int],
    b: Dict[str, Any],
    c: Optional[Callable[[int], str]] = None,
    *args: Tuple[int, str],
    **kwargs: float
) -> Tuple[List[int], Dict[str, Any]]:
    result = a.copy()
    for i in range(len(result)):
        if c is not None:
            b[c(i)] = args[i][0] if i < len(args) else 0
    return result, b

# Function with NoReturn
def exit_program(code: int = 0) -> NoReturn:
    print(f"Exiting with code {code}")
    sys.exit(code)

# Generator function with type annotations
def generate_numbers(n: int) -> Iterator[int]:
    i: int = 0
    while i < n:
        yield i
        i += 1

# Function using Literal types
def set_alignment(align: Literal["left", "center", "right"]) -> str:
    return f"Setting alignment to {align}"

# Generic function
def first_element(collection: Sequence[T]) -> Optional[T]:
    if not collection:
        return None
    return collection[0]

# Higher-order function with type hints
def map_values(
    values: Iterable[T],
    mapper: Callable[[T], V]
) -> List[V]:
    return [mapper(value) for value in values]

# Dictionary with typed keys and values
CONFIG: Dict[str, Union[str, int, bool, List[str]]] = {
    "server": "localhost",
    "port": 8080,
    "debug": True,
    "allowed_origins": ["example.com", "localhost"]
}

# Function with type casting
def get_config_value(key: str, default: T) -> T:
    value = CONFIG.get(key, default)
    if isinstance(value, type(default)):
        return cast(T, value)
    return default

def demonstrate_types() -> Dict[str, Any]:
    """Function to demonstrate various type annotations."""
    
    # Basic types
    user_id: UserId = UserId(12345)
    user_name: UserName = UserName("john_doe")
    users: UserDict = {user_id: user_name}
    
    # Typed dictionary
    movie: MovieInfo = {
        "title": "Inception",
        "director": "Christopher Nolan",
        "year": 2010,
        "rating": 8.8
    }
    
    # Named tuple
    point: Point = Point(10.5, 20.3, "Point A")
    
    # Enum
    color: Color = Color.BLUE
    
    # Dataclass
    person: Person = Person("Alice", 30, "alice@example.com")
    
    # Generic class
    int_box: Box[int] = Box(42)
    str_box: Box[str] = Box("Hello")
    
    # Protocol
    drawable: Drawable = Circle(5.0)
    drawable.draw()
    
    # Overloaded function
    int_result: int = process_input(10)  # Returns 20
    str_result: str = process_input("hello")  # Returns "HELLO"
    
    # Complex function
    complex_result: Tuple[List[int], Dict[str, Any]] = complex_func(
        [1, 2, 3],
        {"a": 1},
        lambda x: f"key_{x}",
        (10, "ten"),
        (20, "twenty"),
        extra=1.5
    )
    
    # Generator
    numbers: List[int] = list(generate_numbers(5))
    
    # Literal type
    alignment: str = set_alignment("center")
    
    # Generic function
    first: Optional[int] = first_element([1, 2, 3])
    
    # Higher-order function
    doubled: List[int] = map_values([1, 2, 3], lambda x: x * 2)
    
    # Type casting
    port: int = get_config_value("port", 0)
    
    return {
        "user_id": user_id,
        "user_name": user_name,
        "users": users,
        "movie": movie,
        "point": point,
        "color": color,
        "person": person,
        "int_box_content": int_box.get(),
        "str_box_content": str_box.get(),
        "int_result": int_result,
        "str_result": str_result,
        "complex_result": complex_result,
        "numbers": numbers,
        "alignment": alignment,
        "first": first,
        "doubled": doubled,
        "port": port
    }

if __name__ == "__main__":
    results = demonstrate_types()
    for key, value in results.items():
        print(f"{key}: {value}") 