import json

with open('benchmark/reports/with_builtins/flask_analysis_report_20251026_011235.json') as f:
    d = json.load(f)

fm = d['function_metrics']
funcs_no_calls = {f: m for f, m in fm.items() if m['out_degree'] == 0 and m['in_degree'] == 0}

print(f'Functions with NO calls: {len(funcs_no_calls)}')
print(f'\nSample functions with no calls:')
for i, f in enumerate(list(funcs_no_calls.keys())[:30], 1):
    print(f'  {i:2d}. {f}')

# Check which functions DO have calls
funcs_with_calls = {f: m for f, m in fm.items() if m['out_degree'] > 0 or m['in_degree'] > 0}
print(f'\n\nFunctions WITH calls: {len(funcs_with_calls)}')
print(f'\nAll functions with calls:')
for i, (f, m) in enumerate(sorted(funcs_with_calls.items(), key=lambda x: -(x[1]['out_degree'] + x[1]['in_degree'])), 1):
    print(f'  {i:2d}. {f}: out={m["out_degree"]}, in={m["in_degree"]}')



