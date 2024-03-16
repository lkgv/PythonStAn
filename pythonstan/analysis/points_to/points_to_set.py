from typing import Set, Optional

from .context import CSObj


class PointsToSet:
    pts: Set[CSObj]

    def __init__(self, obj: Optional[CSObj] = None):
        if obj is None:
            self.pts = set()
        else:
            self.pts = {obj}

    def add_obj(self, obj: CSObj) -> bool:
        ret = obj in self.pts
        self.pts.add(obj)
        return ret

    def add_all(self, pts: 'PointsToSet') -> bool:
        if self.pts.issuperset(pts):
            return False
        else:
            self.pts.update(pts)
            return True

    @classmethod
    def from_set(cls, pts: Set['CSObj']) -> 'PointsToSet':
        ret = cls()
        ret.pts = {x for x in pts}
        return ret

    def has(self, obj: CSObj) -> bool:
        return obj in self.pts

    def is_empty(self) -> bool:
        return len(self.pts) == 0

    def size(self) -> int:
        return len(self.pts)

    def get_objs(self) -> Set[CSObj]:
        return self.pts

    def __sub__(self, other: 'PointsToSet') -> 'PointsToSet':
        return self.from_set(self.pts - other.pts)

    def __str__(self):
        return str(self.pts)

    def __repr__(self):
        return str(self.pts)

    def __iter__(self):
        return iter(self.pts)
