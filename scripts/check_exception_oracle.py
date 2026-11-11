#!/usr/bin/env python3
"""Compare certified summaries with finite executions of generated pure code.

This script accepts only generation parameters, never source text or a source
path. Its fixed grammar has no imports, attributes, heap operations, loops,
recursion, external calls, or user hooks. Only those generated snippets execute.
Tracing is an independent test observer, not part of the certified source model.

Run from any directory; output paths are relative to the repository root:
    .venv/bin/python scripts/check_exception_oracle.py --seed 861231 --count 1000

The corpus and result have no timing-based checks. This finite oracle can expose
implementation errors; it cannot establish the universal soundness theorem.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import platform
import random
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pythonstan.analysis.pointer.ai.certified import (  # noqa: E402
    SCHEMA_VERSION, SEMANTICS_VERSION, check_source, infer_candidate,
)

DEFAULT_SEED = 861231
DEFAULT_COUNT = 1000
GENERATOR_VERSION = "finite-exception-grammar-v1"
MAX_DEPTH = 3
FILENAME = "<generated-finite-exception-oracle>"
OUTPUT_PATH = "docs/certified_analysis/evidence/round2_exception_oracle.json"


def indent(lines):
    return ["    " + line for line in lines]


class Generator:
    def __init__(self, seed):
        self.rng = random.Random(seed)

    def statement(self, depth):
        rng = self.rng
        if depth == 0 or rng.random() < .45:
            return rng.choice([
                ["pass"], ["mark()"], ["fail()"], ["x = None"],
                ["return None"], ["return x"], ["return err"],
                ["raise TypeError()"], ["raise ValueError()"],
                ["raise SystemExit()"],
            ])
        kind = rng.randrange(3)
        if kind == 0:
            return (["if flag is None:"] + indent(self.block(depth - 1)) +
                    ["else:"] + indent(self.block(depth - 1)))
        if kind == 1:
            return (["try:"] + indent(self.block(depth - 1)) +
                    ["finally:"] + indent(self.block(depth - 1)))
        handlers = rng.choice([
            ["except Exception as err:"],
            ["except ValueError as x:", "except TypeError as err:"],
            ["except BaseException:", "except Exception as err:"],
            ["except (ValueError, TypeError) as err:", "except:"],
            ["except ():", "except Exception as err:"],
        ])
        result = ["try:"] + indent(self.block(depth - 1))
        for head in handlers:
            result += [head] + indent(self.block(depth - 1))
        if rng.random() < .6:
            result += ["else:"] + indent(self.block(depth - 1))
        if rng.random() < .6:
            result += ["finally:"] + indent(self.block(depth - 1))
        return result

    def block(self, depth):
        result = []
        for _ in range(self.rng.randint(1, 3)):
            result += self.statement(depth)
        return result

    def source(self):
        body = ["if flag is None:", "    x = None"] + self.block(MAX_DEPTH)
        # Establish both locals in the symbol table even if this suffix is
        # unreachable. They remain genuinely unbound on earlier paths.
        body += ["x = None", "err = None"]
        return ("def mark():\n    pass\ndef fail():\n    raise ValueError()\n"
                "def f(flag):\n" + "\n".join(indent(body)) + "\n")


def check_generated_grammar(tree):
    """Execution admission independent of the certificate validator.

    A future generator edit cannot silently add effectful syntax or arbitrary
    call targets to this oracle. This whitelist is not a general Python sandbox.
    """
    nodes = {
        ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Pass,
        ast.Expr, ast.Assign, ast.Return, ast.Raise, ast.If, ast.Try,
        ast.ExceptHandler, ast.Compare, ast.Is, ast.Call, ast.Name,
        ast.Constant, ast.Tuple, ast.Load, ast.Store,
    }
    names = {"flag", "x", "err", "mark", "fail", "Exception", "BaseException",
             "ValueError", "TypeError", "SystemExit"}
    targets = {"mark", "fail", "ValueError", "TypeError", "SystemExit"}
    for node in ast.walk(tree):
        if type(node) not in nodes:
            raise RuntimeError("generator emitted unapproved syntax")
        if isinstance(node, ast.Name) and node.id not in names:
            raise RuntimeError("generator emitted an unapproved name")
        if isinstance(node, ast.Call):
            if (not isinstance(node.func, ast.Name) or node.func.id not in targets
                    or node.args or node.keywords):
                raise RuntimeError("generator emitted an unapproved call")
        if isinstance(node, ast.FunctionDef):
            if node.name not in {"mark", "fail", "f"} or node.decorator_list:
                raise RuntimeError("generator emitted an unapproved definition")
        if isinstance(node, ast.Constant) and node.value is not None:
            raise RuntimeError("generator emitted an unapproved literal")


def observe(function, flag):
    observed = set()

    def trace(frame, event, arg):
        if frame.f_code.co_filename == FILENAME:
            if event == "call" and frame.f_code.co_name != "f":
                observed.add("call:" + frame.f_code.co_name)
            elif event == "exception":
                observed.add("raise:" + type(arg[1]).__name__)
        return trace

    previous = sys.gettrace()
    exception_kind = None
    try:
        sys.settrace(trace)
        try:
            function(flag)
        except BaseException as exception:
            exception_kind = type(exception).__name__
    finally:
        sys.settrace(previous)
    return observed, exception_kind


def run(seed, count):
    generator = Generator(seed)
    corpus_hash = hashlib.sha256()
    exits = Counter()
    events = set()
    executions = 0
    for case in range(count):
        source = generator.source()
        encoded = source.encode("utf-8")
        corpus_hash.update(len(encoded).to_bytes(8, "big"))
        corpus_hash.update(encoded)
        tree = ast.parse(source)
        check_generated_grammar(tree)
        certificate = infer_candidate(source)
        checked = check_source(source, certificate)
        if not checked.certified:
            raise RuntimeError(f"case {case}: generated source did not certify: {checked.diagnostics}")
        summary = checked.certificate.summary_map()["f"]
        namespace = {}
        exec(compile(tree, FILENAME, "exec"), namespace)
        for flag_name, flag in (("none", None), ("false", False),
                                ("true", True), ("fresh_object", object())):
            observed, exception_kind = observe(namespace["f"], flag)
            covered = (exception_kind in summary.exceptions if exception_kind
                       else summary.normal_exit)
            if not covered or not observed <= summary.events:
                failure = {
                    "case_index": case, "flag": flag_name,
                    "source_sha256": hashlib.sha256(encoded).hexdigest(),
                    "source": source, "exception_exit": exception_kind,
                    "exit_covered": covered,
                    "missing_events": sorted(observed - summary.events),
                }
                raise RuntimeError("oracle mismatch: " + json.dumps(failure, sort_keys=True))
            events.update(observed)
            exits[exception_kind or "normal"] += 1
            executions += 1
    return {
        "status": "passed", "generator_version": GENERATOR_VERSION,
        "seed": seed, "program_count": count, "execution_count": executions,
        "max_generation_depth": MAX_DEPTH,
        "parameter_cases": ["none", "false", "true", "fresh_object"],
        "corpus_sha256": corpus_hash.hexdigest(),
        "schema_version": SCHEMA_VERSION, "semantics_version": SEMANTICS_VERSION,
        "python_version": platform.python_version(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "checker_sha256": hashlib.sha256((REPO_ROOT /
            "pythonstan/analysis/pointer/ai/certified/checker.py").read_bytes()).hexdigest(),
        "observed_events": sorted(events), "observed_exits": dict(sorted(exits.items())),
        "all_observed_prefixes_and_exits_covered": True,
        "scope": "generated finite pure closed snippets; independent concrete trace oracle",
        "limitations": ["finite implementation evidence, not a universal proof",
                        "no heap, dynamic dispatch, recursion, divergence or suspension in this generated corpus"],
        "reproduce": (".venv/bin/python scripts/check_exception_oracle.py "
                      f"--seed {seed} --count {count}"),
    }


def positive_int(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("count must be positive")
    return parsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--count", type=positive_int, default=DEFAULT_COUNT)
    parser.add_argument("--output", default=OUTPUT_PATH,
                        help="JSON output path relative to the repository root")
    args = parser.parse_args()
    relative = Path(args.output)
    if relative.is_absolute() or ".." in relative.parts:
        parser.error("--output must be a relative path within the repository")
    result = run(args.seed, args.count)
    output = REPO_ROOT / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
