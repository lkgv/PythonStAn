"""Native-specific context oracle; no AI/PTA result substitution."""
import ast
from dataclasses import fields, is_dataclass
import json
from pathlib import Path
import subprocess
import sys

import pytest

from pythonstan.analysis.pointer.kcfa.context import (
    AbstractContext, CallSite, CallStringContext, Ctx, FiniteContextAtom,
    OrderedBindingToken, Scope, finite_context_atom, ordered_binding_token,
)
from pythonstan.analysis.pointer.kcfa.context_selector import ContextPolicy, ContextSelector
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocSite, AllocKind
from pythonstan.analysis.pointer.kcfa.variable import Variable
from pythonstan.ir.ir_statements import IRCall, IRStatement
from tests.pointer.kcfa.builders import make_scope, make_site

ROOT = Path(__file__).resolve().parents[3]
PROBE = ROOT / "scripts/refactor/native_context_probe.py"


def _assert_finite_source(value):
    assert not isinstance(value, (AbstractContext, AbstractObject, Ctx, Scope))
    if isinstance(value, (tuple, frozenset)):
        for item in value:
            _assert_finite_source(item)
    elif isinstance(value, FiniteContextAtom):
        _assert_finite_source(value.source)
        _assert_finite_source(value.origins)
    elif isinstance(value, OrderedBindingToken):
        _assert_finite_source(value.bindings)
    # Source IR/AllocSite identities deliberately stay opaque: they contain
    # syntax, never runtime context and are finite in the frozen inventory.


@pytest.mark.parametrize("policy", list(ContextPolicy))
def test_all_19_context_policies_have_finite_atoms(policy):
    selector = ContextSelector(policy)
    scope = make_scope("subject", "module")
    statement = IRCall(ast.parse("again()").body[0].value)
    call = CallSite(statement, "subject")
    site = make_site(name="allocation")
    context = selector.empty_context()
    checkpoints = []
    for index in range(60):
        receiver = AbstractObject(context, site)
        argument = Ctx(context, scope, Variable("x"))
        context = selector.select_call_context(call, context, receiver,
                                               ordered_binding_token((argument,)))
        context = selector.select_alloc_context(context, site)
        for name in ("alloc_sites", "types", "receivers", "params"):
            _assert_finite_source(getattr(context, name, ()))
        if index in (29, 59):
            checkpoints.append(context)
    assert checkpoints[0] == checkpoints[1]


def test_parameter_tokens_preserve_order_keyword_names_and_duplicates():
    scope = make_scope("subject", "module")
    ctx = CallStringContext()
    a, b = (Ctx(ctx, scope, Variable(name)) for name in ("a", "b"))
    assert ordered_binding_token((a, b)) != ordered_binding_token((b, a))
    assert ordered_binding_token((a, a)) != ordered_binding_token((a,))
    assert ordered_binding_token((), (("left", a),)) != ordered_binding_token((), (("right", a),))
    # Caller contexts do not recursively enter a parameter token.
    assert finite_context_atom(Ctx(ctx.append(CallSite(IRCall(ast.parse("x()").body[0].value))), scope, Variable("a"))) == finite_context_atom(a)


def _probe(case, policy, budget):
    process = subprocess.run([sys.executable, str(PROBE), "--repo", str(ROOT),
                              "--case", case, "--policy", policy,
                              "--budget", str(budget)],
                             capture_output=True, text=True, timeout=15)
    assert process.returncode == 0, process.stderr
    row = json.loads(process.stdout)
    assert row["status"] == "ok", row
    return row


@pytest.mark.parametrize("policy", ["1-param", "2-param", "3-param", "2-obj", "3-obj", "1c2o"])
def test_recursive_native_context_counts_stabilize_without_budget_stop(policy):
    case = "param_recursion" if "param" in policy else "object_recursion"
    low, high = (_probe(case, policy, budget) for budget in (1000, 4000))
    for row in (low, high):
        assert row["statistics"]["converged"] == 1, row
        assert row["statistics"]["pending_work"] == 0
        assert row["max_context_shape_depth"] <= 3
    assert low["call_edges"] == high["call_edges"]
    assert low["statistics"]["selected_work_items"] == high["statistics"]["selected_work_items"]


@pytest.mark.parametrize("policy", ["1-param", "2-param", "3-param"])
def test_ordered_parameter_native_result_does_not_cross_pair(policy):
    row = _probe("swap", policy, 1000)
    assert row["statistics"]["converged"] == 1
    assert [item["value"] for item in row["values"]["left"]] == [1]
    assert [item["value"] for item in row["values"]["right"]] == [2]


def test_synthetic_sites_cannot_smuggle_recursive_owner_contexts():
    site = make_site(name="owner")
    context = ContextSelector(ContextPolicy.OBJ_3).empty_context()
    owner = AbstractObject(context, site)
    for _ in range(30):
        synthetic = AllocSite(("namespace", owner), AllocKind.DICT)
        context = context.append(owner)
        owner = AbstractObject(context, synthetic)
        atom = finite_context_atom(owner)
        _assert_finite_source(atom)
    # A bounded source-shape summary prevents recursive synthetic-owner sites
    # from growing into unbounded token terms as well.
    assert "source_shape_summary" in repr(atom.source)


def test_atom_constructor_rejects_contexts_scopes_and_nested_atoms():
    scope = make_scope("subject", "module")
    ctx = CallStringContext()
    good = FiniteContextAtom("variable", (scope.stmt, "x"))
    for source in (ctx, scope, Ctx(ctx, scope, Variable("x")), good,
                   ((good,),), AbstractObject(ctx, make_site())):
        with pytest.raises(ValueError):
            FiniteContextAtom("variable", source)
    with pytest.raises(ValueError):
        FiniteContextAtom("object", "site", (good,))
    with pytest.raises(ValueError):
        FiniteContextAtom("object", "site", ("a", "b", "c"))
    with pytest.raises(ValueError):
        OrderedBindingToken((("pos:0", ctx),))
