"""Bounded independent W0 witnesses and deliberately strict product acceptance.

The concrete worker executes only the frozen handcrafted fixtures. The product
worker never executes target source. A green concrete run freezes the oracle;
it does not turn an analyzer miss into a passing test or prove soundness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
import sys
import time
from pathlib import Path

type Json = None | bool | int | float | str | list[Json] | dict[str, Json]

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = Path(__file__).with_name("semantic_manifest.json")
FROZEN_MANIFEST_SHA256 = "16771ac12bb1267a032fb004c98b47680ee938fcf95a5f639eac5549f159e66b"

CONCRETE_WORKER = r"""
import json, sys
from types import FrameType
request=json.load(sys.stdin)
namespace=dict(request.get("inputs", {}))
calls=[]
def profile(frame: FrameType, event: str, arg: object) -> None:
    if event=="call" and frame.f_code.co_name in request.get("handlers", []):
        assert frame.f_back is not None
        calls.append([frame.f_code.co_name,frame.f_back.f_lineno])
def canonical(value: object) -> object:
    if isinstance(value,tuple):return {"tuple":[canonical(x) for x in value]}
    if isinstance(value,list):return [canonical(x) for x in value]
    if isinstance(value,dict):return {str(k):canonical(v) for k,v in value.items()}
    return value
sys.setprofile(profile)
try:
    exec(compile(request["source"],"<frozen-independent-witness>","exec"),namespace)
finally:
    sys.setprofile(None)
print(json.dumps({"result":canonical(namespace.get("result")),"calls":calls}))
"""

ANALYZER_WORKER = r"""
import json, sys
from pythonstan import Source,ValueQuery,PropertyQuery,analyze
request=json.load(sys.stdin)
config={"engine":request.get("engine","ai_primary"),
    "resources":request["resources"]}
if request.get("abstract"):config["abstract"]=request["abstract"]
if request.get("inputs"):config["inputs"]={"bindings":request["inputs"]}
if request.get("llm"):
    config["llm"]={"enabled":True,"level2":True,"provider":"offline"}
query=ValueQuery("subject","result")
queries=[query,"calls","returns","outcomes","effects","stats","obligations"]
for symbol in request.get("symbols",[]):
    queries.append(ValueQuery("subject",symbol,"function_exit"))
for symbol in request.get("module_symbols",[]):
    queries.append(ValueQuery("subject",symbol))
if request.get("llm"):queries.append(PropertyQuery("guarded_dispatch_return_relation"))
result=analyze(Source(request["source"],module="subject"),config=config,queries=queries)
snapshot=result.raw_snapshot()
print(json.dumps({"answer":result.query(query).to_dict(),
    "solver_status":result.solver_status,"coverage_status":result.coverage_status,
    "calls":snapshot.get("calls"),"returns":result.query("returns").to_dict(),
    "outcomes":result.query("outcomes").to_dict(),
    "effects":result.query("effects").to_dict(),"stats":snapshot.get("stats"),
    "module_exits":snapshot.get("module_exits"),
    "obligations":snapshot.get("obligations"),
    "function_exit":{symbol:result.query(ValueQuery("subject",symbol,"function_exit")).to_dict()
        for symbol in request.get("symbols",[])},
    "module_values":{symbol:result.query(ValueQuery("subject",symbol)).to_dict()
        for symbol in request.get("module_symbols",[])}}))
"""

L1_WORKER = r'''
import copy,json,sys
from pythonstan import Source,ValueQuery,analyze
from pythonstan.config import resolve_config
from pythonstan.inputs import snapshot_target
from pythonstan.llm.strategy import RefinementController
request=json.load(sys.stdin)
source="""def identity(payload):
    return payload
def box(payload):
    return {'value': payload}
def dispatch(key, payload):
    table = {'read': identity, 'write': box}
    return table[key](payload)
