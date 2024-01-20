from typing import Set, Dict

from .obj_label import ObjLabel


class Summarized:
    maybe_summarized: Set[ObjLabel]
    definitely_summarized: Set[ObjLabel]

    def __init__(self):
        self.maybe_summarized = {*()}
        self.definitely_summarized = {*()}

    def copy_from(self, s: 'Summarized'):
        self.maybe_summarized = s.maybe_summarized
        self.definitely_summarized = s.definitely_summarized

    def get_maybe_summarized(self) -> Set[ObjLabel]:
        return self.maybe_summarized

    def get_definitely_summarized(self) -> Set[ObjLabel]:
        return self.definitely_summarized

    def add_definitely_summarized(self, obj_label: ObjLabel):
        self.definitely_summarized.add(obj_label)
        self.maybe_summarized.add(obj_label)

    def is_maybe_summarized(self, obj_label: ObjLabel) -> bool:
        return obj_label in self.maybe_summarized

    def is_definitely_summarized(self, obj_label: ObjLabel) -> bool:
        return obj_label in self.definitely_summarized

    def clear(self):
        self.maybe_summarized.clear()
        self.definitely_summarized.clear()

    def join(self, s: 'Summarized') -> bool:
        ret = (self.maybe_summarized.issuperset(s.maybe_summarized) or
               self.definitely_summarized.issubset(s.definitely_summarized))
        self.maybe_summarized.update(s.maybe_summarized)
        self.definitely_summarized.intersection_update(s.definitely_summarized)
        return ret

    def __eq__(self, other) -> bool:
        if not isinstance(other, Summarized):
            return False
        return (self.maybe_summarized == other.maybe_summarized and
                self.definitely_summarized == other.maybe_summarized)

    def __hash__(self):
        return hash(self.maybe_summarized) * 3 + hash(self.definitely_summarized) * 17

    def __str__(self):
        return f"<Summarized: maybe={self.maybe_summarized}, definitely={self.definitely_summarized}"

    def summarize(self, obj_labels: Set[ObjLabel]) -> Set[ObjLabel]:
        if not any([l for l in obj_labels if l.is_singleton() and self.is_maybe_summarized(l)]):
            return obj_labels
        new_objs = {*()}
        for l in obj_labels:
            if l.is_singleton() and self.is_maybe_summarized(l):
                new_objs.add(l.make_summary())
                if not self.is_definitely_summarized(l):
                    new_objs.add(l)
            else:
                new_objs.add(l)
        return new_objs
