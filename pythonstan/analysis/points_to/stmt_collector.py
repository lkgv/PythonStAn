from typing import List
from pythonstan.ir import *

class StmtCollector:
    assigns: List[IRAssign]
    store_attrs: List[IRStoreAttr]
    load_attrs: List[IRLoadAttr]
    store_subscrs: List[IRStoreSubscr]
    load_subscrs: List[IRLoadAttr]
    allocs: List[IRCall]
    invokes: List[IRCall]

    def __init__(self):
        self.assigns = []
        self.store_attrs = []
        self.load_attrs = []
        self.store_subscrs = []
        self.load_subscrs = []
        self.allocs = []
        self.invokes = []
