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
            if value.get("top") or value.get("top_sorts"):
                imprecise += 1
                continue
            if index % 2 == 0:
                exact = value.get("constants") == ["payload"] and not value.get("objects")
            else:
                slots = value.get("slots",())
                exact = len(value.get("objects",())) == 1 and len(slots) == 1
                exact = exact and len(slots[0].get("entries",())) == 1
                exact = exact and slots[0]["entries"][0]["key"] == "value" and slots[0]["entries"][0]["value"].get("constants") == ["payload"]
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
            "all_gold_useful":closed and correct == expected and mispairs == 0 and len(fresh) == len(set(fresh))}


def worker(args):
    # Process limits apply identically to all arms, including candidate work.
    resource.setrlimit(resource.RLIMIT_AS,(args.memory_mib*1024*1024,args.memory_mib*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(int(args.seconds)+8,int(args.seconds)+9))
    sys.path.insert(0,str(args.executable_root))
    from types import SimpleNamespace
    from pythonstan.analysis.contracts import EntryScenario, SourceModule
    from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
    from pythonstan.analysis.composition.guarded_dispatch import GuardedDispatchProvider
    from pythonstan.llm.providers import OfflineDispatchProposer
    source = family_source(args.size,args.repeats)
    module = SourceModule("example","example.py",source)
    config = {"engine":"ai_primary","semantic_profile":"python-core-v1",
              "resources":{"max_work":args.work,"max_seconds":args.seconds},
              "llm":{"enabled":True,"level2":True,"provider":"offline","max_proposals":2,"checker_budget":10000}}
    started = time.perf_counter()
    provider = None
    try:
        if args.arm != "baseline":
            proposer = OfflineDispatchProposer(coarse=args.arm == "valid_coarse_top",
                adversarial="wrong_handler" if args.arm == "malicious_candidate" else None,max_work=args.work)
            provider = GuardedDispatchProvider(SimpleNamespace(modules=(module,)),config,proposer)
        driver = InterproceduralDriver((module,),options=config)
        driver.summary_provider = provider
        driver.max_seconds = max(0.001,args.seconds-(time.perf_counter()-started))
        result = driver.run((EntryScenario("example"),)).to_dict()
        record = {"status":"result","source":source,"source_sha256":module.digest,
                  "solver_status":result.get("solver_status"),"coverage_status":result.get("coverage_status"),
                  "actual_phase_trace":result.get("phase_trace",()),"result":result,
                  "quality":quality(result,driver,args.size,args.repeats),
                  "provider":provider.statistics if provider else None,
                  "property_records":[dict(row) for row in provider.proof_records] if provider else [],
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
    hashes = {}
    for package in ("pythonstan","pythonstan_inspector"):
        for source in sorted((ROOT/package).rglob("*")):
            if not source.is_file() or "__pycache__" in source.parts or source.suffix == ".pyc":
                continue
            relative = source.relative_to(ROOT)
            target = destination/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
            hashes[str(relative)] = hashlib.sha256(source.read_bytes()).hexdigest()
    digest = hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
    with tarfile.open(output/"executable_snapshot.tar.gz","w:gz") as archive:
        archive.add(destination,arcname="executable",filter=lambda entry: entry)
    (output/"executable_identity.json").write_text(json.dumps({"source_tree_sha256":digest,"files":hashes},indent=2)+"\n")
    return destination,digest


def summarize(records):
    rows = []
    for size,repeats in sorted({(row["size"],row["repeats"]) for row in records}):
        group = {row["arm"]:row for row in records if (row["size"],row["repeats"]) == (size,repeats)}
        baseline,correct = group.get("baseline",{}),group.get("correct_candidate",{})
        same_quality = baseline.get("quality",{}).get("all_gold_useful",False) and correct.get("quality",{}).get("all_gold_useful",False)
        recovery = not baseline.get("quality",{}).get("all_gold_useful",False) and correct.get("quality",{}).get("all_gold_useful",False)
        work_gain = same_quality and correct.get("total_work",float("inf")) < baseline.get("total_work",0)
        wall_gain = same_quality and correct.get("wall_seconds",float("inf")) < baseline.get("wall_seconds",0)
        rows.append({"size":size,"repeats":repeats,"same_useful_quality":same_quality,
                     "recovered_decidable_queries":recovery,"lower_total_work_at_equal_precision":work_gain,
                     "lower_total_wall_at_equal_precision":wall_gain,
                     "baseline_work":baseline.get("total_work"),"correct_total_work":correct.get("total_work"),
                     "baseline_wall_seconds":baseline.get("wall_seconds"),"correct_wall_seconds":correct.get("wall_seconds"),
                     "solver_status_by_arm":{name:row.get("solver_status",row.get("status")) for name,row in group.items()},
                     "benefit_demonstrated":recovery or work_gain or wall_gain})
    return {"rows":rows,"benefit_demonstrated":any(row["benefit_demonstrated"] for row in rows),
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
    if args.worker:
        return worker(args)
    args.output.mkdir(parents=True,exist_ok=True)
    executable,digest = freeze_runtime(args.output)
    records = []
    for size in args.sizes:
        for repeats in args.repeat_family:
            for arm in ARMS:
                path = args.output/f"size{size}_repeat{repeats}_{arm}.json.gz"
                command = [sys.executable,str(Path(__file__).resolve()),"--worker","--arm",arm,"--size",str(size),
                    "--repeats",str(repeats),"--work",str(args.work),"--seconds",str(args.seconds),
                    "--memory-mib",str(args.memory_mib),"--executable-root",str(executable),"--output",str(path)]
                started = time.monotonic()
                try:
                    process = subprocess.run(command,text=True,capture_output=True,timeout=args.seconds+10)
                    if process.returncode == 0 and path.exists():
                        with gzip.open(path,"rt",encoding="utf-8") as file:
                            full = json.load(file)
                        row = {key:value for key,value in full.items() if key not in ("source","result","property_records")}
                        row["solver_status"] = full.get("solver_status",full.get("result",{}).get("solver_status"))
                        row["coverage_status"] = full.get("coverage_status",full.get("result",{}).get("coverage_status"))
                    else:
                        row = {"size":size,"repeats":repeats,"arm":arm,"status":"worker_failed",
                               "exit_code":process.returncode,"stderr":process.stderr}
                except subprocess.TimeoutExpired:
                    row = {"size":size,"repeats":repeats,"arm":arm,"status":"timeout"}
                row.update(raw_file=path.name,source_tree_sha256=digest,process_wall_seconds=time.monotonic()-started,command=command)
                records.append(row)
                (args.output/"records.json").write_text(json.dumps(records,indent=2)+"\n")
                print(json.dumps({key:row.get(key) for key in ("size","repeats","arm","status","total_work","wall_seconds")}),flush=True)
    (args.output/"summary.json").write_text(json.dumps(summarize(records),indent=2)+"\n")
    print(json.dumps({"runs":len(records),"benefit_demonstrated":summarize(records)["benefit_demonstrated"],"source_tree_sha256":digest}),flush=True)


if __name__ == "__main__":
    main()