result = dispatch(key, 'after')
"""
target=Source(source,module="subject");query=ValueQuery("subject","result")
options={"engine":"ai_primary","abstract":{"enable_value_partition":request["direction"]=="degrade"},
    "inputs":{"bindings":{"subject.key":{"type":"str","top":True}}},
    "resources":request["resources"],"llm":{"enabled":True,"level1":True,"provider":"offline"}}
cfg=resolve_config(target,options).to_dict();snapshot=snapshot_target(target,cfg)
baseline_cfg=copy.deepcopy(options);baseline_cfg["llm"]["enabled"]=False
baseline=analyze(target,config=baseline_cfg,queries=[query]).raw_snapshot()
class Proposal:
    def propose(self, *args: object) -> list[dict[str, object]]:
        return [{"query":{"kind":"value","module":"subject","symbol":"result","point":"module_exit"},
            "source_digest":snapshot.source_digest,"region":{"module":"*","qualname":"*","kind":"block"},
            "strategy":"key_partitions","parameters":{"enable_value_partition":request["direction"]!="degrade"},"resources":{}}]
selected,feedback=RefinementController(Proposal()).run(target=target,source_snapshot=snapshot,
    config=cfg,queries=[query],output=None,baseline=baseline)
print(json.dumps({"before":baseline["values"]["subject"]["result"],
    "after":selected["values"]["subject"]["result"],"feedback":feedback,
    "baseline_work":baseline["stats"]["work"],"selected_work":selected["stats"]["work"]}))
'''


def json_value(value: object) -> Json:
    """Validate the sole dynamic JSON boundary before any internal consumer."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {str(key): json_value(item) for key, item in value.items()}
    raise ValueError("Invalid or nonfinite JSON value")


def object_value(value: Json) -> dict[str, Json]:
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value


def array_value(value: Json) -> list[Json]:
    if not isinstance(value, list):
        raise ValueError("Expected JSON array")
    return value


def string_value(value: Json) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected JSON string")
    return value


def integer_value(value: Json) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("Expected JSON integer")
    return value


def decode(text: str) -> Json:
    raw: object = json.loads(text)
    return json_value(raw)


def load_manifest() -> dict[str, Json]:
    if hashlib.sha256(MANIFEST.read_bytes()).hexdigest() != FROZEN_MANIFEST_SHA256:
        raise ValueError("Frozen semantic denominator or input contract changed")
    return object_value(decode(MANIFEST.read_text(encoding="utf-8")))


def run_worker(code: str, request: dict[str, Json], timeout: int = 15) -> dict[str, Json]:
    started = time.perf_counter()
    try:
        process = subprocess.run(
            [sys.executable, "-c", code],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            cwd=ROOT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"process_status": "timeout", "elapsed_seconds": time.perf_counter() - started}
    if process.returncode:
        return {
            "process_status": "internal_error",
            "exit_code": process.returncode,
            "stderr": process.stderr[-4000:],
            "elapsed_seconds": time.perf_counter() - started,
        }
    result = object_value(decode(process.stdout))
    result["process_status"] = "completed"
    result["elapsed_seconds"] = time.perf_counter() - started
    return result


def exact_scalar(payload: dict[str, Json], expected: list[Json]) -> bool:
    """Reject all forms of Top and compare scalars with Python sort tags."""
    if payload.get("top") or payload.get("top_sorts") or payload.get("bottom"):
        return False
    patterns = payload.get("string_patterns")
    if isinstance(patterns, dict) and any(patterns.values()):
        return False
    if all(isinstance(value, list) for value in expected):
        return len(expected) == 1 and payload.get("items") == expected[0]
    if payload.get("objects"):
        return False
    tagged = array_value(payload.get("tagged_constants", []))
    actual = {
        (string_value(object_value(item).get("type")), json.dumps(object_value(item).get("value")))
        for item in tagged
    }
    wanted = {
        ("none" if value is None else type(value).__name__, json.dumps(value)) for value in expected
    }
    return actual == wanted


def normal_module_exit(row: dict[str, Json]) -> bool:
    modules = object_value(row.get("module_exits", {}))
    exits = array_value(modules.get("subject", []))
    return bool(exits) and all(object_value(value).get("kind") == "normal" for value in exits)


