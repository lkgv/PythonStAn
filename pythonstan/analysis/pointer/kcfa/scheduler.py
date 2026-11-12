"""Fair or legacy arbitration over Native's four persistent work queues."""

from collections import deque
from collections.abc import Sequence, Sized
from typing import ClassVar, Literal

type NativeWorkKind = Literal["static", "constraint_replay", "builtin_replay", "points_to"]


class NativeScheduler:
    KIND_ORDER: ClassVar[tuple[NativeWorkKind, ...]] = (
        "static",
        "constraint_replay",
        "builtin_replay",
        "points_to",
    )

    def __init__(self, policy: str) -> None:
        if policy not in {"fair", "legacy"}:
            raise ValueError("Unknown native scheduling policy")
        self.policy = policy

    def select(
        self, queues: Sequence[Sized], cursor: int, *, policy: str | None = None
    ) -> tuple[NativeWorkKind | None, int]:
        if len(queues) != len(self.KIND_ORDER):
            raise ValueError("Native scheduler requires all four work kinds")
        fair = (self.policy if policy is None else policy) == "fair"
        start = cursor if fair else 0
        for offset in range(len(self.KIND_ORDER)):
            index = (start + offset) % len(self.KIND_ORDER)
            if len(queues[index]):
                return self.KIND_ORDER[index], (
                    (index + 1) % len(self.KIND_ORDER) if fair else cursor
                )
        return None, cursor

    def pop[T](self, queue: deque[T], *, policy: str | None = None) -> T:
        return (
            queue.popleft()
            if (self.policy if policy is None else policy) == "fair"
            else queue.pop()
        )

    def pending(self, queues: Sequence[Sized]) -> dict[str, int]:
        if len(queues) != len(self.KIND_ORDER):
            raise ValueError("Native scheduler requires all four work kinds")
        return {kind: len(queue) for kind, queue in zip(self.KIND_ORDER, queues, strict=False)}
