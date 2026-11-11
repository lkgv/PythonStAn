#!/usr/bin/env python3
"""Analyze a finite source-defined generator protocol without executing it.

JSON arrays in arguments/send values denote immutable tuples. Input arguments
are already-bound parameter environments. --domains supplies explicit finite
alternatives; --operations is a JSON list of next/send/throw/close operations.
Outputs are diagnostic reports, not serializable resumable state handles.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pythonstan.analysis.pointer.ai.generator_protocol import (
    InvalidGeneratorInput, ProtocolBudget, ProtocolException,
    compile_generator_protocol,
)


def value_from_json(value):
    if type(value) is list:
        return tuple(value_from_json(item) for item in value)
    if type(value) in (type(None), bool, int, str):
        return value
    raise InvalidGeneratorInput("JSON protocol values must be null/bool/int/string/array")


def analyze(args):
    config = json.loads(args.budget) if args.budget else {}
    if type(config) is not dict:
        raise InvalidGeneratorInput("budget must be a JSON object")
    budget = ProtocolBudget(**config)
    # Bound file reads before parsing, including oversized inputs.
    with args.source.open("rb") as stream:
        raw = stream.read(budget.max_source_bytes + 1)
    if len(raw) > budget.max_source_bytes:
        raise InvalidGeneratorInput("source exceeds byte budget")
    model = compile_generator_protocol(raw.decode("utf-8"), args.function, budget=budget)
    bindings = json.loads(args.domains or args.arguments or "{}")
    if type(bindings) is not dict:
        raise InvalidGeneratorInput("argument bindings must be a JSON object")
    if args.domains is not None:
        if any(type(values) is not list or not values for values in bindings.values()):
            raise InvalidGeneratorInput("domains require nonempty arrays of alternatives")
        states = model.initial_alternatives({key: tuple(value_from_json(value) for value in values)
                                             for key, values in bindings.items()})
    else:
        states = (model.initial({key: value_from_json(value) for key, value in bindings.items()}),)
    operations = json.loads(args.operations)
    if type(operations) is not list or not operations:
        raise InvalidGeneratorInput("operations must be a nonempty JSON array")
    for operation in operations:
        if type(operation) is not dict or set(operation) - {"op", "value", "exception", "args"}:
            raise InvalidGeneratorInput("invalid operation object")
        if operation.get("op") not in {"next", "send", "throw", "close"}:
            raise InvalidGeneratorInput("invalid operation name")
        allowed = {"op", "value"} if operation["op"] == "send" else (
            {"op", "exception", "args"} if operation["op"] == "throw" else {"op"})
        if set(operation) - allowed:
            raise InvalidGeneratorInput("operation contains irrelevant fields")
    traces = []
    complete = True
    for initial in states:
        state, steps = initial, []
        for operation in operations:
            kwargs = {}
            if operation["op"] == "send":
                kwargs["value"] = value_from_json(operation.get("value"))
            elif operation["op"] == "throw":
                arguments = value_from_json(operation.get("args", []))
                kwargs["exception"] = ProtocolException(operation.get("exception"), arguments)
            step = model.resume(state, operation["op"], **kwargs)
            steps.append(step.to_dict())
            if step.status != "complete":
                complete = False
                break
            state = step.outcomes[0].continuation
        traces.append({"initial_state_id": initial.state_id, "steps": steps})
    return {
        "status": "complete" if complete else "unknown",
        "source_sha256": model.source_sha256,
        "general_python_soundness_proved": False,
        "assumptions": ["Python 3.12 generator protocol", "stable source and builtin exception bindings",
                        "only declared finite immutable inputs", "no shared external heap or reentrant observers",
                        "no external active exception inherited at generator resume"],
        "trace_count": len(traces), "traces": traces,
    }, 0 if complete else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--function", required=True)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--arguments", help="JSON already-bound parameter object")
    inputs.add_argument("--domains", help="JSON parameter-to-finite-alternatives object")
    parser.add_argument("--operations", required=True, help="JSON operation array")
    parser.add_argument("--budget", help="JSON ProtocolBudget fields")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        payload, code = analyze(args)
    except (ValueError, TypeError, OSError, UnicodeError) as exc:
        payload, code = {"status": "input_error", "diagnostics": [str(exc)],
                         "general_python_soundness_proved": False}, 2
    output = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
