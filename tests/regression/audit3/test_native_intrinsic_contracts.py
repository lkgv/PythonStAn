"""Real intrinsic budget/summary boundaries and uncached module import orders."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from pythonstan.analysis.contracts.budgets import AnalysisBudget
from pythonstan.analysis.pointer.kcfa.context import CallStringContext
from pythonstan.analysis.pointer.kcfa.heap_model import Field, FieldKind
from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite, ConstantObject
from pythonstan.compat.intrinsic_boundary import checked_intrinsic_summary
from pythonstan.compat.legacy_ai.api import AISummary


def _constant() -> ConstantObject:
    return ConstantObject(CallStringContext(), AllocSite("intrinsic-test", AllocKind.CONSTANT), 7)


def test_canonical_budget_factory_preserves_defaults_and_exact_control_types() -> None:
    assert AnalysisBudget.from_options({}) == AnalysisBudget()
    supplied = {"max_iterations": 0, "strict_exceptions": True, "max_const_strings": 3}
    result = AnalysisBudget.from_options(supplied)
    assert result.max_iterations == 0
    assert result.strict_exceptions is True
    assert result.max_const_strings == 3
    with pytest.raises(ValueError, match="type int"):
        AnalysisBudget.from_options({"max_iterations": True})
    with pytest.raises(ValueError, match="supported range"):
        AnalysisBudget.from_options({"max_const_strings": 0})
    with pytest.raises(ValueError, match="dictionary"):
        AnalysisBudget.from_options(MappingProxyType({"max_iterations": 1}))


def test_legacy_partial_summary_defaults_produce_the_existing_real_record() -> None:
    obj = _constant()
    field = Field(FieldKind.ATTRIBUTE, "field")
    legacy = SimpleNamespace(
        is_complete=True,
        ret={obj},
        writes=[SimpleNamespace(obj_addrs={obj}, field_key=field, value_addrs={obj})],
    )
    result = checked_intrinsic_summary(legacy)
    assert isinstance(result, AISummary)
    assert result.ret == frozenset({obj})
    assert result.writes[0].obj_addrs == frozenset({obj})
    assert result.writes[0].field_key == field
    assert not result.calls and not result.unknown_effects and not result.may_raise


def test_incomplete_backend_does_not_require_or_publish_its_unchecked_payload() -> None:
    result = checked_intrinsic_summary(
        SimpleNamespace(is_complete=False, incomplete_reasons=("budget",), ret=object())
    )
    assert not result.is_complete
    assert result.incomplete_reasons == ("budget",)
    assert not result.ret and not result.writes
    original = AISummary(is_complete=False, incomplete_reasons=("budget",))
    original.core_statistics = {"total_work": 5}
    assert checked_intrinsic_summary(original).core_statistics == {"total_work": 5}


@pytest.mark.parametrize(
    "legacy",
    [
        SimpleNamespace(is_complete=True, ret={"not a Native object"}, writes=()),
        SimpleNamespace(is_complete=1, ret=(), writes=()),
        SimpleNamespace(is_complete=True, ret=()),
        SimpleNamespace(is_complete=True, ret=(), writes=[object()]),
    ],
)
def test_complete_backend_payload_is_checked_before_native_publication(legacy: object) -> None:
    with pytest.raises((TypeError, KeyError)):
        checked_intrinsic_summary(legacy)


def test_real_summary_keeps_owned_core_statistics() -> None:
    summary = AISummary(ret=frozenset({_constant()}))
    summary.core_statistics = {"total_work": 3}
    decoded = checked_intrinsic_summary(summary)
    assert decoded.ret == summary.ret
    assert decoded.core_statistics == {"total_work": 3}
    summary.core_statistics["total_work"] = 9
    assert decoded.core_statistics == {"total_work": 3}


@pytest.mark.parametrize(
    "module",
    [
        "pythonstan.compat.legacy_ai.value_domain",
        "pythonstan.analysis.pointer.kcfa.solver",
        "pythonstan.analysis.composition.native_intrinsics",
        "pythonstan.compat.intrinsic_boundary",
        "pythonstan.compat.shared_ai",
    ],
)
def test_cold_module_imports_do_not_need_artificial_preloading(module: str) -> None:
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
