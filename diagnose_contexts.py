from pythonstan.analysis.pointer.kcfa2.context_selector import *
from pythonstan.analysis.pointer.kcfa2.config import KCFAConfig
from pythonstan.analysis.pointer.kcfa2.context import CallSite

print("=== Testing Context Creation ===\n")

policies = ['0-cfa', '1-cfa', '2-cfa', '1-obj', '1-type', '1-rcv']
for policy_str in policies:
    config = KCFAConfig(context_policy=policy_str)
    print(f"Config: {config}")
    
    policy = parse_policy(policy_str)
    selector = ContextSelector(policy)
    
    ctx = selector.empty_context()
    print(f"  Empty context type: {type(ctx).__name__}")
    print(f"  Empty context repr: {ctx}")
    print(f"  Empty context str: {str(ctx)}")
    
    # Try appending
    cs = CallSite("test.py:10:5:call", "foo")
    new_ctx = selector.select_call_context(ctx, cs, "foo")
    print(f"  After call context type: {type(new_ctx).__name__}")
    print(f"  After call context: {new_ctx}")
    print(f"  Contexts are same: {ctx == new_ctx}")
    print()

print("\n=== Hash Uniqueness Test ===\n")
cs1 = CallSite("test.py:10:5:call", "foo")
cs2 = CallSite("test.py:20:8:call", "bar")

from pythonstan.analysis.pointer.kcfa2.context import CallStringContext
ctx1 = CallStringContext((cs1,), 2)
ctx2 = CallStringContext((cs2,), 2)
ctx3 = CallStringContext((cs1, cs2), 2)

print(f"ctx1: {ctx1}, hash: {hash(ctx1)}")
print(f"ctx2: {ctx2}, hash: {hash(ctx2)}")
print(f"ctx3: {ctx3}, hash: {hash(ctx3)}")
print(f"ctx1 == ctx2: {ctx1 == ctx2}")
print(f"ctx1 == ctx3: {ctx1 == ctx3}")

