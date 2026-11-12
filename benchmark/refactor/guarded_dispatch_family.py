"""Frozen guarded-dispatch-v1 usefulness family and four fair experiment arms.

Each worker imports one frozen executable tree. Baseline uses the existing
independent AI with its normal policies/cache; no precision feature is disabled.
Every candidate arm includes proposal, complete check, runtime validation and
application costs. Raw results distinguish resource stops and useful answers.
"""
from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[2]
ARMS = ("baseline","correct_candidate","malicious_candidate","valid_coarse_top")

def _precise(value):
    return (value.get('top',True) is False and value.get('bottom',True) is False
            and not any(value.get(key) for key in ("top_sorts","may_unbound","integer_interval","string_patterns")))

def _exact_payload(value):
    return _precise(value) and value.get("constants")==["payload"] and not value.get("objects")

def _empty_default(value):
    return (isinstance(value,dict) and value.get('bottom') is True and value.get('top') is False
            and not any(value.get(key) for key in ('constants','objects','types','top_sorts','may_unbound','integer_interval','string_patterns')))


def family_source(size,repeats):
    lines = []
    for index in range(size):
        returned = "payload" if index % 2 == 0 else "{'value':payload}"
        lines.extend((f"def handler{index}(payload):",f"    return {returned}"))
    lines.extend(("def dispatch(table,key,payload):","    return table[key](payload)"))
    lines.append("table={"+",".join(repr("key"+str(index))+":handler"+str(index) for index in range(size))+"}")
    for invocation in range(repeats):
        for index in range(size):
            lines.append(f"out_{invocation}_{index}=dispatch(table,{repr('key'+str(index))},'payload')")
    return "\n".join(lines)+"\n"


def quality(result,driver,size,repeats):
    module = result.get("values",{}).get("example",{})
    names = {identifier:info.qualname for identifier,info in driver.functions.items()}
    correct,imprecise,missing,mispairs = 0,0,0,0
    fresh = []
    expected = size*repeats
    for invocation in range(repeats):
        for index in range(size):
            name = f"out_{invocation}_{index}"
            value = module.get(name)
            if value is None:
                missing += 1
                continue
            for token in value.get("relations",()):
                handler = names.get(token["handler"],token["handler"])
                if token["key"] != repr("key"+str(index)) or handler != "example.handler"+str(index):
                    mispairs += 1
            if not _precise(value):
                imprecise += 1
                continue
            if index % 2 == 0:
                exact = _exact_payload(value)
            else:
                slots = value.get("slots",())
                exact = not value.get("constants") and len(value.get("objects",())) == 1 and len(slots) == 1
                exact = exact and slots[0].get('havoc',True) is False and _empty_default(slots[0].get('default'))
                exact = exact and not slots[0].get('maybe_missing')
                exact = exact and len(slots[0].get("entries",())) == 1
                exact = exact and slots[0]["entries"][0]["key"] == "value" and _exact_payload(slots[0]["entries"][0]["value"])
                if exact:
                    fresh.append(value["objects"][0])
            if exact:
                correct += 1
            else:
                imprecise += 1
    closed = result.get("solver_status") == "converged" and result.get("coverage_status") == "closed_under_profile"
    return {"expected_queries":expected,"exact_matching_answers":correct,"imprecise_answers":imprecise,
            "missing_answers":missing,"decidable_requested_queries":correct if closed else 0,
            "key_handler_mispairs":mispairs,"fresh_boxes":len(fresh),
            "fresh_identities_distinct":len(fresh) == len(set(fresh)),
            "precision_scope":"Exported finite scalar/slot payloads, empty unknown defaults, no havoc, and distinct returned object IDs. Per-key presence and allocation singleton metadata are unavailable in this export.",
            "all_gold_useful":closed and correct == expected and mispairs == 0 and len(fresh) == len(set(fresh))}


