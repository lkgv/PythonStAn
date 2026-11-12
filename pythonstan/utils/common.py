import sys
from collections.abc import Collection, Hashable, Mapping, Sequence
from typing import ClassVar, Self


class Singleton:
    _instances: ClassVar[dict[type["Singleton"], "Singleton"]] = {}

    def __new__(class_: type[Self], *args: object, **kwargs: object) -> Self:
        if class_ not in class_._instances:
            class_._instances[class_] = super().__new__(class_, *args, **kwargs)
        instance = class_._instances[class_]
        if not isinstance(instance, class_):
            raise TypeError("Singleton cache entry has a different concrete class")
        return instance


class Box[T]:
    __slots__ = ["val"]
    val: T

    def __init__(self, content: T) -> None:
        self.val = content


def topo_sort[Node: Hashable](succ: Mapping[Node, Collection[Node]]) -> list[Node]:
    in_degree = dict.fromkeys(succ, 0)
    for _, tgts in succ.items():
        for tgt in tgts:
            in_degree[tgt] += 1
    ret = [node for node, ind in in_degree.items() if ind == 0]
    for src in ret:
        for tgt in succ[src]:
            in_degree[tgt] -= 1
            if in_degree[tgt] == 0:
                ret.append(tgt)
    return ret


def is_src_file(filename: str) -> bool:
    return filename.endswith(".py")


def srcfile_to_name(srcfile: str) -> str:
    if srcfile.endswith(".py"):
        return srcfile.rstrip(".py")
    return srcfile


def builtin_module_names() -> Sequence[str]:
    return sys.builtin_module_names


def deoptional[T](v: T | None, default: T) -> T:
    if v is None:
        return default
    return v


def set_deoptional[T](v: set[T] | None) -> set[T]:
    return deoptional(v, {*()})


def multimap_add[K, V](m: dict[K, set[V]], k: K, v: V) -> None:
    if k not in m:
        m[k] = {*()}
    m[k].add(v)
