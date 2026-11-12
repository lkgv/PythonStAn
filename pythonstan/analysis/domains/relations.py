"""Finite may relations carried by individual abstract values.

Tokens are data rather than certificates. Cap overflow retains every old pair
by taking the Cartesian product at its original source point.
"""

from __future__ import annotations

from dataclasses import dataclass

from pythonstan.analysis.contracts.identity import ObjectId, ProgramPointId, stable_digest
from pythonstan.analysis.contracts.json_data import JsonObject, JsonValue


@dataclass(frozen=True)
class PartitionToken:
    point: ProgramPointId
    key_type: str
    key_repr: str
    handler: ObjectId
    partitioned: bool = True

    @classmethod
    def make(
        cls, point: ProgramPointId, key: object, handler: ObjectId, partitioned: bool = True
    ) -> PartitionToken:
        return cls(point, type(key).__name__, repr(key), handler, partitioned)

    @property
    def identity(self) -> str:
        return stable_digest(
            (self.point, self.key_type, self.key_repr, self.handler, self.partitioned)
        )

    def export(self) -> JsonObject:
        return {
            "token": self.identity,
            "point": self.point,
            "key_type": self.key_type,
            "key": self.key_repr,
            "handler": self.handler,
            "partitioned": self.partitioned,
        }


def _token_identity(token: PartitionToken) -> str:
    return token.identity


@dataclass(frozen=True)
class KeyValueRelation:
    tokens: frozenset[PartitionToken] = frozenset()

    def join(self, other: KeyValueRelation) -> KeyValueRelation:
        return KeyValueRelation(self.tokens | other.tokens)

    def coarsen(self, cap: int) -> KeyValueRelation:
        if len(self.tokens) <= cap:
            return self
        points = {token.point for token in self.tokens}
        result: set[PartitionToken] = set()
        for point in points:
            group = [token for token in self.tokens if token.point == point]
            for key_type, key_repr in {(token.key_type, token.key_repr) for token in group}:
                for handler in {token.handler for token in group}:
                    result.add(PartitionToken(point, key_type, key_repr, handler, False))
        return KeyValueRelation(frozenset(result))

    def export(self) -> list[JsonValue]:
        return [token.export() for token in sorted(self.tokens, key=_token_identity)]
