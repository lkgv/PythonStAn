# Context Policy Comparison - Quick Start Guide

**Status:** Ready for experimentation  
**Date:** October 18, 2025

---

## TL;DR - Run Experiments Now

```bash
cd /mnt/data_fast/code/PythonStAn

# Quick test (3 modules, 3 policies)
python benchmark/compare_context_policies.py flask --policies 0-cfa,1-cfa,2-cfa --max-modules 3

# Core comparison (9 policies, all Flask modules)
python benchmark/compare_context_policies.py flask --policies core

# Full comparison (16 policies, Flask + Werkzeug)
python benchmark/compare_context_policies.py both --policies all
```

---

## What Was Implemented

### Context Sensitivity Policies (16 total)

| Category | Policies | Description |
|----------|----------|-------------|
| **Context-insensitive** | `0-cfa` | No context distinction |
| **Call-string** | `1-cfa`, `2-cfa`, `3-cfa` | Last k call sites |
| **Object** | `1-obj`, `2-obj`, `3-obj` | Allocation site chain |
| **Type** | `1-type`, `2-type`, `3-type` | Type chain |
| **Receiver** | `1-rcv`, `2-rcv`, `3-rcv` | Receiver allocation sites |
| **Hybrid** | `1c1o`, `2c1o`, `1c2o` | Call + Object |

### Policy Sets

- **Core:** 9 policies (recommended for initial experiments)
  - `0-cfa, 1-cfa, 2-cfa, 3-cfa, 1-obj, 2-obj, 1-type, 2-type, 1-rcv`

- **Extended:** 16 policies (comprehensive comparison)
  - Core + `3-obj, 3-type, 2-rcv, 3-rcv, 1c1o, 2c1o, 1c2o`

---

## Running Comparisons

### Command Syntax

```bash
python benchmark/compare_context_policies.py <project> --policies <policy_set> [options]
```

**Projects:** `flask`, `werkzeug`, `both`  
**Policy Sets:** `core`, `all`, or comma-separated list

### Examples

#### 1. Quick Test (Recommended First Step)

Test with just a few modules to verify everything works:

```bash
python benchmark/compare_context_policies.py flask \
    --policies 0-cfa,1-cfa,2-cfa \
    --max-modules 3
```

**Expected time:** ~30 seconds  
**Output:** Report in `benchmark/reports/context_comparison/`

#### 2. Core Comparison on Flask

Compare 9 policies on full Flask codebase:

```bash
python benchmark/compare_context_policies.py flask --policies core
```

**Expected time:** ~10-15 minutes  
**Modules:** ~30 Python files

#### 3. Full Comparison on Both Projects

Compare all 16 policies on Flask and Werkzeug:

```bash
python benchmark/compare_context_policies.py both --policies all
```

**Expected time:** ~1-2 hours  
**Modules:** ~80 Python files total

#### 4. Custom Policy Set

Compare specific policies of interest:

```bash
python benchmark/compare_context_policies.py werkzeug \
    --policies 0-cfa,2-cfa,1-obj,1-type,1-rcv
```

#### 5. With Timeout

Set per-policy timeout (useful for expensive policies):

```bash
python benchmark/compare_context_policies.py flask \
    --policies all \
    --timeout 600
```

---

## Understanding the Output

### Report Files

Each run generates two files in `benchmark/reports/context_comparison/`:

1. **Markdown Report** (`flask_policy_comparison_YYYYMMDD_HHMMSS.md`)
   - Human-readable comparison tables
   - Performance rankings
   - Precision rankings
   - Trade-off analysis
   - Recommendations

2. **JSON Data** (`flask_policy_comparison_YYYYMMDD_HHMMSS.json`)
   - Raw metrics for further analysis
   - Can import into Python/R for visualization

### Sample Report Structure

```markdown
# Context Policy Comparison - FLASK

## Performance Comparison
| Policy | Duration (s) | Throughput (LOC/s) | Contexts |
|--------|--------------|-------------------|----------|
| 0-cfa  | 0.85         | 15234             | 1        |
| 1-cfa  | 1.12         | 11571             | 847      |
| 2-cfa  | 1.34         | 9672              | 2341     |

## Precision Comparison
| Policy | Singleton % | Avg Size | Max Size |
|--------|-------------|----------|----------|
| 2-cfa  | 87.3%       | 1.21     | 8        |
| 1-obj  | 89.1%       | 1.15     | 6        |
| 0-cfa  | 72.4%       | 1.89     | 15       |

## Recommendations
- **Best Precision:** 1-obj (89.1% singleton)
- **Fastest:** 0-cfa (0.85s)
- **Best Balance:** 1-cfa (combined score: 2.5)
```

