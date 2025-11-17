"""Test file with multiple function calls to verify context sensitivity."""

def identity(x):
    """Identity function."""
    return x

def process1(x):
    """Process via identity."""
    result = identity(x)
    return result

def process2(x):
    """Process via identity - different call site."""
    result = identity(x)
    return result

def main():
    """Main function with multiple call sites."""
    obj1 = {"type": "object1"}
    obj2 = {"type": "object2"}
    
    # Call process1 which calls identity
    r1 = process1(obj1)
    
    # Call process2 which calls identity
    r2 = process2(obj2)
    
    # Direct calls to identity
    r3 = identity(obj1)
    r4 = identity(obj2)
    
    return r1, r2, r3, r4

# Trigger execution
if __name__ == "__main__":
    main()

