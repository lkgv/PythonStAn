from typing import Set


class PointsToSet:
    pts: Set

    def __init__(self):
        self.pts = set()

    def add_obj(self, obj):
        ret = obj in self.pts
        self.pts.add(obj)
        return ret

    def add_all(self, pts):
        if self.pts.issuperset(pts):
            return False
        else:
            self.pts.update(pts)

    def has(self, obj):
        return obj in self.pts

    def is_empty(self):
        return len(self.pts) == 0

    def size(self):
        return len(self.pts)

    def get_objs(self):
        return self.pts

    def __str__(self):
        return str(self.pts)

    def __repr__(self):
        return str(self.pts)

    def __iter__(self):
        return iter(self.pts)
