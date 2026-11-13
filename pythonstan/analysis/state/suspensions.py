"""Correlated alternatives for a summarized suspended activation."""

from __future__ import annotations

from dataclasses import replace

from .metadata import GeneratorMeta

# Matches the existing finite activation discriminator; overflow is represented
# by an explicit conservative object rather than an exact retained cursor.
MAX_SUSPENSION_STATES = 32


def suspension_states(record: GeneratorMeta) -> tuple[GeneratorMeta, ...]:
    """Flatten only the alternative relation; each execution record stays whole."""
    from .transform import semantic_payload_equal

    result: list[GeneratorMeta] = []
    pending = [record]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        primary = replace(current, alternatives=())
        if not any(semantic_payload_equal(primary, prior) for prior in result):
            result.append(primary)
        pending.extend(current.alternatives)
    return tuple(result)


def merge_suspensions(left: GeneratorMeta, right: GeneratorMeta) -> GeneratorMeta:
    """Union whole activation states without crossing their correlated fields."""
    from .transform import semantic_payload_equal

    states = list(suspension_states(left))
    for state in suspension_states(right):
        if not any(semantic_payload_equal(state, prior) for prior in states):
            states.append(state)
    overflow = left.overflow or right.overflow or len(states) > MAX_SUSPENSION_STATES
    retained = states[:MAX_SUSPENSION_STATES]
    return replace(retained[0], alternatives=tuple(retained[1:]), overflow=overflow)


def suspensions_cover(left: GeneratorMeta, right: GeneratorMeta) -> bool:
    from .transform import semantic_payload_equal

    targets = suspension_states(right)
    return all(
        any(semantic_payload_equal(source, target) for target in targets)
        for source in suspension_states(left)
    )
