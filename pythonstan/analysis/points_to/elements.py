from typing import Union, Optional

from .points_to_set import PointsToSet


class Var:
    ...

class Pointer:
    def get_points_to_set(self)-> Optional[PointsToSet]:
        ...

    def set_points_to_set(self, pts: PointsToSet):
        ...


class StaticField:
    ...


class InstanceField:
    ...


class ArrayIndex:
    ...