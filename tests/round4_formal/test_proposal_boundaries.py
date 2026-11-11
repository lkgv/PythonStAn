"""Independent checks of the proof boundary on the round-three hard cases.

These tests use only local immutable source and deterministic candidates. They
do not contact an LLM, execute upstream source, or treat runtime observations as
proof assumptions.
"""
import ast
from dataclasses import replace
from itertools import combinations, product
import json
from pathlib import Path

import pytest

from pythonstan.analysis.pointer.ai.certified.checker import (
    EXCEPTION_TYPES, FunctionSummary, ProgramCertificate, source_digest,
)
from pythonstan.analysis.pointer.ai.certified.driver import analyze_source
from pythonstan.analysis.pointer.ai.certified.read_specialization import (
    HeapAlternative, ReadProposal, ResidualState, check_list_read,
)


CORPUS = Path(__file__).resolve().parents[2] / "benchmark/realworld_hardcases"


def _hardcases():
    cases = []
    for cohort in ("web_orm", "validation", "plugins"):
        manifest = json.loads((CORPUS / cohort / "manifest.json").read_text())
        for case in manifest["cases"]:
            cases.append(pytest.param(CORPUS / cohort / case["source"], id=case["id"]))
    return cases


@pytest.mark.parametrize("path", _hardcases())
def test_generous_model_summary_cannot_certify_unsupported_realworld_source(path):
    source = path.read_text()
    names = sorted({node.name for node in ast.parse(source).body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))})
    # Even maximal exit/event sets in the old domain cannot discharge heap,
    # descriptor, metaclass, continuation or environment-model obligations.
    events = frozenset({"call:" + name for name in names}
                       | {"raise:" + name for name in EXCEPTION_TYPES})
    candidate = ProgramCertificate(source_digest(source), tuple(
        (name, FunctionSummary(events, True, EXCEPTION_TYPES)) for name in names))
    report = analyze_source(source, candidate=candidate, allow_fallback=False)
    assert report.status == "unsupported"
    assert report.candidate_check_status == "unsupported"
    assert not report.certified
    payload = report.to_dict()
    assert payload["certificate"] is None
    assert all(payload["residual"][key] for key in (
        "may_call_any", "may_modify_heap", "may_raise", "may_suspend"))


def test_supported_source_certificate_is_not_transferable_to_hidden_heap_effect():
    source = "def target():\n    return None\ndef entry(x):\n    target()\n"
    harmless = analyze_source(source)
    assert harmless.certified
    # A candidate tied to the new text still cannot assert the old heap contract.
    candidate = harmless.to_dict()["certificate"]
    changed = source + "    x.callback = target\n"
    candidate["source_sha256"] = source_digest(changed)
    report = analyze_source(changed, candidate=candidate, allow_fallback=False)
    assert report.status == "unsupported" and not report.certified
    assert report.to_dict()["residual"]["may_modify_heap"]


def _alternative(values):
    return HeapAlternative("list", True, len(values), len(values), tuple(
        (index, frozenset({value})) for index, value in enumerate(values)))


def test_accepted_read_contract_contains_all_finite_concrete_alternatives():
    """Enumerate actual finite list-next behavior independently of the checker.

    Include mixed lengths, alias contents and sticky exhaustion after append.
    An accepted proposal must contain each possible read or the real StopIteration.
    This is finite model checking of the local implication, not a loop theorem.
    """
    concrete = [()] + [("a",), ("b",)] + list(product(("a", "b"), repeat=2))
    families = [(values,) for values in concrete] + list(combinations(concrete, 2))
    proved = 0
    refused = 0
    for family, index, exhausted in product(families, range(4), (False, True)):
        state = ResidualState("iter", "list", index, exhausted, "heap", "locals",
                              "pending-exception", tuple(map(_alternative, family)), True)
        proposals = [ReadProposal("iter", "list", index, "stop")]
        proposals += [ReadProposal("iter", "list", index, "read", frozenset(values))
                      for values in (("a",), ("b",), ("a", "b"))]
        for proposal in proposals:
            checked = check_list_read(state, proposal)
            assert checked.residual_state is state
            if not checked.certified:
                refused += 1
                continue
            proved += 1
            for values in family:
                stops = exhausted or index >= len(values)
                if proposal.kind == "stop":
                    assert stops
                else:
                    assert not stops
                    assert values[index] in proposal.values
                    assert values[index] in checked.values
    assert proved > 0 and refused > 0


def test_local_read_proof_cannot_survive_unproved_iterator_or_live_mutation():
    state = ResidualState("iter", "list", 0, False, "heap", "locals", "finally",
                          (_alternative(("a",)),), True)
    proposal = ReadProposal("iter", "list", 0, "read", frozenset({"a"}))
    assert check_list_read(state, proposal).certified
    for changed in (
        replace(state, exact_builtin_iterator=False),
        replace(state, alternatives=(_alternative(("b",)),)),
        replace(state, alternatives=state.alternatives + (_alternative(()),)),
        replace(state, alternatives=(replace(state.alternatives[0], exact_builtin_list=False),)),
    ):
        result = check_list_read(changed, proposal)
        assert not result.certified
        assert result.residual_state is changed
