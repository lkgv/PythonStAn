from __future__ import annotations

import ast
import os
from importlib.util import resolve_name
from pathlib import Path
from typing import ClassVar, override

from pythonstan.ir import IRImport
from pythonstan.utils.common import builtin_module_names, is_src_file


def get_root(path: str, names: list[str]) -> str:
    prev_num = len(names)
    path_obj = Path(path)
    if path_obj.name == "__init__.py":
        module_path = path_obj.parent
    elif path_obj.suffix == ".py":
        module_path = path_obj.with_suffix("")
    else:
        module_path = path_obj
    parts = module_path.parts
    root_parts = parts[:-prev_num] if prev_num <= len(parts) else parts
    if not root_parts:
        return path_obj.anchor if path_obj.is_absolute() else ""
    return str(Path(*root_parts))


class Namespace:
    names: list[str]
    empty_ns: ClassVar[Namespace | None] = None

    ns_dict: ClassVar[dict[str, Namespace]] = {}

    def __init__(self, names: list[str]) -> None:
        assert len(names) > 0, "Cannot  construct empty namespace"
        self.names = names
        self.ns_dict[".".join(names)] = self

    @classmethod
    def build(cls, names: list[str]) -> Namespace:
        name_str = ".".join(names)
        if name_str in cls.ns_dict:
            return cls.ns_dict[name_str]
        return cls(names)

    @classmethod
    def from_str(cls, name_str: str) -> Namespace:
        return cls.build(name_str.split("."))

    @classmethod
    def from_path(cls, filename: str) -> Namespace:
        path_obj = Path(filename)
        if path_obj.name == "__init__.py":
            module_path = path_obj.parent
        elif path_obj.suffix == ".py":
            module_path = path_obj.with_suffix("")
        else:
            module_path = path_obj
        parts = list(module_path.parts)
        if parts and parts[0] == os.sep:
            parts = parts[1:]
        return cls(parts)

    def to_str(self) -> str:
        return ".".join(self.names)

    @override
    def __str__(self) -> str:
        return self.to_str()

    @override
    def __repr__(self) -> str:
        return self.to_str()

    def to_filepath(self, rootpath: str | None = None) -> str:
        path = f"{'/'.join(self.names)}.py"
        if rootpath is not None:
            path = os.path.join(rootpath, path)
        return path

    def to_dirpath(self, rootpath: str | None = None) -> str:
        path = f"{'/'.join(self.names)}/__init__.py"
        if rootpath is not None:
            path = os.path.join(rootpath, path)
        return path

    def base(self) -> str:
        return self.names[0]

    def relative_ns(self, names: list[str], level: int) -> Namespace:
        assert level >= 0
        assert len(self.names) >= level

        # TODO names=[''] is a special case, need to be clarified in the future
        if len(names) == 1 and names[0] == "":
            return self

        if level > 0:
            return self.build(self.names[:-level] + names)
        return self.build(self.names + names)

    def next_ns(self, names: list[str]) -> Namespace:
        return self.build(self.names + names)

    def subns(self, name: str) -> Namespace:
        assert len(name) > 0
        return Namespace(self.names + [name])

    def prev_ns(self) -> Namespace:
        assert len(self.names) > 0
        return Namespace(self.names[:-1])

    def get_name(self) -> str:
        assert len(self.names) > 0
        return self.names[-1]


