from pythonstan.ir import IRImport, IRScope


class ImportManager:
    def resolve_import(self, imp: IRImport) -> IRScope:
        ...

