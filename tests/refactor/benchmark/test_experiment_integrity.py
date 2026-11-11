"""Negative controls for evidence identity, denominator and source-pin integrity."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest


REPO = Path(__file__).resolve().parents[3]


def load_module(name):
    path = REPO / "benchmark/refactor" / (name + ".py")
    spec = importlib.util.spec_from_file_location("refactor_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_run(root):
    identity = {"git_commit": "frozen", "code_sha256": "bytes"}
    manifest = {"stage": "official", "executable": identity, "selected_cases": ["one"],
                "selected_modes": ["A", "B"], "input_identities": {"one": {"digest": "source"}}}
    (root / "run_manifest.json").write_text(json.dumps(manifest))
    for mode, status in (("A", "valid_result"), ("B", "timeout")):
        folder = root / "one" / mode
        folder.mkdir(parents=True)
        row = {"case_id": "one", "mode": mode, "process_status": status,
               "capture_status": "complete", "executable": identity,
               "executable_before": identity, "executable_after": identity,
               "matched_version_valid": True, "input_digest": "source",
               "observed_input_digest": "source", "input_identity_matches": True,
               "result": {"stats": {}}}
        (folder / "record.json").write_text(json.dumps(row))


def test_all_failures_retained_and_missing_work_not_zero(tmp_path):
    fixture_run(tmp_path)
    summary = load_module("aggregate").aggregate(tmp_path)
    assert summary["denominator_complete"]
    assert summary["modes"]["B"]["process_status"] == {"timeout": 1}
    assert all(row["native_work"] is None for row in summary["records"])
    assert len(summary["records"]) == 2


def test_aggregator_rechecks_identities_instead_of_trusting_marker(tmp_path):
    fixture_run(tmp_path)
    path = tmp_path / "one/A/record.json"
    row = json.loads(path.read_text())
    row["executable_after"] = {"git_commit": "other", "code_sha256": "changed"}
    row["observed_input_digest"] = "other_source"
    # A stale or buggy supervisor's Boolean cannot certify these identities.
    assert row["matched_version_valid"] is True
    path.write_text(json.dumps(row))
    summary = load_module("aggregate").aggregate(tmp_path)
    assert not summary["same_version_valid"]
    assert not summary["same_input_valid"]


def test_duplicate_rows_do_not_inflate_the_completed_denominator(tmp_path):
    fixture_run(tmp_path)
    extra = tmp_path / "duplicate/A"
    extra.mkdir(parents=True)
    (extra / "record.json").write_text((tmp_path / "one/A/record.json").read_text())
    summary = load_module("aggregate").aggregate(tmp_path)
    assert summary["record_count"] == 3 and summary["unique_rows"] == 2
    assert not summary["denominator_complete"]


def test_changed_cache_cannot_keep_the_original_archive_identity(tmp_path):
    runner = load_module("runner")
    archives = tmp_path / "archives"
    archives.mkdir()
    archive = archives / "sample.tar.gz"
    source = b"value = 'after'\n"
    with tarfile.open(archive, "w:gz") as packed:
        info = tarfile.TarInfo("sample-1/pkg/__init__.py")
        info.size = len(source)
        packed.addfile(info, io.BytesIO(source))
    case = {"id": "sample", "kind": "corpus", "category": "test", "archive_filename": archive.name,
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "project_root": "sample/sample-1", "runtime_source_roots": ["pkg"],
            "import_roots": ["."], "exclusions": [], "expected_inventory_count": 1,
            "reference_fact": None}
    cache = tmp_path / "cache"
    runner.prepare_case(case, cache, archives)
    changed = cache / "sample/sample-1/pkg/__init__.py"
    changed.write_text("value = 'changed-cache'\n")
    with pytest.raises(ValueError, match="differs from pinned archive"):
        runner.prepare_case(case, cache, archives)
    assert changed.read_text() == "value = 'changed-cache'\n", "Do not overwrite a changed cached source to fake identity"


def test_distribution_sibling_exclusions_keep_all_runtime_package_files(tmp_path):
    from pythonstan import Project
    from pythonstan.config import resolve_config
    from pythonstan.inputs import snapshot_target

    runner = load_module("runner")
    package_files = {"pkg/__init__.py", "pkg/tasks.py", "pkg/tests/test_bundled.py", "pkg/build/model.py"}
    for name in package_files | {"tasks.py", "tests/test_distribution.py"}:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("value = 'after'\n")
    target = Project(tmp_path, source_roots=(str(tmp_path),),
                     exclusions=runner.root_exclusion_patterns(["tasks.py", "tests"]))
    shared = json.loads((REPO / "benchmark/refactor/manifest.json").read_text())["shared_algorithm_configuration"]
    config = resolve_config(target, shared)
    snapshot = snapshot_target(target, config)
    actual = {str(Path(item["filename"]).relative_to(tmp_path)) for item in snapshot.inventory}
    assert actual == package_files


def test_no_snapshot_means_unknown_coverage_counters_not_an_empty_project(tmp_path):
    fixture_run(tmp_path)
    path=tmp_path/'one/B/record.json'
    row=json.loads(path.read_text());row.pop('result');row['expected_inventory_count']=909
    path.write_text(json.dumps(row))
    summary=load_module('aggregate').aggregate(tmp_path)
    failed=next(row for row in summary['records'] if row['mode']=='B')
    assert failed['expected_root_scenarios']==909
    assert all(failed[name] is None for name in ('actual_inventory','declared_entry_scenarios',
        'discovered_units','analyzed_units','initialized_roots','published_module_exits',
        'call_count','obligation_count','total_charged_work'))
    # An observed empty inventory is different and remains the measured zero.
    row['result']={'inventory':[],'entry_contract':{'scenarios':[]},'calls':[],
                   'obligations':[],'module_exits':{},'stats':{'units':20,'analyzed_units':3,'initialized_roots':2,
                   'native_prepass_work':17,'native_work':8,'ai_intrinsic_bridge_work':9}}
    path.write_text(json.dumps(row))
    observed=next(row for row in load_module('aggregate').aggregate(tmp_path)['records'] if row['mode']=='B')
    assert observed['actual_inventory']==observed['declared_entry_scenarios']==observed['published_module_exits']==0
    assert (observed['discovered_units'],observed['analyzed_units'],observed['initialized_roots'])==(20,3,2)
    assert observed['native_prepass_work']==17 and observed['legacy_native_work']==8 and observed['intrinsic_bridge_work']==9


def test_aggregation_does_not_retain_all_large_raw_snapshots(tmp_path,monkeypatch):
    import weakref
    module=load_module('aggregate');identity={'git_commit':'frozen','code_sha256':'bytes'}
    names=[f'case{index}' for index in range(24)]
    manifest={'stage':'official','executable':identity,'selected_cases':names,'selected_modes':['D'],
              'input_identities':{name:{'digest':name} for name in names}}
    (tmp_path/'run_manifest.json').write_text(json.dumps(manifest))
    for name in names:
        directory=tmp_path/name/'D';directory.mkdir(parents=True)
        row={'case_id':name,'mode':'D','process_status':'timeout','executable':identity,
             'executable_before':identity,'executable_after':identity,'matched_version_valid':True,
             'input_digest':name,'result':{'values':{'payload':'x'*4096}}}
        (directory/'record.json').write_text(json.dumps(row))
    class Receipt(dict):pass
    live=peak=0;original=module.json.loads
    def release():
        nonlocal live
        live-=1
    def observed_loads(text,*args,**kwargs):
        nonlocal live,peak
        value=original(text,*args,**kwargs)
        if isinstance(value,dict) and 'case_id' in value:
            value=Receipt(value);live+=1;peak=max(peak,live);weakref.finalize(value,release)
        return value
    monkeypatch.setattr(module.json,'loads',observed_loads)
    result=module.aggregate(tmp_path)
    assert result['denominator_complete'] and result['record_count']==24
    assert peak<=2,'The aggregator must stream raw snapshots rather than retaining every complete result'


def test_partial_cost_receipt_and_missing_requested_answer_remain_unavailable(tmp_path):
    fixture_run(tmp_path);path=tmp_path/'one/B/record.json';row=json.loads(path.read_text())
    row['cost']={'worker_inclusive_wall_seconds':1.25,'cpu_user_seconds_self':0.1}
    row['reference_fact']={'kind':'pure_module_literal','expected':'after'}
    path.write_text(json.dumps(row))
    observed=next(row for row in load_module('aggregate').aggregate(tmp_path)['records'] if row['mode']=='B')
    assert observed['wall_seconds_inclusive']==1.25
    assert observed['cpu_seconds_before_final_json_export'] is None
    assert observed['quality']['query_status']=='no_data'


@pytest.mark.parametrize('workers',[0,3])
def test_invalid_worker_count_rejects_before_input_preparation(tmp_path,monkeypatch,workers):
    module=load_module('runner')
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps({'resources':{'parallel_workers':2},'cases':[]}))
    def forbidden(*args,**kwargs):raise AssertionError('Invalid concurrency reached source preparation')
    monkeypatch.setattr(module,'prepare_case',forbidden)
    with pytest.raises(SystemExit):module.main(['--manifest',str(manifest),'--output',str(tmp_path/'new'),'--workers',str(workers)])
