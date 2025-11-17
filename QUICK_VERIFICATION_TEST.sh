#!/bin/bash
# Quick test to verify context sensitivity works

echo "🧪 Quick Context Sensitivity Verification Test"
echo "=============================================="
echo ""

python3 << 'PYTHON'
from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd()))

from benchmark.analyze_real_world import RealWorldAnalyzer
from pythonstan.analysis.pointer.kcfa2.config import KCFAConfig

flask_path = Path('benchmark/projects/flask/src/flask')
app_py = flask_path / 'app.py'

print("Testing app.py with different policies...")
print("")

results = {}
for policy in ['0-cfa', '1-cfa', '2-cfa']:
    config = KCFAConfig(context_policy=policy, verbose=False)
    analyzer = RealWorldAnalyzer(flask_path, 'flask', config)
    result, analysis = analyzer.analyze_module(app_py, debug=False)
    
    if analysis:
        results[policy] = {
            'contexts': len(analysis._contexts),
            'env': len(analysis._env),
            'calls': analysis._statistics['calls_processed']
        }

print("| Policy | Contexts | Env | Calls |")
print("|--------|----------|-----|-------|")
for policy, data in results.items():
    print(f"| {policy:6s} | {data['contexts']:8d} | {data['env']:3d} | {data['calls']:5d} |")

print("")
if results['0-cfa']['contexts'] < results['1-cfa']['contexts']:
    print("✅ SUCCESS: Context counts differ!")
    print("✅ Context sensitivity is working correctly!")
else:
    print("❌ FAIL: Context counts are identical")
    print("❌ Bug still present")

PYTHON