### Key Metrics Explained

**Performance:**
- **Duration:** Wall-clock time in seconds
- **Throughput:** Lines of code analyzed per second
- **Contexts:** Number of distinct contexts created

**Precision:**
- **Singleton %:** Percentage of points-to sets with exactly 1 element (higher = more precise)
- **Avg Size:** Average number of objects in points-to sets (lower = more precise)
- **Max Size:** Largest points-to set (lower = more precise)

---

## Interpreting Results

### What to Look For

1. **Precision Leaders**
   - Which policies have highest singleton ratio?
   - Does object sensitivity beat call-string for Python?

2. **Performance Leaders**
   - Which policies are fastest?
   - Is context-insensitive (0-cfa) much faster?

3. **Trade-off Sweet Spot**
   - Which policy has best combined score?
   - Is 2-cfa still optimal, or should we change default?

4. **Context Explosion**
   - Do any policies create excessive contexts (>10,000)?
   - Does 3-cfa explode compared to 2-cfa?

5. **Python-Specific Insights**
   - Does receiver sensitivity (1-rcv) work well for methods?
   - Is type sensitivity effective for duck typing?

### Expected Hypotheses

**H1: Object Sensitivity Wins for Precision**
- Expect `1-obj` or `2-obj` to have highest singleton ratio
- Python is OO-heavy, so allocation sites should distinguish well

**H2: 1-CFA is Sweet Spot**
- Expect `1-cfa` to have best precision/performance balance
- `2-cfa` may be overkill with diminishing returns

**H3: Receiver Sensitivity is Efficient**
- Expect `1-rcv` to be faster than `1-obj` but similarly precise
- Only tracks method calls, not all allocations

**H4: Hybrid is Overkill**
- Expect `1c1o` and `2c1o` to be slow without major precision gains
- Context explosion from combining dimensions

---

## Troubleshooting

### Common Issues

**1. Import Error: Module not found**

```bash
# Make sure you're in the project root
cd /mnt/data_fast/code/PythonStAn

# Verify Python path
python -c "import pythonstan; print(pythonstan.__file__)"
```

**2. Project Path Not Found**

```
Error: Flask/Werkzeug path not found
```

**Solution:** Check that projects are downloaded:
```bash
ls benchmark/projects/flask/src/flask
ls benchmark/projects/werkzeug/src/werkzeug
```

**3. Analysis Timeout**

```
⏱ TIMEOUT after 300s
```

**Solution:** Increase timeout or reduce modules:
```bash
# Increase timeout
python benchmark/compare_context_policies.py flask --policies core --timeout 600

# Or reduce modules
python benchmark/compare_context_policies.py flask --policies core --max-modules 10
```

**4. Policy Fails**

```
✗ FAILED: ValueError: Unknown policy
```

**Solution:** Check policy name is valid. Available:
```
0-cfa, 1-cfa, 2-cfa, 3-cfa,
1-obj, 2-obj, 3-obj,
1-type, 2-type, 3-type,
1-rcv, 2-rcv, 3-rcv,
1c1o, 2c1o, 1c2o
```

---

## Next Steps After Running Experiments

### 1. Analyze Results

```bash
# View latest report
ls -lt benchmark/reports/context_comparison/*.md | head -1 | xargs cat

# Or open in editor
code benchmark/reports/context_comparison/flask_policy_comparison_*.md
```

### 2. Extract Insights

Look for:
- Which policy has best singleton ratio?
- Which is fastest?
- Which has best combined score?
- Are there surprising results?

### 3. Visualize (Optional)

Use the JSON data to create plots:

```python
import json
import matplotlib.pyplot as plt

with open('flask_policy_comparison_*.json') as f:
    data = json.load(f)

# Plot precision vs performance
policies = [r['policy'] for r in data]
precision = [r['singleton_ratio'] for r in data]
duration = [r['duration'] for r in data]

plt.scatter(duration, precision)
for i, p in enumerate(policies):
    plt.annotate(p, (duration[i], precision[i]))

plt.xlabel('Duration (s)')
plt.ylabel('Singleton Ratio (%)')
plt.title('Context Policy Trade-offs')
plt.savefig('policy_comparison.png')
```

### 4. Update Default Policy

If experiments show a different policy is better:

```python
# In config.py, change default
class KCFAConfig:
    def __init__(
        self,
        context_policy: Optional[str] = None,  # Was "2-cfa"
        ...
    ):
        if context_policy is None:
            context_policy = "1-obj"  # NEW DEFAULT
```

---

## Advanced Usage

### Programmatic Use