def finite_inclusion(payload: dict[str, Json], expected: list[Json]) -> bool:
    """Use an explicitly weaker precision obligation for the context-zero probe."""
    if payload.get("top") or payload.get("top_sorts") or payload.get("bottom"):
        return False
    constants = array_value(payload.get("tagged_constants", []))
    interval = payload.get("integer_interval")
    for value in expected:
        if any(
            object_value(item).get("value") == value
            and object_value(item).get("type") == type(value).__name__
            for item in constants
        ):
            continue
        if isinstance(value, int) and not isinstance(value, bool) and isinstance(interval, dict):
            lower, upper = interval.get("lower"), interval.get("upper")
            if isinstance(lower, int) and isinstance(upper, int) and lower <= value <= upper:
                continue
        return False
    return True


def open_boundary_is_propagated(row: dict[str, Json], expected: list[Json]) -> bool:
    answer = object_value(row.get("answer", {}))
    payload = object_value(answer.get("value") or {})
    outcomes = object_value(object_value(row.get("outcomes", {})).get("value", {}))
    kinds = {object_value(item).get("kind") for item in array_value(outcomes.get("subject", []))}
    effects = object_value(object_value(row.get("effects", {})).get("value", {}))
    obligations = array_value(row.get("obligations", []))
    value_covered = answer.get("status") == "unavailable" or (
        answer.get("status") == "answered"
        and (payload.get("top") is True or exact_scalar(payload, expected))
    )
    return bool(
        row.get("process_status") == "completed"
        and row.get("coverage_status") == "open"
        and value_covered
        and {"normal", "raise"} <= kinds
        and obligations
        and all(object_value(item).get("status") == "open" for item in obligations)
        and effects.get("havoc")
        and effects.get("exceptions")
        and effects.get("calls")
    )


def check_concrete(case: dict[str, Json]) -> dict[str, Json]:
    if platform.python_implementation() != "CPython" or sys.version_info[:2] != (3, 12):
        raise RuntimeError("The frozen concrete order oracle requires CPython 3.12")
    source = string_value(case["source"])
    expected = array_value(case["expected"])
    inputs = array_value(case["oracle_inputs"])
    rows: list[Json] = []
    for environment in inputs:
        rows.append(run_worker(CONCRETE_WORKER, {"source": source, "inputs": environment}))
    passed = len(rows) == len(expected) and all(
        object_value(row).get("process_status") == "completed"
        and json.dumps(object_value(row).get("result"), sort_keys=True)
        == json.dumps(value, sort_keys=True)
        for row, value in zip(rows, expected, strict=True)
    )
    return {"id": case["id"], "finding": case["finding"], "passed": passed, "rows": rows}


def check_analyzer(case: dict[str, Json], resources: dict[str, Json]) -> dict[str, Json]:
    request = dict(case)
    request["resources"] = {
        key: value for key, value in resources.items() if key != "external_seconds"
    }
    row = run_worker(ANALYZER_WORKER, request, integer_value(resources["external_seconds"]))
    answer = object_value(row.get("answer", {}))
    payload = object_value(answer.get("value") or {})
    mandatory = case["classification"] == "mandatory"
    passed = (
        row.get("process_status") == "completed"
        and row.get("solver_status") == "converged"
        and row.get("coverage_status") == "closed_under_profile"
        and answer.get("status") == "answered"
        and normal_module_exit(row)
        and (
            exact_scalar(payload, array_value(case["expected"]))
            if case.get("exact")
            else finite_inclusion(payload, array_value(case["expected"]))
        )
    )
    if not mandatory:
        passed = open_boundary_is_propagated(row, array_value(case["expected"]))
    return {"id": case["id"], "finding": case["finding"], "passed": passed, "result": row}


def handler_pairs(calls: Json, names: list[Json]) -> set[tuple[str, int]]:
    pairs: set[tuple[str, int]] = set()
    for item in array_value(calls or []):
        call = object_value(item)
        callee = string_value(call["callee"]).rsplit(".", 1)[-1]
        if callee in names:
            line = int(string_value(call["site"]).rsplit(":", 3)[1])
            pairs.add((callee, line))
    return pairs


