"""Opaque host iteration preserves the Python primitive's observable contract."""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterator
from typing import Self

import pytest

from pythonstan.compat.host_iteration import iter_objects


class _TerminalIterator:
    def __init__(self, item: object, terminal: object) -> None:
        self.item = item
        self.terminal = terminal
        self.started = False
        self.iter_calls = 0

    def __iter__(self) -> Self:
        self.iter_calls += 1
        return self

    def __next__(self) -> object:
        if self.started:
            raise StopIteration(self.terminal)
        self.started = True
        return self.item


def test_returns_actual_iterator_and_preserves_terminal_payload_identity() -> None:
    item, terminal = object(), object()
    source = _TerminalIterator(item, terminal)
    iterator = iter_objects(source)
    assert iterator is source
    assert source.iter_calls == 1
    assert next(iterator) is item
    with pytest.raises(StopIteration) as stopped:
        next(iterator)
    assert stopped.value.value is terminal


def test_iter_slot_runs_eagerly_and_errors_before_any_next() -> None:
    observed: list[str] = []

    class BrokenIterable:
        def __iter__(self) -> Iterator[object]:
            observed.append("iter")
            raise RuntimeError("eager iter sentinel")

    with pytest.raises(RuntimeError, match="eager iter sentinel"):
        iter_objects(BrokenIterable())
    assert observed == ["iter"]


def test_legacy_getitem_slots_stay_lazy_and_preserve_index_order() -> None:
    token = object()

    class LegacySequence:
        __slots__ = ("indices",)

        def __init__(self) -> None:
            self.indices: list[int] = []

        def __getitem__(self, index: int) -> object:
            self.indices.append(index)
            if index >= 1:
                raise IndexError(index)
            return token

    source = LegacySequence()
    iterator = iter_objects(source)
    assert source.indices == []
    assert next(iterator) is token
    with pytest.raises(StopIteration):
        next(iterator)
    assert source.indices == [0, 1]


def test_dynamic_attribute_is_not_a_type_level_iteration_slot() -> None:
    class DynamicAttribute:
        def __getattr__(self, name: str) -> Callable[[], Iterator[object]]:
            if name == "__iter__":
                return lambda: iter(())
            raise AttributeError(name)

    source = DynamicAttribute()
    assert tuple(source.__iter__()) == ()
    with pytest.raises(TypeError, match="not iterable"):
        iter_objects(source)


def test_explicitly_disabled_iteration_does_not_fall_back_to_getitem() -> None:
    class DisabledIteration:
        __iter__: None = None

        def __getitem__(self, index: int) -> object:
            raise AssertionError("Disabled iteration must not read a sequence item")

    with pytest.raises(TypeError, match="not iterable"):
        iter_objects(DisabledIteration())


def test_invalid_iter_return_is_rejected_at_the_primitive_boundary() -> None:
    class InvalidIterable:
        def __iter__(self) -> object:
            return 1

    with pytest.raises(TypeError, match="non-iterator"):
        iter_objects(InvalidIterable())


def test_generator_return_payload_survives_without_generator_wrapping() -> None:
    token = object()

    def source() -> Generator[object, None, object]:
        yield token
        return token

    generator = source()
    iterator = iter_objects(generator)
    assert iterator is generator
    assert next(iterator) is token
    with pytest.raises(StopIteration) as stopped:
        next(iterator)
    assert stopped.value.value is token


def test_primitive_is_captured_before_later_builtin_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _TerminalIterator(object(), object())

    def replaced(value: object) -> Iterator[object]:
        raise AssertionError(
            "A later mutable builtin lookup must not replace the captured primitive"
        )

    with monkeypatch.context() as patch:
        patch.setattr("builtins.iter", replaced)
        observed = iter_objects(source)
    assert observed is source


def test_real_legacy_string_consumer_accepts_getitem_only_sequence() -> None:
    from pythonstan.compat.legacy_ai.value_domain import AbsVal

    class LegacyStrings:
        def __getitem__(self, index: int) -> str:
            return ("a", "b")[index]

    result = AbsVal(str_const=LegacyStrings()).get_string_lattice().get_consts()
    assert result == frozenset({"a", "b"})
