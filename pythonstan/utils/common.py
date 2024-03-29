import sys
from typing import Dict, Collection, Any, List


class Singleton(object):
    _instances = {}

    def __new__(class_, *args, **kwargs):
        if class_ not in class_._instances:
            class_._instances[class_] = super(Singleton, class_).__new__(class_, *args, **kwargs)
        return class_._instances[class_]


def topo_sort(succ: Dict[Any, Collection[Any]]) -> List[Any]:
    in_degree = {node: 0 for node in succ.keys()}
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
    return filename.endswith('.py')


def srcfile_to_name(srcfile: str) -> str:
    if srcfile.endswith('.py'):
        return srcfile.rstrip('.py')
    return srcfile


def builtin_module_names():
    return sys.builtin_module_names
