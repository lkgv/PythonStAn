"""
Benchmark program with complex control flow for testing CFG generation
and control flow analysis.
"""

def complex_branching(a, b, c):
    result = 0
    
    # Simple if-else
    if a > 10:
        result = a + 5
    else:
        result = a - 5
    
    # Nested if statements
    if b > 0:
        if c > 0:
            result *= 2
        else:
            result += b
    else:
        result -= 1
    
    # Multiple conditions
    if a > 0 and b > 0 or c > 0:
        result += 10
    
    return result

def loop_examples(n, data):
    # For loop
    sum_val = 0
    for i in range(n):
        sum_val += i
        
        # Early exit
        if sum_val > 100:
            break
    
    # While loop with else
    count = 0
    while count < n:
        if count in data:
            count += 2
        else:
            count += 1
    else:
        sum_val += 50
    
    # Nested loops
    matrix_sum = 0
    for i in range(min(5, n)):
        for j in range(min(5, n)):
            matrix_sum += i * j
    
    return sum_val, matrix_sum

def try_except_example(x):
    try:
        result = 100 / x
        return result
    except ZeroDivisionError:
        return float('inf')
    except (TypeError, ValueError) as e:
        return str(e)
    finally:
        print("Operation attempted")

if __name__ == "__main__":
    complex_branching(15, 7, -3)
    loop_examples(10, {1, 3, 5})
    try_except_example(0) 