```python
from pathlib import Path
from benchmark.compare_context_policies import PolicyComparator, CORE_POLICIES

# Setup
project_path = Path("benchmark/projects/flask/src/flask")
comparator = PolicyComparator(project_path, "flask")
comparator.find_python_modules(max_modules=5)

# Run comparison
results = comparator.compare_policies(CORE_POLICIES[:3])

# Analyze
for r in results:
    print(f"{r.policy}: {r.duration:.2f}s, {r.singleton_ratio:.1f}% singleton")

# Generate report
comparator.generate_comparison_report(results, Path("my_report.md"))
```

### Running Single Policy

```python
from pythonstan.analysis.pointer.kcfa2 import KCFAConfig, KCFA2PointerAnalysis

# Configure
config = KCFAConfig(
    context_policy="1-obj",  # Try object sensitivity
    verbose=True
)

# Analyze
analysis = KCFA2PointerAnalysis(config)
# ... load IR and run
```

---

## Testing

### Verify Implementation

```bash
# Run unit tests
pytest tests/pointer/test_context_policies.py -v

# Should see:
# 25 passed in 0.11s
```

### Quick Smoke Test

```bash
# Test on small example (should complete in <1 minute)
python benchmark/compare_context_policies.py flask \
    --policies 0-cfa,2-cfa \
    --max-modules 1
```

---

## Tips for Efficient Experimentation

### 1. Start Small

Always test with `--max-modules 3` first to verify:
- Policies work correctly
- No crashes or errors
- Output format is as expected

### 2. Use Core Set First

Run core policies (9) before extended (16):
```bash
python benchmark/compare_context_policies.py flask --policies core
```

### 3. Parallel Runs

Run Flask and Werkzeug in parallel (different terminals):

```bash
# Terminal 1
python benchmark/compare_context_policies.py flask --policies core

# Terminal 2
python benchmark/compare_context_policies.py werkzeug --policies core
```

### 4. Iterative Refinement

1. Run core policies
2. Identify interesting policies
3. Run extended set with just those policies
4. Deep dive on winners

### 5. Automate Batch Runs

```bash
#!/bin/bash
# run_all_experiments.sh

policies=("core" "all")
projects=("flask" "werkzeug")

for proj in "${projects[@]}"; do
    for pol in "${policies[@]}"; do
        echo "Running $pol on $proj..."
        python benchmark/compare_context_policies.py $proj --policies $pol
    done
done
```

---

## Expected Timeline

| Task | Time | What It Does |
|------|------|--------------|
| **Quick test** | 30s | Verify setup (3 modules, 3 policies) |
| **Core on Flask** | 10-15 min | Full Flask with 9 policies |
| **Core on Werkzeug** | 15-20 min | Full Werkzeug with 9 policies |
| **All on Flask** | 20-30 min | Flask with 16 policies |
| **All on Werkzeug** | 30-45 min | Werkzeug with 16 policies |
| **Full comparison** | 1-2 hours | Both projects, all policies |

**Total for complete study:** ~2-3 hours

---

## Deliverables

After running experiments, you'll have:

1. ✅ **Markdown Reports** for each run
2. ✅ **JSON Data** for further analysis
3. ✅ **Comparative Insights** on which policies work best for Python
4. ✅ **Publication-quality Data** for research paper
5. ✅ **Recommendations** for optimal default policy

---

## Questions This Answers

### RQ1: Is 2-CFA the optimal default?
**How to check:** Compare `1-cfa`, `2-cfa`, `3-cfa` in report

### RQ2: Does object sensitivity help Python?
**How to check:** Compare `2-cfa` vs `1-obj`, `2-obj` precision

### RQ3: Is receiver sensitivity a good alternative?
**How to check:** Compare `1-obj` vs `1-rcv` speed and precision

### RQ4: Are hybrid policies worth it?
**How to check:** Compare `1c1o` vs `1-cfa` and `1-obj`

### RQ5: What's the precision/performance sweet spot?
**How to check:** Look at "Best Balance" in recommendations

---

## Support

**Issues?** Check:
1. Unit tests pass: `pytest tests/pointer/test_context_policies.py`
2. Import works: `python -c "from pythonstan.analysis.pointer.kcfa2.context_selector import *"`
3. Projects exist: `ls benchmark/projects/`

**Need help?** See:
- `CONTEXT_POLICY_DESIGN.md` - Full design documentation
- `tests/pointer/test_context_policies.py` - Example usage
- `benchmark/compare_context_policies.py` - Implementation

---

## Ready to Start?

```bash
# Run this now!
cd /mnt/data_fast/code/PythonStAn
python benchmark/compare_context_policies.py flask --policies core
```

Then check the report in `benchmark/reports/context_comparison/` 🚀

---

**Last Updated:** October 18, 2025  
**Status:** ✅ Ready for experimentation

