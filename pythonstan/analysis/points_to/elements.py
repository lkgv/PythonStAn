from typing import Union, Optional

from .points_to_set import PointsToSet


class Var:
    ...


class Pointer:
    pts: Optional[PointsToSet]

    def get_points_to_set(self) -> Optional[PointsToSet]:
        return self.pts

    def set_points_to_set(self, pts: PointsToSet):
        self.pts = pts


class StaticField:
    ...


class InstanceField:
    ...


class ArrayIndex:
    ...
