"""Generate operational public defaults, schema and reference from one catalog."""
from pathlib import Path
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from pythonstan.config import catalog,json_schema
from pythonstan.config.catalog import default_config


def generate(destination=None):
    root=Path(destination) if destination else Path(__file__).resolve().parents[2]/'docs/refactor/config'
    root.mkdir(parents=True,exist_ok=True)
    for name,data in [('defaults.json',default_config()),('schema.json',json_schema()),('catalog.json',catalog())]:
        (root/name).write_text(json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
    rows=['# Public configuration reference','','Generated from the actual operational catalog. Empty engine applicability means compatibility-default only; nondefaults reject. Source/entry/profile and actual execution are retained in every result. AST consumer locations alone do not prove that a control affects execution.','','| Option | Default | Engines | Actual consumer or boundary |','|---|---|---|---|']
    for row in catalog():
        default=json.dumps(row['default'],ensure_ascii=False).replace('|','\\|')
        note=(row['note'] or row['consumer']).replace('|','\\|').replace('\n',' ')
        rows.append(f"| `{row['path']}` | `{default}` | {', '.join(row['engines'])} | {note} |")
    (root/'REFERENCE.md').write_text('\n'.join(rows)+'\n')

if __name__=='__main__':generate(sys.argv[1] if len(sys.argv)>1 else None)
