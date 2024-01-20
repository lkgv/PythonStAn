from typing import Dict, List


class Worklist:
    pointers: Dict
    call_edges: List

    def __init__(self):
        self.pointers = {}
        self.call_edges = []

    def add_pts(self, pointer, pts):
        if pointer not in self.pointers:
            self.pointers[pointer] = pts
        else:
            self.pointers[pointer].add_all(pts)

    def add_edge(self, call_edge):
        self.call_edges.append(call_edge)

    def is_empty(self):
        return len(self.pointers) == 0 and len(self.call_edges) == 0

    def get(self):
        if len(self.call_edges) > 0:
            return self.call_edges.pop()
        elif len(self.pointers) > 0:
            k, v = next(iter(self.pointers.items()))
            self.pointers.pop(k)
            return k, v
        else:
            return None

