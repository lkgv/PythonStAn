import json

with open('benchmark/reports/test_fix/flask_analysis_report_20251026_005116.json') as f:
    d = json.load(f)

cg = d['call_graph_metrics']
fm = d['function_metrics']

print(f'Call edges: {cg["total_edges"]}')
print(f'Functions in call graph: {cg["total_functions"]}')
print(f'Unique functions in metrics: {len(fm)}')

funcs_with_edges = {f: m for f, m in fm.items() if m['out_degree'] > 0 or m['in_degree'] > 0}
print(f'Functions with call edges: {len(funcs_with_edges)}')

print(f'\nTop 10 callers by out-degree:')
for f in sorted(funcs_with_edges.keys(), key=lambda x: funcs_with_edges[x]['out_degree'], reverse=True)[:10]:
    m = funcs_with_edges[f]
    print(f'  {f}: out={m["out_degree"]}, in={m["in_degree"]}')



