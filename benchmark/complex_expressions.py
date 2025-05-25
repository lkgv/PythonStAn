"""
Benchmark program with complex Python expressions to test the
Three-Address IR transformation capabilities.
"""

def complex_assignments():
    # Multiple targets
    a = b = c = 10
    
    # Tuple unpacking
    x, y, z = 1, 2, 3
    
    # List unpacking with star expressions
    first, *middle, last = [1, 2, 3, 4, 5]
    
    # Nested unpacking
    (p, q), (r, s) = (10, 20), (30, 40)
    
    # Attribute assignment
    class Point:
        def __init__(self):
            self.x = 0
            self.y = 0
    
    point = Point()
    point.x = 100
    
    # Subscript assignment
    my_list = [0, 1, 2, 3, 4]
    my_list[2] = 99
    
    # Nested subscript and attribute
    points = [Point(), Point()]
    points[0].x = 200
    
    return a, b, c, x, y, z, first, middle, last, p, q, r, s, point, my_list, points

def complex_expressions():
    # Nested binary operations
    expr1 = 10 + 20 * 30 - 40 / 5
    
    # Nested comparisons
    expr2 = 10 < 20 < 30 != 40
    
    # Boolean operations
    expr3 = (10 > 5 and 20 < 30) or (40 != 50 and 60 >= 70)
    
    # Conditional expression (ternary)
    expr4 = "yes" if 10 > 5 else "no"
    
    # Nested conditional expressions
    expr5 = "big" if 100 > 50 else "medium" if 50 > 10 else "small"
    
    # Attribute and subscript in expressions
    my_dict = {"a": [1, 2, 3], "b": [4, 5, 6]}
    expr6 = my_dict["a"][2] + my_dict["b"][1]
    
    # Call expressions with keyword arguments
    expr7 = complex(real=10, imag=20)
    
    # Function call with star args and keyword args
    def func(a, b, c, d=0):
        return a + b + c + d
    
    args = [1, 2]
    kwargs = {"d": 4}
    expr8 = func(*args, c=3, **kwargs)
    
    # List, set, dict comprehensions
    expr9 = [x**2 for x in range(10) if x % 2 == 0]
    expr10 = {x: x**2 for x in range(5)}
    expr11 = {x for x in range(10) if x % 3 == 0}
    
    # Generator expression
    expr12 = sum(x for x in range(100) if x % 7 == 0)
    
    # F-strings
    name = "Python"
    age = 30
    expr13 = f"{name} is {age} years old"
    
    # Nested f-strings
    expr14 = f"Calculation: {10 * 20 + (30 if age > 20 else 40)}"
    
    return (expr1, expr2, expr3, expr4, expr5, expr6, expr7, expr8, 
            expr9, expr10, expr11, expr12, expr13, expr14)

if __name__ == "__main__":
    complex_assignments()
    complex_expressions() 