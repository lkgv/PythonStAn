"""Append factual ownership to frozen migration targets, without importing code."""
from pathlib import Path
import ast
import hashlib
import json
import subprocess

ROOT=Path(__file__).resolve().parents[2]


def module_path(module,root=ROOT):
    file=root.joinpath(*module.split('.')).with_suffix('.py')
    if file.exists():return file
    init=root.joinpath(*module.split('.'))/'__init__.py'
    return init if init.exists() else None


def module_name(file,root=ROOT):
    name='.'.join(file.relative_to(root).with_suffix('').parts)
    return name.removesuffix('.__init__')


def alias_target(file):
    """Recognize one actual whole-module facade; ordinary imports are not moves."""
    source=file.read_text();tree=ast.parse(source)
    imports={}
    for node in tree.body:
        if isinstance(node,ast.Import):
            for name in node.names:imports[name.asname or name.name]=name.name
        elif isinstance(node,ast.ImportFrom) and node.level==0:
            for name in node.names:imports[name.asname or name.name]=(node.module+'.'+name.name if node.module else name.name)
        elif isinstance(node,ast.Assign):
            value=node.value
            if isinstance(value,ast.Call) and value.args and isinstance(value.args[0],ast.Constant) and isinstance(value.args[0].value,str):
                fn=value.func.id if isinstance(value.func,ast.Name) else value.func.attr if isinstance(value.func,ast.Attribute) else ''
                if fn in {'import_module','__import__'}:
                    for target in node.targets:
                        if isinstance(target,ast.Name):imports[target.id]=value.args[0].value
    for node in ast.walk(tree):
        if not isinstance(node,ast.Assign):continue
        if not any(isinstance(target,ast.Subscript) and isinstance(target.value,ast.Attribute)
                   and target.value.attr=='modules' for target in node.targets):continue
        if isinstance(node.value,ast.Name):return imports.get(node.value.id)
    return None


def historical_archives(root=ROOT):
    found={}
    for manifest in sorted((root/'legacy/prototypes').glob('*/archive_manifest.json')):
        for row in json.loads(manifest.read_text())['files']:
            file=root/row['archived_path'];digest=hashlib.sha256(file.read_bytes()).hexdigest()
            if digest!=row['sha256']:raise ValueError('Historical archive digest mismatch: '+str(file))
            if row['original_path'] in found:raise ValueError('Ambiguous historical archive')
            found[row['original_path']]={**row,'manifest':str(manifest.relative_to(root))}
    return found


def record_for_row(row,root=ROOT,archives=None):
    archives=historical_archives(root) if archives is None else archives
    old=root/row['old_path'];target=module_path(row['new_module'],root)
    alias=alias_target(old) if old.exists() else None
    # The frozen destination is an intention, not proof that a still-present
    # original module delegates to it. Only a concrete module alias changes
    # the owner here; package aggregators and symbol reexports retain their
    # original module identity.
    actual_path=module_path(alias,root) if alias else old if old.exists() else target
    actual=module_name(actual_path,root) if actual_path is not None else None
    if actual is None and row['old_path'] in archives:
        return {'status':'historical_noninstalled_archive','current_module':None,'current_path':None,
                'supported_import':False,'historical_archive':archives[row['old_path']],
                'note':'Byte preservation is not a maintained implementation or semantic repair.'}
    if actual is None:
        return {'status':'removed_without_active_destination','current_module':None,'current_path':None,
                'supported_import':False,'note':'Missing path cannot be classified as a thin delegate.'}
    status=('same_object_module_alias' if alias else 'active_at_target' if target is not None and actual_path==target
            else 'retained_at_original_location')
    return {'status':status,'current_module':actual,'current_path':str(actual_path.relative_to(root)),
            'source_sha256':hashlib.sha256(actual_path.read_bytes()).hexdigest(),
            'old_path_present':old.exists(),'supported_import':old.exists(),
            'alias_target':alias,'intended_destination':row['new_module']}


def update():
    path=ROOT/'docs/refactor/module_map.json';data=json.loads(path.read_text())
    archives=historical_archives()
    for row in data['modules']:row['implementation']=record_for_row(row,archives=archives)
    catalog=[]
    for package in ('pythonstan','pythonstan_inspector'):
        for file in sorted((ROOT/package).rglob('*.py')):
            catalog.append({'module':module_name(file),'path':str(file.relative_to(ROOT)),
                            'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
    data['current_catalog']=catalog
    data['implementation_snapshot']={'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'tree_sha256':hashlib.sha256(json.dumps(catalog,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'notes':['Original150 target rows/symbol mappings remain frozen. Actual ownership is factual and separately recorded.',
                 'A file destination or alias does not establish semantic acceptance.',
                 'Historical prototypes are not part of installed package or selected engine.']}
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'old_rows':len(data['modules']),'current_modules':len(catalog)}))

if __name__=='__main__':update()
