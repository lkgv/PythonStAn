"""Opt-in consumer-fair native points work, without dropping pending facts.

The original FIFO Worklist remains in state.py for compatibility. This backend
is selected only by points_work_policy='consumer_fair'. OrderedDict lanes avoid
scanning or copying the pending queue during selection and promotion.
"""
from collections import OrderedDict

from pythonstan.utils.common import Box
from .pointer_flow_graph import PointerFlowNode, NormalNode
from .context import Ctx
from .points_to_set import PointsToSet


class ConsumerFairWorklist:
    HOT_QUOTA = 3

    def __init__(self):
        self.items_dict = {}
        self._hot = OrderedDict()
        self._cold = OrderedDict()
        self._hot_streak = 0
        self._hot_pops = 0
        self._cold_pops = 0
        self._promotions = 0
        self._cold_seeds = 0

    def _add(self, content, *, cold):
        scope, node, points = content
        if not isinstance(node, PointerFlowNode):
            raise TypeError("points work requires a PointerFlowNode")
        if isinstance(node, NormalNode) and not isinstance(node.var, Ctx):
            raise TypeError("normal points work requires a qualified Ctx")
        if not isinstance(points, PointsToSet):
            raise TypeError("points work requires a PointsToSet")
        existing = self.items_dict.get(node)
        if existing is not None:
            existing[2].val = existing[2].val.union(points)
            if not cold:
                self.promote(node)
            return False
        item = (scope, node, Box(points))
        self.items_dict[node] = item
        if cold:
            self._cold[node] = item
            self._cold_seeds += 1
        else:
            self._hot[node] = item
        return True

    def add(self, content):
        """Ordinary work is HOT, including a new delta for a pending cold node."""
        return self._add(content, cold=False)

    def add_seed(self, content, *, cold):
        """Classify a fresh seed atomically; existing HOT items never demote.

        The audited builtin initializer supplies the classification. Merely
        merging another seed into a pending node cannot reset its service age.
        """
        if type(cold) is not bool:
            raise ValueError("seed cold classification must be a boolean")
        return self._add(content, cold=cold)

    def promote(self, node):
        """Move one pending cold item to the HOT tail once, in constant time."""
        item = self._cold.pop(node, None)
        if item is None:
            return False
        self._hot[node] = item
        self._promotions += 1
        return True

    def pop(self):
        if self._cold and (not self._hot or self._hot_streak >= self.HOT_QUOTA):
            node, item = self._cold.popitem(last=False)
            self._hot_streak = 0
            self._cold_pops += 1
        elif self._hot:
            node, item = self._hot.popitem(last=False)
            self._hot_streak = min(self.HOT_QUOTA, self._hot_streak + 1)
            self._hot_pops += 1
        else:
            raise IndexError("pop from empty worklist")
        del self.items_dict[node]
        scope, actual_node, points = item
        return scope, actual_node, points.val

    def empty(self):
        return not self.items_dict

    def __len__(self):
        return len(self.items_dict)

    def iter_pending(self):
        """Enumerate all live native items, HOT FIFO then COLD FIFO.

        This is a diagnostic lane enumeration, not a replay of future weighted
        selections. It yields the same canonical tuple/Box objects as the index.
        """
        yield from self._hot.values()
        yield from self._cold.values()

    @property
    def items_list(self):
        """Read-only compatibility observation; mutable FIFO clients stay FIFO."""
        return tuple(self.iter_pending())

    def diagnostics(self):
        return {"points_work_policy": "consumer_fair", "hot_quota": self.HOT_QUOTA,
                "pending_hot": len(self._hot), "pending_cold": len(self._cold),
                "hot_pops": self._hot_pops, "cold_pops": self._cold_pops,
                "promotions": self._promotions, "cold_seed_nodes": self._cold_seeds}
