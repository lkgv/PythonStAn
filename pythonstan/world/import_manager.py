from pythonstan.ir import IRImport, IRModule


class ImportManager:
    mod_import_submod: dict[tuple[IRModule, IRImport], IRModule] = {}

    def build(self) -> None:
        self.mod_import_submod = {}

    def set_import(self, mod: IRModule, imp: IRImport, submod: IRModule) -> None:
        self.mod_import_submod[(mod, imp)] = submod

    def get_import(self, mod: IRModule, imp: IRImport) -> IRModule:
        return self.mod_import_submod[(mod, imp)]