def exact_handler_relations(payload: dict[str, Json]) -> bool:
    relations = array_value(payload.get("relations", []))
    pairs = {
        (object_value(item).get("key"), object_value(item).get("handler")) for item in relations
    }
    return (
        len(relations) == 2
        and pairs == {("'read'", "subject.identity"), ("'write'", "subject.box")}
        and all(
            object_value(item).get("key_type") == "str"
            and object_value(item).get("partitioned") is True
            for item in relations
        )
    )


def boxed_payload_is_useful(payload: dict[str, Json], expected: str) -> bool:
    if payload.get("top") or payload.get("top_sorts") or payload.get("bottom"):
        return False
    objects = array_value(payload.get("objects", []))
    slots = array_value(payload.get("slots", []))
    return bool(
        len(objects) == 1
        and slots
        and all(
            object_value(slot).get("object") in objects
            and not object_value(slot).get("havoc")
            and any(
                object_value(field).get("key") == "value"
                and exact_scalar(object_value(object_value(field).get("value") or {}), [expected])
                for field in array_value(object_value(slot).get("entries", []))
            )
            for slot in slots
        )
    )


def query_payload(answers: dict[str, Json], symbol: str) -> dict[str, Json]:
    answer = object_value(answers.get(symbol, {}))
    return object_value(answer.get("value") or {}) if answer.get("status") == "answered" else {}


def function_scopes_are_useful(row: dict[str, Json], symbols: list[Json]) -> bool:
    answers = object_value(row.get("function_exit", {}))
    if set(answers) != {string_value(symbol) for symbol in symbols}:
        return False
    identity = query_payload(answers, "identity")
    box = query_payload(answers, "box")
    dispatch = query_payload(answers, "dispatch")
    if not exact_scalar(identity, ["original"]) or not boxed_payload_is_useful(box, "retained"):
        return False
    scalar_dispatch = dict(dispatch)
    scalar_dispatch["objects"] = []
    if not exact_scalar(scalar_dispatch, ["original"]) or not boxed_payload_is_useful(
        dispatch, "retained"
    ):
        return False
    if dispatch.get("objects") != box.get("objects"):
        return False
    for answer in answers.values():
        payload = object_value(object_value(answer).get("value") or {})
        if (
            payload.get("normal_exit") is not True
            or payload.get("exceptions")
            or payload.get("suspends")
            or payload.get("top")
            or payload.get("top_sorts")
        ):
            return False
    scopes = {"subject." + string_value(symbol) for symbol in symbols}
    returns = object_value(object_value(row.get("returns", {})).get("value", {}))
    outcomes = object_value(object_value(row.get("outcomes", {})).get("value", {}))
    if set(returns) != scopes or set(outcomes) != scopes | {"subject"}:
        return False
    for scope, value in returns.items():
        bulk = object_value(value)
        target = query_payload(answers, scope.removeprefix("subject."))
        if (
            bulk.get("top")
            or bulk.get("top_sorts")
            or bulk.get("normal_exit") is not True
            or bulk.get("tagged_constants") != target.get("tagged_constants")
            or bulk.get("objects") != target.get("objects")
        ):
            return False
    for scope, rows in outcomes.items():
        expected_kind = "normal" if scope == "subject" else "return"
        completions = array_value(rows)
        if not completions or any(
            object_value(item).get("kind") != expected_kind for item in completions
        ):
            return False
        if scope != "subject":
            values = [object_value(object_value(item).get("value") or {}) for item in completions]
            if any(
                value.get("top") or value.get("top_sorts") or value.get("bottom")
                for value in values
            ):
                return False
            target = query_payload(answers, scope.removeprefix("subject."))
            actual_objects = {
                string_value(obj)
                for value in values
                for obj in array_value(value.get("objects", []))
            }
            if actual_objects != {
                string_value(obj) for obj in array_value(target.get("objects", []))
            }:
                return False
            if scope in {"subject.identity", "subject.dispatch"} and not any(
                exact_scalar(value, ["original"]) for value in values
            ):
                return False
    return True


