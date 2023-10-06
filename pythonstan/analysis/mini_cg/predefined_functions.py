from .lattice.obj_label import ObjLabel, LabelKind
from pythonstan.ir import IRFunc, IRScope


PREDEFINED_FUNCTIONS_LIST = [
    # ('dict', ObjLabel()),
    ...
]


def evaluate_predefined_function(*args, **kwargs):
    ...