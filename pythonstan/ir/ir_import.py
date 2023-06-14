from typing import Union, Set, Optional
import ast
from .ir_statement import IRAbstractStmt
from pythonstan.utils.ast_rename import RenameTransformer

__all__ = ["IRImport"]


class IRImport(IRAbstractStmt):
    stmt: Union[ast.Import, ast.ImportFrom]
    module: Optional[str]
    name: str
    asname: Optional[str]
    level: int

    def __init__(self, stmt):
        self.stmt = stmt
        if isinstance(stmt, ast.ImportFrom):
            if stmt.module is not None:
                self.module = stmt.module
            else:
                self.module = ""
            self.level = stmt.level
        else:
            self.module = None
            self.level = 0
        self.name = stmt.names[0].name
        self.asname = stmt.names[0].asname

    def __str__(self):
        ast.unparse(self.stmt)

    def __repr__(self):
        ast.unparse(self.stmt)

    def get_stores(self) -> Set[str]:
        stores = {*()}
        for name in self.stmt.names:
            if name.asname is None:
                stores.add(name.name)
            else:
                stores.add(name.asname)
        return stores

    def rename(self, old_name, new_name, ctxs):
        if ast.Store not in ctxs:
            return
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.stmt = renamer.visit(self.stmt)

    def get_ast(self) -> Union[ast.Import, ast.ImportFrom]:
        return self.stmt