class NamespaceManager:
    homepath: str
    paths: list[str]
    names2path: dict[str, str]
    ns2path: dict[Namespace, str]
    mock_root: Path
    mock_libs: bool
    prefer_mock_libs: bool

    def build(
        self,
        homepath: str,
        paths: list[str] | None,
        mock_libs: bool = True,
        prefer_mock_libs: bool = False,
    ) -> None:
        self.homepath = homepath
        self.paths = [homepath] + (paths or [])
        self.names2path = {}
        self.ns2path = {}
        self.mock_libs = mock_libs
        self.prefer_mock_libs = prefer_mock_libs
        self.mock_root = Path(__file__).resolve().parents[2] / "stubs" / "stdlib"

    # path to namespace
    def get_module(self, filepath: str) -> Namespace:
        full_path = filepath
        if not os.path.isabs(filepath):
            for path in self.paths:
                cur_path = os.path.join(path, filepath)
                if (is_src_file(cur_path) and os.path.isfile(cur_path)) or os.path.isdir(cur_path):
                    full_path = cur_path
                    break
        return Namespace.from_str(full_path)

    def get_ns2path(self, ns: Namespace) -> str:
        return self.names2path[ns.to_str()]

    def set_entry_module(self, module_path: str, root_path: str) -> Namespace:
        module_path = str(Path(module_path).resolve())
        try:
            rel_module_path = str(Path(module_path).relative_to(Path(root_path).resolve()))
        except ValueError:
            rel_module_path = module_path
        ns = Namespace.from_path(rel_module_path)
        self.ns2path[ns] = module_path
        self.names2path[ns.to_str()] = module_path
        return ns

    def names_from_import(self, ir: IRImport) -> None: ...

    def _cache_ns_path(self, ns: Namespace, path: str) -> str:
        self.ns2path[ns] = path
        self.names2path[ns.to_str()] = path
        return path

    def find_ns_in_path(self, paths: list[str], ns: Namespace) -> str | None:
        for path in paths:
            # FileFinder checks a package initializer before an equal-named
            # module in the same search directory.
            if os.path.isfile(ns.to_dirpath(rootpath=path)):
                mod_path = ns.to_dirpath(path)
                return self._cache_ns_path(ns, mod_path)
            if os.path.isfile(ns.to_filepath(rootpath=path)):
                mod_path = ns.to_filepath(path)
                return self._cache_ns_path(ns, mod_path)
        if ns.base() in builtin_module_names():
            return self._cache_ns_path(ns, f"__builtin__.{ns.base()}")
        return None

    def _find_mock_path(self, ns: Namespace) -> str | None:
        if not self.mock_libs:
            return None
        if not self.mock_root.exists():
            return None
        file_path = ns.to_filepath(rootpath=str(self.mock_root))
        if os.path.isfile(file_path):
            return file_path
        dir_path = ns.to_dirpath(rootpath=str(self.mock_root))
        if os.path.isfile(dir_path):
            return dir_path
        return None

    def _resolve_import_path(self, ns: Namespace) -> str | None:
        if ns.base() in builtin_module_names():
            return self.find_ns_in_path(self.paths, ns)
        if not self.mock_libs:
            return self.find_ns_in_path(self.paths, ns)
        if self.prefer_mock_libs:
            mock_path = self._find_mock_path(ns)
            if mock_path is not None:
                return self._cache_ns_path(ns, mock_path)
            return self.find_ns_in_path(self.paths, ns)
        mod_path = self.find_ns_in_path(self.paths, ns)
        if mod_path is not None:
            return mod_path
        mock_path = self._find_mock_path(ns)
        if mock_path is not None:
            return self._cache_ns_path(ns, mock_path)
        return None

    def resolve_import(self, name: str) -> tuple[Namespace, str] | None:
        """
        Resolve import from name to namespace and path.

        Args:
            name: Name of the module to import

        Returns:
            tuple[Namespace, str]: Namespace and path of the imported module
        """
        ns = Namespace.from_str(name)
        path = self._resolve_import_path(ns)
        if path is not None:
            return ns, path
        return None

    def resolve_importfrom(self, module: str, name: str) -> tuple[Namespace, str] | None:
        """
        Resolve import from module.name to name.

        Args:
            module: Module name
            name: Name of the item to import

        Returns:
            tuple[Namespace, str]: Namespace and path of the imported module
        """
        mod_ns = Namespace.from_str(module)
        succ_mod_ns = mod_ns.next_ns([name])
        succ_mod_path = self._resolve_import_path(succ_mod_ns)
        mod_path = self._resolve_import_path(mod_ns)
        if succ_mod_path is not None:
            return succ_mod_ns, succ_mod_path
        if mod_path is not None:
            return mod_ns, mod_path
        return None

    def resolve_rel_importfrom(
        self, cur_ns: Namespace, module: str | None, name: str, level: int
    ) -> tuple[Namespace, str] | None:
        """
        Resolve relative import from cur_ns to module.name with level.

        Args:
            cur_ns: Current namespace
            module: Module name
            name: Name of the item to import
            level: Level of the relative import

        Returns:
            tuple[Namespace, str]: Namespace and path of the imported module
        """
        package = (
            cur_ns.to_str()
            if self.ns2path[cur_ns].endswith("__init__.py")
            else ".".join(cur_ns.names[:-1])
        )
        if not package:
            raise ImportError("attempted relative import with no known parent package")
        absolute = resolve_name("." * level + (module or ""), package)
        return self.resolve_importfrom(absolute, name)

    def get_import(self, cur_ns: Namespace, ir: IRImport) -> tuple[Namespace, str] | None:
        """
        Get namespace and path of the imported module from cur_ns and ir.

        Args:
            cur_ns: Current namespace
            ir: Import statement

        Returns:
            tuple[Namespace, str]: Namespace and path of the imported module
        """
        statement: ast.AST = ir.stmt
        if isinstance(statement, ast.Import):
            ir._import_base_name = ir.name
            ret = self.resolve_import(ir.name)
            ir._resolved_import_name = ret[0].to_str() if ret is not None else None
            return ret
        if isinstance(statement, ast.ImportFrom):
            if ir.level == 0:
                ir._import_base_name = ir.module
                if ir.module is None:
                    raise AttributeError("'NoneType' object has no attribute 'split'")
                ret = self.resolve_importfrom(ir.module, ir.name)
                # assert ret is not None, f"Failed to resolve import: {ir.module}, {ir.name}"
                ir._resolved_import_name = ret[0].to_str() if ret is not None else None
                return ret
            package = (
                cur_ns.to_str()
                if self.ns2path[cur_ns].endswith("__init__.py")
                else ".".join(cur_ns.names[:-1])
            )
            ir._import_base_name = resolve_name("." * ir.level + (ir.module or ""), package)
            ret = self.resolve_rel_importfrom(cur_ns, ir.module, ir.name, ir.level)
            # assert ret is not None, f"Failed to resolve relative import: {ir.module}, {ir.name}, {ir.level}"
            ir._resolved_import_name = ret[0].to_str() if ret is not None else None
            return ret
        return None
