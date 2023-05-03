import copy
import ast
from ast import NodeTransformer

class RenameTransformer(NodeTransformer):
    def __init__(self, old_name, new_name):
        self.old_name = old_name
        self.new_name = new_name

    def visit_Name(self, node: ast.Name):
        name = node
        if name.id == self.old_name:
            name = copy.deepcopy(node)
            name.id = self.new_name
        return name

    def visit_alias(self, node):
        alias = node
        if alias.asname == self.old_name:
            alias = copy.deepcopy(node)
            alias.asname = self.new_name
        elif alias.asname is None and alias.name == self.old_name:
            alias = copy.deepcopy(node)
            alias.asname = self.new_name
        return alias