def check_llm(case: dict[str, Json], resources: dict[str, Json]) -> dict[str, Json]:
    request = dict(case)
    request["resources"] = {
        key: value for key, value in resources.items() if key != "external_seconds"
    }
    if case["kind"] == "l1_selection":
        row = run_worker(L1_WORKER, request, integer_value(resources["external_seconds"]))
        after = object_value(row.get("after", {}))
        before = object_value(row.get("before", {}))
        feedback = object_value(row.get("feedback", {}))
        decisions = [
            object_value(item).get("status") for item in array_value(feedback.get("proposals", []))
        ]
        # Equality preserves the useful baseline against the dominated proposal;
        # the improvement control also needs an actual finite two-pair relation.
        scalar_after = dict(after)
        scalar_after["objects"] = []
        passed = (
            row.get("process_status") == "completed"
            and exact_handler_relations(after)
            and exact_scalar(scalar_after, ["after"])
            and boxed_payload_is_useful(after, "after")
        )
        if case["direction"] == "degrade":
            passed = passed and after == before and bool(decisions) and "accepted" not in decisions
        else:
            passed = (
                passed
                and decisions == ["accepted"]
                and len(array_value(before.get("relations", []))) == 4
            )
        return {"id": case["id"], "finding": case["finding"], "passed": passed, "result": row}
    request["llm"] = True
    request["module_symbols"] = ["plain", "boxed"]
    row = run_worker(ANALYZER_WORKER, request, integer_value(resources["external_seconds"]))
    baseline_request = dict(request)
    baseline_request["llm"] = False
    baseline = run_worker(
        ANALYZER_WORKER, baseline_request, integer_value(resources["external_seconds"])
    )
    passed = (
        row.get("process_status") == "completed"
        and row.get("solver_status") == "converged"
        and row.get("coverage_status") == "closed_under_profile"
    )
    if case["kind"] == "l2_calls":
        names = array_value(case["handler_names"])
        concrete = run_worker(CONCRETE_WORKER, {"source": case["source"], "handlers": names})
        pairs = handler_pairs(row.get("calls"), names)
        expected = {
            (string_value(name), integer_value(line))
            for name in names
            for line in array_value(case["expected_handler_lines"])
            if isinstance(line, int)
        }
        observed = {
            (string_value(array_value(pair)[0]), integer_value(array_value(pair)[1]))
            for pair in array_value(concrete.get("calls", []))
        }
        passed = passed and pairs == expected == observed
        module_values = object_value(row.get("module_values", {}))
        passed = passed and exact_scalar(query_payload(module_values, "plain"), ["original"])
        passed = passed and boxed_payload_is_useful(
            query_payload(module_values, "boxed"), "retained"
        )
        row["concrete"] = concrete
    else:
        passed = passed and function_scopes_are_useful(row, array_value(case["symbols"]))
        effects = object_value(object_value(row.get("effects", {})).get("value", {}))
        reads = {
            tuple(string_value(part) for part in array_value(read))
            for read in array_value(effects.get("reads", []))
        }
        for item in array_value(case.get("expected_effect_reads", [])):
            owner, name, suffix = (string_value(part) for part in array_value(item))
            passed = passed and any(
                read[0] == owner and read[1] == name and read[2].endswith(suffix) for read in reads
            )
    return {
        "id": case["id"],
        "finding": case["finding"],
        "passed": passed,
        "result": row,
        "baseline": baseline,
    }


def run(check: str) -> dict[str, Json]:
    manifest = load_manifest()
    rows: list[Json] = []
    for item in array_value(manifest["cases"]):
        case = object_value(item)
        result = (
            check_concrete(case)
            if check == "concrete"
            else check_analyzer(case, object_value(manifest["resources"]))
        )
        rows.append(result)
        print(case["id"], "PASS" if result["passed"] else "FAIL", flush=True)
    if check == "analyzer":
        for item in array_value(manifest["llm_cases"]):
            result = check_llm(object_value(item), object_value(manifest["resources"]))
            rows.append(result)
            print(result["id"], "PASS" if result["passed"] else "FAIL", flush=True)
    return {
        "schema_version": 1,
        "check": check,
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "source_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        "passed": all(object_value(row)["passed"] for row in rows),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", choices=("concrete", "analyzer"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.check)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
