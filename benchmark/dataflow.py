"""
Benchmark program with various dataflow patterns for testing
reaching definitions, liveness analysis, and other dataflow analyses.
"""

def reaching_definitions_example():
    a = 10          # def1
    b = 20          # def2
    
    if a > b:
        a = 30      # def3
    else:
        a = 40      # def4
        
    print(a)        # uses def3 or def4
    
    c = a + b       # uses def3 or def4, and def2
    
    return c

def liveness_example(n):
    x = 0           # x live here
    y = 10          # x, y live here
    
    for i in range(n):  # x, y, i live here
        x = x + i       # x, y, i live here
        
    # i not live here
    z = x + y       # x, y, z live here
    
    return z        # z live here
    # nothing live here

def use_before_def_example(flag):
    if flag:
        x = 10
    
    # Potential use before define if flag is False
    return x + 5

def dead_code_example():
    a = 10
    b = 20
    
    if a < b:
        c = 30
    else:
        c = 40
        
    # Dead assignment - c is overwritten before use
    c = 50
    
    return c

def complex_assignments():
    # Multiple assignment
    a = b = c = 0
    
    # Tuple unpacking
    x, y = 10, 20
    
    # List comprehension with assignment
    values = [i**2 for i in range(10)]
    
    # Compound assignment
    z = 5
    z += 10
    
    return a, b, c, x, y, z, values

if __name__ == "__main__":
    reaching_definitions_example()
    liveness_example(5)
    try:
        use_before_def_example(False)
    except UnboundLocalError:
        print("Caught use before def error")
    dead_code_example()
    complex_assignments() 