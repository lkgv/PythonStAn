"""
Benchmark program with various import patterns to test
import resolution and module handling.
"""

# Standard imports
import os
import sys

# Import with alias
import math as m

# From imports
from datetime import datetime, timedelta

# Relative imports would be here in a package

# Conditional imports
if sys.version_info >= (3, 9):
    from functools import cache
else:
    from functools import lru_cache as cache

# Import with try/except
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# Function that uses imports
def use_imports():
    # Using os
    current_dir = os.getcwd()
    
    # Using math (aliased)
    pi_value = m.pi
    sqrt_2 = m.sqrt(2)
    
    # Using datetime
    now = datetime.now()
    tomorrow = now + timedelta(days=1)
    
    # Using conditional import
    @cache
    def fibonacci(n):
        if n <= 1:
            return n
        return fibonacci(n-1) + fibonacci(n-2)
    
    result = fibonacci(10)
    
    # Using optional import
    if HAS_NUMPY:
        array = np.array([1, 2, 3])
        mean = np.mean(array)
    else:
        array = [1, 2, 3]
        mean = sum(array) / len(array)
    
    return {
        'current_dir': current_dir,
        'pi_value': pi_value,
        'sqrt_2': sqrt_2,
        'now': now,
        'tomorrow': tomorrow,
        'fibonacci_10': result,
        'array': array,
        'mean': mean
    }

if __name__ == "__main__":
    result = use_imports()
    for key, value in result.items():
        print(f"{key}: {value}") 