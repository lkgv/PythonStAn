"""Registered finite value-partition capacity policy."""
from dataclasses import dataclass
from pythonstan.analysis.domains.relations import KeyValueRelation
@dataclass(frozen=True)
class PartitionPolicy:
    enabled:bool=False
    max_partitions:int=4
    string_enumeration_cap:int=4
    def apply(self,relation):
        if not self.enabled:return relation.coarsen(0)
        return relation.coarsen(self.max_partitions)
