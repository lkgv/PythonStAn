"""Fresh accounting checks after the lost W3 workspace was rebuilt."""

from __future__ import annotations

from collections.abc import Iterator
from typing import override

import pytest

from pythonstan.analysis.contracts.cost_model import (
    COST_MODEL_DIGEST,
    StructuralWorkMeter,
    cost_model_document,
    visit_json_text,
)


@pytest.mark.parametrize("count,ticks", [(0, 0), (1, 1), (7, 1), (8, 1), (9, 2), (16, 2), (17, 3)])
def test_started_batch_is_paid_before_each_admitted_visit(count: int, ticks: int) -> None:
    used = 0
    visited = 0

    def charge(amount: int, category: str) -> None:
        nonlocal used
        assert category == "test_walk"
        used += amount

    def record(category: str, amount: int) -> None:
        nonlocal visited
        visited += amount
        assert visited <= used * 8

    with StructuralWorkMeter(charge, record, "test_walk") as meter:
        for _ in range(count):
            meter.visit()
    assert used == ticks
    assert visited == count


def test_denied_second_batch_does_not_execute_the_ninth_visit() -> None:
    used = 0
    recorded = 0

    def charge(amount: int, category: str) -> None:
        nonlocal used
        if used + amount > 1:
            raise RuntimeError("quota")
        used += amount

    def record(category: str, amount: int) -> None:
        nonlocal recorded
        recorded += amount

    meter = StructuralWorkMeter(charge, record, "test_walk")
    for _ in range(8):
        meter.visit()
    with pytest.raises(RuntimeError, match="quota"):
        meter.visit()
    assert (used, recorded, meter.raw_operations, meter.charged_ticks) == (1, 8, 8, 1)


def test_fresh_operations_cannot_reuse_surplus_credit() -> None:
    charges: list[int] = []
    for _ in range(2):
        with StructuralWorkMeter(
            lambda amount, category: charges.append(amount), lambda category, amount: None, "test"
        ) as meter:
            meter.visit()
            meter.visit()
    assert charges == [1, 0, 1, 0]
    with pytest.raises(RuntimeError, match="closed"):
        meter.visit()


def test_scope_change_rejects_even_zero_delta_credit() -> None:
    scope = [1]
    charges: list[int] = []
    meter = StructuralWorkMeter(
        lambda amount, category: charges.append(amount),
        lambda category, amount: None,
        "test",
        scope=lambda: tuple(scope),
    )
    meter.visit()
    scope[0] = 2
    with pytest.raises(RuntimeError, match="another scope"):
        meter.visit()
    assert charges == [1]
    assert meter.raw_operations == 1


@pytest.mark.parametrize("invalid", [-1, True])
def test_invalid_integer_counts_have_no_admitted_work(invalid: int) -> None:
    charges: list[int] = []
    meter = StructuralWorkMeter(
        lambda amount, category: charges.append(amount), lambda category, amount: None, "test"
    )
    with pytest.raises(ValueError):
        meter.visit(invalid)
    assert charges == []
    assert meter.raw_operations == 0


@pytest.mark.parametrize("text", ["", "x" * 1024, "x" * 1025, "中" * 500, "😀" * 300, "\ud800"])
def test_text_count_agrees_with_utf8_surrogatepass_without_unfunded_copy(text: str) -> None:
    admitted: list[int] = []
    visit_json_text(text, admitted.append)
    assert admitted[0] == 1
    assert sum(admitted) == 1 + (len(text.encode("utf-8", "surrogatepass")) + 1023) // 1024


def test_first_text_admission_can_stop_before_inspection() -> None:
    class Unreadable(str):
        @override
        def __iter__(self) -> Iterator[str]:
            raise AssertionError("text inspected before admission")

    def reject(amount: int) -> None:
        raise RuntimeError("quota")

    with pytest.raises(RuntimeError, match="quota"):
        visit_json_text(Unreadable("abc"), reject)


def test_fixed_descriptor_matches_durable_interruption_record() -> None:
    assert COST_MODEL_DIGEST == "21d3f74512c790657e1c779e914f596106ccde903d66e578684085b3f6e3e043"
    descriptor = cost_model_document()
    descriptor["structural_operations_per_tick"] = 999
    assert cost_model_document()["structural_operations_per_tick"] == 8
