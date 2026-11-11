"""Finite-input relational contracts over the source-derived closed heap model.

Each row starts from a fresh source-defined heap, then invokes one named entry
with literal arguments. All declared combinations must finish normally in the
underlying model. This is a conditional finite-entry contract, never a contract
for arbitrary heaps, later calls, omitted inputs, or KCFA pruning. Expected
observations do not choose entries, inputs, transitions, or covered rows.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
import hashlib
import itertools
import json
import keyword
import math
from pathlib import Path
from typing import Any

from . import closed_heap_effects as heap_model

SEMANTICS_VERSION = "finite-entry-relations-v1"


@dataclass(frozen=True)
class FiniteEntrySpec:
    entry_function: str
    positional_domains: tuple[tuple[Any, ...], ...] = ()
    keyword_domains: tuple[tuple[str, tuple[Any, ...]], ...] = ()
    observed_globals: tuple[str, ...] = ()


@dataclass(frozen=True)
class FamilyBudget:
    max_variants: int = 256
    max_source_bytes: int = 1_000_000
    max_literal_nodes: int = 4096
    max_literal_depth: int = 32
    max_integer_bits: int = 4096
    max_string_chars: int = 100_000
    max_candidate_bytes: int = 1_000_000
    per_variant: heap_model.Budget = field(default_factory=heap_model.Budget)

    def __post_init__(self):
        for name in ("max_variants", "max_source_bytes", "max_literal_nodes",
                     "max_literal_depth", "max_integer_bits", "max_string_chars",
                     "max_candidate_bytes"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(name + " must be a positive integer")
        if type(self.per_variant) is not heap_model.Budget:
            raise ValueError("per_variant must be a closed-model Budget")


class _BudgetExceeded(Exception):
    def __init__(self, message, declared_variants=None):
        super().__init__(message)
        self.declared_variants = declared_variants


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _identifier(value):
    return type(value) is str and value.isidentifier() and not keyword.iskeyword(value)


def _literal(value, budget, remaining, depth=0):
    """Canonical typed literal encoding; never invokes arbitrary host hooks."""
    remaining[0] -= 1
    if remaining[0] < 0 or depth > budget.max_literal_depth:
        raise _BudgetExceeded("literal structure budget exhausted")
    kind = type(value)
    if value is None:
        return ["none"]
    if kind is bool:
        return ["bool", value]
    if kind is int:
        if value.bit_length() > budget.max_integer_bits:
            raise _BudgetExceeded("integer literal budget exhausted")
        return ["int", str(value)]
    if kind is float:
        if not math.isfinite(value):
            raise ValueError("input floats must be finite")
        return ["float", value.hex()]
    if kind is str:
        if len(value) > budget.max_string_chars:
            raise _BudgetExceeded("string literal budget exhausted")
        return ["str", value]
    if kind is tuple:
        return ["tuple", [_literal(v, budget, remaining, depth + 1) for v in value]]
    raise ValueError("entry values must be exact immutable builtin literals")


def _validated_spec(spec, budget):
    if type(spec) is not FiniteEntrySpec or not _identifier(spec.entry_function):
        raise ValueError("entry spec requires one named source entry")
    if type(spec.positional_domains) is not tuple or type(spec.keyword_domains) is not tuple:
        raise ValueError("domains must be immutable tuples")
    if (type(spec.observed_globals) is not tuple
            or any(not _identifier(n) for n in spec.observed_globals)
            or len(set(spec.observed_globals)) != len(spec.observed_globals)):
        raise ValueError("observed globals must be distinct identifiers")
    names = []
    domains = list(spec.positional_domains)
    for item in spec.keyword_domains:
        if type(item) is not tuple or len(item) != 2 or not _identifier(item[0]):
            raise ValueError("keyword domains must be (name, alternatives) pairs")
        names.append(item[0])
        domains.append(item[1])
    if len(names) != len(set(names)):
        raise ValueError("duplicate keyword domain")
    # Check cardinality before generating the product or encoding its values.
    cardinality = 1
    for domain in domains:
        if type(domain) is not tuple or not domain:
            raise ValueError("every domain must be a nonempty tuple")
        cardinality *= len(domain)
    if cardinality > budget.max_variants:
        raise _BudgetExceeded("finite entry product exceeds variant budget", cardinality)
    remaining = [budget.max_literal_nodes]
    encoded = []
    for domain in domains:
        values = [_literal(value, budget, remaining) for value in domain]
        keys = [_json(value) for value in values]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate typed literal in a domain")
        encoded.append(values)
    wire = {
        "entry_function": spec.entry_function,
        "positional_domains": encoded[:len(spec.positional_domains)],
        "keyword_domains": [[name, values] for name, values in zip(names, encoded[len(spec.positional_domains):])],
        "observed_globals": list(spec.observed_globals),
        "heap_initialization": "fresh source initialization for every argument combination",
        "input_construction": "fresh literal construction; no cross-argument alias contract",
    }
    return domains, names, wire, cardinality


def _implementation_digest():
    # Include the sibling model helpers used for heap, generator and coroutine
    # execution. This identifies the installed source, not a mechanical proof.
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _literal_expression(value):
    if type(value) is tuple:
        return ast.Tuple(elts=[_literal_expression(v) for v in value], ctx=ast.Load())
    return ast.Constant(value=value)


def _return_name(tree, observed):
    occupied = set(observed)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            occupied.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            occupied.add(node.name)
        elif isinstance(node, ast.Constant) and type(node.value) is str:
            occupied.add(node.value)
    name = "_finite_entry_return"
    while name in occupied:
        name += "_"
    return name


def analyze_finite_entry(source: str, spec: FiniteEntrySpec, *, budget: FamilyBudget | None = None):
    """Derive the complete finite input/output relation, without a candidate.

    The original module runs before the appended entry call in every variant.
    Escaping target exceptions, unsupported source and exhausted computation
    remain residual; a prefix or successful subset cannot verify the family.
    """
    if type(source) is not str:
        raise ValueError("source must be exact text")
    if budget is None:
        budget = FamilyBudget()
    if type(budget) is not FamilyBudget:
        raise ValueError("budget must be a FamilyBudget")
    try:
        raw = source.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("source must be valid UTF-8") from exc
    result = {
        "schema_version": 1, "semantics_version": SEMANTICS_VERSION,
        "model_semantics_version": heap_model.SEMANTICS_VERSION,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "status": "unknown", "rows": [], "covered_variants": 0,
        "declared_variants": None, "verified_variants": 0,
        "entry_spec_sha256": None, "entry_spec": None,
        "effective_budget": asdict(budget),
        "implementation_sha256": _implementation_digest(),
        "implementation_stable": True,
        "runtime_observations_used": False, "target_source_executed": False,
        "kcfa_results_certified": False, "full_python_soundness_proved": False,
        "mechanized_semantics_proof": False,
        "residual": [
            "Inputs outside the declared finite domains remain uncovered.",
            "Other initial heaps, repeated invocations on one heap, external interference and unsupported Python semantics remain uncovered.",
            "No result authorizes KCFA pruning or an unrestricted framework summary.",
        ],
    }
    try:
        if len(raw) > budget.max_source_bytes:
            raise _BudgetExceeded("source byte budget exhausted")
        domains, names, wire, cardinality = _validated_spec(spec, budget)
        result.update(entry_spec=wire, entry_spec_sha256=_digest(wire), declared_variants=cardinality)
        tree = ast.parse(source)
        compile(tree, "<finite-entry-source>", "exec", dont_inherit=True)
        if not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name == spec.entry_function for node in tree.body):
            raise ValueError("entry must name a top-level source function declaration")
        return_name = _return_name(tree, spec.observed_globals)
        for values in itertools.product(*domains):
            positional = values[:len(spec.positional_domains)]
            keywords = values[len(spec.positional_domains):]
            arguments = {
                "positional": [_literal(v, budget, [budget.max_literal_nodes]) for v in positional],
                "keywords": [[n, _literal(v, budget, [budget.max_literal_nodes])] for n, v in zip(names, keywords)],
            }
            call = ast.Call(func=ast.Name(id=spec.entry_function, ctx=ast.Load()),
                            args=[_literal_expression(v) for v in positional],
                            keywords=[ast.keyword(arg=n, value=_literal_expression(v)) for n, v in zip(names, keywords)])
            assignment = ast.fix_missing_locations(ast.Assign(
                targets=[ast.Name(id=return_name, ctx=ast.Store())], value=call))
            variant_source = source + "\n" + ast.unparse(assignment) + "\n"
            variant = heap_model.analyze_closed_source(
                variant_source, [return_name, *spec.observed_globals], budget=budget.per_variant)
            completed = variant["status"] == "closed_model_completed"
            observations = variant["observations"]
            row = {
                "variant_id": _digest(arguments), "arguments": arguments,
                "derived_source_sha256": hashlib.sha256(variant_source.encode()).hexdigest(),
                "status": variant["status"],
                "observations": ({"return": observations[return_name],
                                  "globals": {n: observations[n] for n in spec.observed_globals}}
                                 if completed else {}),
                "prefix_calls": variant.get("prefix_calls", []),
                "steps": variant.get("steps", 0), "objects": variant.get("objects", 0),
                "diagnostic": variant.get("diagnostic"),
            }
            result["rows"].append(row)
            result["covered_variants"] += int(completed)
        result["status"] = "finite_model_completed" if result["covered_variants"] == cardinality else "residual"
    except _BudgetExceeded as exc:
        result["diagnostic"] = str(exc)
        if exc.declared_variants is not None:
            result["declared_variants"] = exc.declared_variants
    except (SyntaxError, ValueError) as exc:
        result.update(status="unsupported", diagnostic=type(exc).__name__ + ": " + str(exc))
    except (RecursionError, OverflowError):
        result["diagnostic"] = "host structure or numeric limit"
    if _implementation_digest() != result["implementation_sha256"]:
        result.update(status="unknown", implementation_stable=False,
                      diagnostic="model source changed during family analysis")
    return result


def _claim_literal(value, remaining, depth=0):
    remaining[0] -= 1
    if remaining[0] < 0 or depth > 64:
        raise ValueError("candidate observation structure budget exceeded")
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _claim_literal(item, remaining, depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _claim_literal(item, remaining, depth + 1)
        return
    raise ValueError("candidate observations must be finite JSON literals")


def _same_literal(left, right):
    if type(left) is not type(right):
        return False
    if type(left) is list:
        return len(left) == len(right) and all(_same_literal(a, b) for a, b in zip(left, right))
    if type(left) is dict:
        return left.keys() == right.keys() and all(_same_literal(left[k], right[k]) for k in left)
    # Keep signed zero exact in the typed contract.
    if type(left) is float:
        return left.hex() == right.hex()
    return left == right


def check_finite_entry(source: str, spec: FiniteEntrySpec, candidate: dict, *, budget: FamilyBudget | None = None):
    """Independently derive every row, then compare an untrusted full relation.

    Candidate format binds the original source, model version, complete entry
    spec and exact row set. It cannot reduce the caller-declared input domain.
    """
    budget = FamilyBudget() if budget is None else budget
    result = analyze_finite_entry(source, spec, budget=budget)
    try:
        required = {"schema_version", "semantics_version", "model_semantics_version",
                    "source_sha256", "entry_spec_sha256", "rows"}
        if (type(candidate) is not dict or any(type(key) is not str for key in candidate)
                or set(candidate) != required):
            raise ValueError("candidate requires the exact finite-entry schema")
        _claim_literal(candidate, [100_000])
        if len(_json(candidate).encode()) > budget.max_candidate_bytes:
            result.update(status="unknown", diagnostic="candidate byte budget exhausted")
            return result
        if type(candidate["schema_version"]) is not int or candidate["schema_version"] != 1:
            raise ValueError("candidate schema mismatch")
        for key in ("semantics_version", "model_semantics_version", "source_sha256", "entry_spec_sha256"):
            if type(candidate[key]) is not str or candidate[key] != result[key]:
                raise ValueError("candidate " + key + " mismatch")
        if type(candidate["rows"]) is not dict:
            raise ValueError("candidate rows must map variant identities to observations")
        expected_rows = {row["variant_id"]: row for row in result["rows"]}
        if set(candidate["rows"]) != set(expected_rows):
            raise ValueError("candidate must cover exactly every declared input row")
        if result["status"] != "finite_model_completed":
            return result
        matches = {key: _same_literal(candidate["rows"][key], row["observations"])
                   for key, row in expected_rows.items()}
        result["row_matches"] = matches
        if all(matches.values()):
            result.update(status="conditional_family_verified", verified_variants=len(matches))
        else:
            result.update(status="rejected", diagnostic="candidate relation differs from source-derived rows")
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        result.update(status="rejected", diagnostic=str(exc))
    return result