def worker(args):
    # Process limits apply identically to all arms, including candidate work.
    resource.setrlimit(resource.RLIMIT_AS,(args.memory_mib*1024*1024,args.memory_mib*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(int(args.seconds)+8,int(args.seconds)+9))
    # Import/setup time is part of every arm's timer.  Optional verification
    # modules are not imported at all for the ordinary baseline.
    started = time.perf_counter()
    sys.path.insert(0,str(args.executable_root))
    from types import SimpleNamespace
    from pythonstan.analysis.contracts import EntryScenario, SourceModule
    from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
    source = family_source(args.size,args.repeats)
    module = SourceModule("example","example.py",source)
    config = {"engine":"ai_primary","semantic_profile":"python-core-v1",
              "resources":{"max_work":args.work,"max_seconds":args.seconds},
              "llm":{"enabled":args.arm!="baseline","level2":args.arm!="baseline","provider":"offline","max_proposals":2,"checker_budget":10000}}
    provider = None
    try:
        if args.arm != "baseline":
            from pythonstan.analysis.composition.guarded_dispatch import GuardedDispatchProvider
            from pythonstan.llm.providers import OfflineDispatchProposer
            proposer = OfflineDispatchProposer(coarse=args.arm == "valid_coarse_top",
                adversarial="wrong_handler" if args.arm == "malicious_candidate" else None,max_work=args.work)
            provider = GuardedDispatchProvider(SimpleNamespace(modules=(module,)),config,proposer)
            provider.prepare()
        driver = InterproceduralDriver((module,),options=config)
        driver.summary_provider = provider
        remaining=args.seconds-(time.perf_counter()-started)
        if remaining<=0:driver.max_work=0
        else:driver.max_seconds=remaining
        result = driver.run((EntryScenario("example"),)).to_dict()
        record = {"status":"result","source":source,"source_sha256":module.digest,
                  "solver_status":result.get("solver_status"),"coverage_status":result.get("coverage_status"),
                  "actual_phase_trace":result.get("phase_trace",()),"result":result,
                  "quality":quality(result,driver,args.size,args.repeats),
                  "provider":provider.statistics if provider else None,
                  "property_records":[dict(row) for row in provider.proof_records] if provider else [],
                  "optional_verification_imported":any(name.startswith("pythonstan.analysis.verification") for name in sys.modules),
                  "total_work":result.get("stats",{}).get("work"),
                  "wall_seconds":time.perf_counter()-started,
                  "peak_bytes":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                  "input_contract":"finite source module initialization; exact stable builtin table; literal calls cover every key; symbolic checker additionally validates all modeled payloads"}
    except MemoryError:
        record = {"status":"memory_exhausted","wall_seconds":time.perf_counter()-started,
                  "provider":provider.statistics if provider else None,
                  "peak_bytes":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024}
    record.update(arm=args.arm,size=args.size,repeats=args.repeats,
                  budgets={"max_work":args.work,"max_seconds":args.seconds,"memory_mib":args.memory_mib})
    args.output.parent.mkdir(parents=True,exist_ok=True)
    # Mapping proxies in scoped proof records are already scalar/list/tuple
    # JSON wire data; normalize them through the canonical immutable exporter.
    from pythonstan.analysis.contracts import freeze_snapshot,thaw_snapshot
    with gzip.open(args.output,"wt",encoding="utf-8") as file:
        json.dump(thaw_snapshot(freeze_snapshot(record)),file,sort_keys=True)


def freeze_runtime(output):
    destination = output/"executable"
    destination.mkdir(parents=True,exist_ok=True)
    sources=[source for package in ("pythonstan","pythonstan_inspector")
             for source in sorted((ROOT/package).rglob("*"))
             if source.is_file() and "__pycache__" not in source.parts and source.suffix!=".pyc"]
    if (ROOT/'pyproject.toml').is_file():sources.append(ROOT/'pyproject.toml')
    hashes={str(source.relative_to(ROOT)):hashlib.sha256(source.read_bytes()).hexdigest() for source in sources}
    for source in sources:
        relative=source.relative_to(ROOT);target=destination/relative
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    after_sources=[source for package in ("pythonstan","pythonstan_inspector")
             for source in sorted((ROOT/package).rglob("*"))
             if source.is_file() and "__pycache__" not in source.parts and source.suffix!=".pyc"]
    if (ROOT/'pyproject.toml').is_file():after_sources.append(ROOT/'pyproject.toml')
    after={str(source.relative_to(ROOT)):hashlib.sha256(source.read_bytes()).hexdigest() for source in after_sources}
    actual={str(source.relative_to(destination)):hashlib.sha256(source.read_bytes()).hexdigest()
            for source in destination.rglob('*') if source.is_file()}
    if hashes!=after or hashes!=actual:
        (output/'source_capture_error.json').write_text(json.dumps({'before':hashes,'after':after,'actual':actual},indent=2)+'\n')
        raise RuntimeError('Executable source changed during frozen family capture')
    digest = hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
    with tarfile.open(output/"executable_snapshot.tar.gz","w:gz") as archive:
        archive.add(destination,arcname="executable",filter=lambda entry: entry)
    (output/"executable_identity.json").write_text(json.dumps({"source_tree_sha256":digest,"files":hashes},indent=2)+"\n")
    return destination,digest


def summarize(records,expected_scenarios=None):
    scenarios=set(expected_scenarios) if expected_scenarios is not None else {(row['size'],row['repeats']) for row in records}
    expected={(size,repeats,arm) for size,repeats in scenarios for arm in ARMS}
    observed={(row['size'],row['repeats'],row['arm']) for row in records}
    complete=len(records)==len(observed)==len(expected) and observed==expected
    rows = []
    for size,repeats in sorted({(row["size"],row["repeats"]) for row in records}):
        group = {row["arm"]:row for row in records if (row["size"],row["repeats"]) == (size,repeats)}
        baseline,correct = group.get("baseline",{}),group.get("correct_candidate",{})
        same_quality = baseline.get("quality",{}).get("all_gold_useful",False) and correct.get("quality",{}).get("all_gold_useful",False)
        recovery = not baseline.get("quality",{}).get("all_gold_useful",False) and correct.get("quality",{}).get("all_gold_useful",False)
        work_gain = same_quality and correct.get("total_work",float("inf")) < baseline.get("total_work",0)
        wall_gain = same_quality and correct.get("wall_seconds",float("inf")) < baseline.get("wall_seconds",0)
        process_wall_gain=same_quality and correct.get('process_wall_seconds',float('inf'))<baseline.get('process_wall_seconds',0)
        process_cpu_gain=same_quality and correct.get('process_cpu_seconds',float('inf'))<baseline.get('process_cpu_seconds',0)
        rows.append({"size":size,"repeats":repeats,"same_useful_quality":same_quality,
                     "recovered_decidable_queries":recovery,"lower_total_work_at_equal_precision":work_gain,
                     "lower_total_wall_at_equal_precision":wall_gain,
                     "lower_full_process_wall_at_equal_precision":process_wall_gain,
                     "lower_full_process_cpu_at_equal_precision":process_cpu_gain,
                     "baseline_work":baseline.get("total_work"),"correct_total_work":correct.get("total_work"),
                     "baseline_wall_seconds":baseline.get("wall_seconds"),"correct_wall_seconds":correct.get("wall_seconds"),
                     "baseline_process_wall_seconds":baseline.get('process_wall_seconds'),"correct_process_wall_seconds":correct.get('process_wall_seconds'),
                     "baseline_process_cpu_seconds":baseline.get('process_cpu_seconds'),"correct_process_cpu_seconds":correct.get('process_cpu_seconds'),
                     "solver_status_by_arm":{name:row.get("solver_status",row.get("status")) for name,row in group.items()},
                     "benefit_demonstrated":recovery or work_gain or process_wall_gain or process_cpu_gain})
    return {"rows":rows,"expected_runs":len(expected),"actual_runs":len(records),'denominator_complete':complete,
            'missing_runs':[list(row) for row in sorted(expected-observed)],'unexpected_runs':[list(row) for row in sorted(observed-expected)],
            "benefit_demonstrated":complete and any(row["benefit_demonstrated"] for row in rows),
            "limits":["Finite scenario/gamma checks establish these expected answers, not unrestricted Python soundness.",
                      "The source checker validates its explicit symbolic profile; the original solver status is retained.",
                      "All candidate setup, runtime applicability checks and applications are charged; baseline normal capabilities stay enabled."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker",action="store_true")
    parser.add_argument("--arm",choices=ARMS)
    parser.add_argument("--size",type=int,default=2)
    parser.add_argument("--repeats",type=int,default=1)
    parser.add_argument("--sizes",type=int,nargs="+",default=(2,8,24))
    parser.add_argument("--repeat-family",type=int,nargs="+",default=(1,4,16))
    parser.add_argument("--work",type=int,default=200000)
    parser.add_argument("--seconds",type=float,default=5)
    parser.add_argument("--memory-mib",type=int,default=1024)
    parser.add_argument("--executable-root",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args(argv)
    if args.work<1 or args.seconds<=0 or args.memory_mib<1 or min(args.size,args.repeats,*args.sizes,*args.repeat_family)<1:
        parser.error('Experiment work/time/memory and all source sizes/repeats must be positive')
    if args.worker:
        return worker(args)
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('Use a new empty output directory; previous raw family evidence cannot be overwritten')
    args.output.mkdir(parents=True,exist_ok=True)
    runner=args.output/'experiment_runner.py';shutil.copyfile(Path(__file__).resolve(),runner)
    runner_digest=hashlib.sha256(runner.read_bytes()).hexdigest()
    executable,digest = freeze_runtime(args.output)
    protocol={'schema_version':2,'sizes':list(args.sizes),'repeats':list(args.repeat_family),'arms':list(ARMS),
              'source_tree_sha256':digest,'runner_sha256':runner_digest,'python':sys.version,
              'budgets':{'max_work':args.work,'max_seconds':args.seconds,'memory_mib':args.memory_mib},
              'baseline_optional_verification_imports':False,
              'measurement':'One subprocess per arm; complete process wall/CPU includes imports/setup/serialization. Inner engine timer starts before imports. No censored observation is removed.'}
    (args.output/'run_manifest.json').write_text(json.dumps(protocol,indent=2)+'\n')
    records = []
    for size in args.sizes:
        for repeats in args.repeat_family:
            for arm in ARMS:
                path = args.output/f"size{size}_repeat{repeats}_{arm}.json.gz"
                command = [sys.executable,str(runner.resolve()),"--worker","--arm",arm,"--size",str(size),
                    "--repeats",str(repeats),"--work",str(args.work),"--seconds",str(args.seconds),
                    "--memory-mib",str(args.memory_mib),"--executable-root",str(executable),"--output",str(path)]
                started = time.monotonic()
                before_cpu=resource.getrusage(resource.RUSAGE_CHILDREN)
                process_done=None
                try:
                    process = subprocess.run(command,text=True,capture_output=True,timeout=args.seconds+10)
                    process_done=time.monotonic()
                    path.with_suffix('.stdout.txt').write_text(process.stdout)
                    path.with_suffix('.stderr.txt').write_text(process.stderr)
                    if process.returncode == 0 and path.exists():
                        with gzip.open(path,"rt",encoding="utf-8") as file:
                            full = json.load(file)
                        row = {key:value for key,value in full.items() if key not in ("source","result","property_records")}
                        row["solver_status"] = full.get("solver_status",full.get("result",{}).get("solver_status"))
                        row["coverage_status"] = full.get("coverage_status",full.get("result",{}).get("coverage_status"))
                    else:
                        row = {"size":size,"repeats":repeats,"arm":arm,"status":"worker_failed",
                               "exit_code":process.returncode,"stderr":process.stderr}
                except subprocess.TimeoutExpired as error:
                    process_done=time.monotonic()
                    row = {"size":size,"repeats":repeats,"arm":arm,"status":"timeout"}
                    row['partial_stdout']=error.stdout.decode(errors='replace') if isinstance(error.stdout,bytes) else error.stdout
                    row['partial_stderr']=error.stderr.decode(errors='replace') if isinstance(error.stderr,bytes) else error.stderr
                    path.with_suffix('.stdout.txt').write_text(row['partial_stdout'] or '')
                    path.with_suffix('.stderr.txt').write_text(row['partial_stderr'] or '')
                after_cpu=resource.getrusage(resource.RUSAGE_CHILDREN)
                full_cpu=after_cpu.ru_utime+after_cpu.ru_stime-before_cpu.ru_utime-before_cpu.ru_stime
                row.update(raw_file=path.name,source_tree_sha256=digest,runner_sha256=runner_digest,
                           process_wall_seconds=process_done-started,supervisor_wall_seconds=time.monotonic()-started,process_cpu_seconds=full_cpu,
                           process_cpu_scope='Waited child process user+system CPU, including startup/import/setup and finalgzipJSON serialization',command=command)
                records.append(row)
                (args.output/"records.json").write_text(json.dumps(records,indent=2)+"\n")
                print(json.dumps({key:row.get(key) for key in ("size","repeats","arm","status","total_work","wall_seconds")}),flush=True)
    expected_scenarios={(size,repeats) for size in args.sizes for repeats in args.repeat_family}
    summary=summarize(records,expected_scenarios)
    captured=json.loads((args.output/'executable_identity.json').read_text())['files']
    current={str(path.relative_to(executable)):hashlib.sha256(path.read_bytes()).hexdigest()
             for path in executable.rglob('*') if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc'}
    protocol['capture_after_run_unchanged']=captured==current
    protocol['completed_runs']=len(records)
    (args.output/'run_manifest.json').write_text(json.dumps(protocol,indent=2)+'\n')
    summary['same_executable_valid']=captured==current
    summary['benefit_demonstrated']=summary['benefit_demonstrated'] and captured==current
    (args.output/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    print(json.dumps({"runs":len(records),"benefit_demonstrated":summary["benefit_demonstrated"],"denominator_complete":summary['denominator_complete'],"source_tree_sha256":digest}),flush=True)


if __name__ == "__main__":
    main()
