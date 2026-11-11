"""Heap policy controls real object identities, independently of method k."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.context_selector import ContextSelector, ContextPolicy
from pythonstan.analysis.pointer.kcfa.context import CallSite, CallStringContext
from tests.pointer.kcfa.builders import make_site

ROOT = Path(__file__).resolve().parents[3]
PROBE = ROOT / "scripts/refactor/native_context_probe.py"


def _probe(case, heap_depth):
    settings = {} if heap_depth is None else {"heap_context_policy":"2-cfa", "heap_context_depth":heap_depth}
    command = [sys.executable, str(PROBE), "--repo", str(ROOT), "--case", case,
               "--policy", "2-cfa", "--budget", "6000", "--config-json", json.dumps(settings)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout)
    assert row["status"] == "ok", row
    assert row["statistics"]["converged"] == 1, row
    return row


@pytest.mark.parametrize("case,kind", [
    ("heap_precision","instance"), ("container_precision","list"),
    ("tuple_factory","tuple"), ("dict_factory","dict"),
    ("builtin_factory","list"), ("args_factory","tuple"), ("kwargs_factory","dict"),
    ("generator_factory","generator"),
])
def test_heap_depth_changes_real_allocation_identity_without_losing_values(case, kind):
    precise, coarse = _probe(case, None), _probe(case, 0)
    def values(row, name):
        return {entry["value"] for entry in row["values"][name]
                if entry["kind"] == "ConstantObject"}
    assert values(precise, "left") == {1}, precise
    assert values(precise, "right") == {2}, precise
    assert values(coarse, "left") == {1,2}, coarse
    assert values(coarse, "right") == {1,2}, coarse
    assert coarse["statistics"]["allocation_policy_decisions"][kind+":fresh"] > 0


def test_heap_config_roundtrips_and_refuses_invalid_depth():
    config = Config(heap_context_policy="1-obj", heap_context_depth=0)
    assert Config.from_dict(config.to_dict()) == config
    for invalid in (-1,4,True,"2"):
        with pytest.raises(ValueError):
            Config(heap_context_depth=invalid)


@pytest.mark.parametrize("case,kind", [("closure_factory","func"),("class_factory","class")])
def test_definition_factories_use_heap_policy_and_preserve_existing_may_values(case,kind):
    precise,coarse = _probe(case,None),_probe(case,0)
    # Native lexical result flow is already broader for these cases; do not
    # misrepresent an address-policy change as a value-precision fix. The actual
    # object identities must nevertheless change with the selected heap depth.
    assert precise["object_identity_counts"]["union_ab"] == 2
    assert coarse["object_identity_counts"]["union_ab"] == 1
    for row in (precise,coarse):
        for symbol,witness in (("left",1),("right",2)):
            assert witness in {entry["value"] for entry in row["values"][symbol]
                               if entry["kind"] == "ConstantObject"}
        assert row["statistics"]["allocation_policy_decisions"][kind+":fresh"] > 0


def test_no_unaudited_scalar_super_or_mapping_pair_constructors():
    import ast
    package = ROOT / "pythonstan/analysis/pointer/kcfa"
    bypasses=[]
    for path in package.rglob("*.py"):
        relative=str(path.relative_to(package))
        if relative=="object.py":
            # Low-level factories explicitly accept a caller-owned identity;
            # actual analyzer consumers must select/canonicalize before use.
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Name):
                continue
            if node.func.id not in {"ConstantObject","SuperObject","MappingItemObject"}:
                continue
            context=(node.args[0] if node.args else
                     next((kw.value for kw in node.keywords if kw.arg=="context"),None))
            if not (isinstance(context,ast.Call) and isinstance(context.func,ast.Attribute)
                    and context.func.attr=="select_allocation_context"):
                bypasses.append((relative,node.lineno,node.func.id))
    assert not bypasses,bypasses


@pytest.mark.parametrize("case,decision",[("mapping_items","tuple:alias"),("super_builtin","obj:fresh")])
def test_review_holdouts_use_declared_allocation_policy(case,decision):
    row=_probe(case,0)
    assert row["statistics"]["allocation_policy_decisions"].get(decision,0)>0,row
    assert 1 in {item["value"] for item in row["values"]["result"] if item["kind"]=="ConstantObject"},row
