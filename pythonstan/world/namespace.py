import ast
from typing import List, Dict, Tuple, Any
import os

from pythonstan.ir import IRImport
from pythonstan.utils.common import is_src_file, srcfile_to_name, builtin_module_names


class Namespace:
    names: List[str] # just keep this
    empty_ns = None

    def __init__(self, names):
        self.names = names

    def __str__(self):
        return '.'.join(self.names)

    # TODO resolve filename into full namespace in detail
    @classmethod
    def from_str(cls, filename: str) -> 'Namespace':
        names = filename.split('/')
        return cls(names)

    @classmethod
    def get_empty_ns(cls) -> 'Namespace':
        if cls.empty_ns is None:
            cls.empty_ns = Namespace([])
        return cls.empty_ns

    @classmethod
    def build(cls, names: List[str]) -> 'Namespace':
        if len(names) > 0:
            return cls(names)
        else:
            return cls.get_empty_ns()

    def is_empty(self) -> bool:
        return self == self.empty_ns

    def from_import(self, stmt: IRImport):
        ...

    def to_str(self):
        return '.'.join(self.names)

    def get_name(self):
        return self.names[-1]

    def get_item(self, name):
        return Namespace(self.names + [name])


class NamespaceManager:
    homepath: str
    paths: List[str]
    names2path: Dict[str, str]

    def __init__(self, homepath, paths: List[str]):
        self.homepath = homepath
        self.paths = [homepath] + paths

    # path to namespace
    def get_module(self, filepath: str) -> Namespace:
        full_path = filepath
        names = []
        if not os.path.isabs(filepath):
            for path in self.paths:
                cur_path = os.path.join(path, filepath)
                if (is_src_file(cur_path) and os.path.isfile(cur_path)) \
                        or os.path.isdir(cur_path):
                    full_path = cur_path
                    break
        else:
            names = filepath.split('/')
        ns = Namespace.from_str(full_path)
        return ns

    def ns2path(self, ns: Namespace) -> str:
        return self.names2path[ns.to_str()]

    def add_import(self, names: List[str]) -> Tuple[str, Namespace, List]:
        ns = Namespace.build(names)
        root_name = names[0]
        new_modules = []
        if root_name in builtin_module_names():
            mod_path = f"__builtin__.{root_name}"
            self.names2path[ns.to_str()] = f"__builtin__.{root_name}"
            return mod_path, Namespace.build(names[1:]), new_modules
        else:
            for path in self.paths:
                root_path = os.path.join(path, root_name)
                if os.path.isdir(root_path):
                    idx = 0
                    while os.path.isdir(root_path):
                        submodule_init = os.path.join(root_path, "__init__.py")
                        submodule_names = '.'.join(names[: idx + 1])
                        if submodule_names not in self.names2path and os.path.isfile(submodule_init):
                            self.names2path[submodule_names] = submodule_init
                            new_modules.append((submodule_names, submodule_init))
                        idx += 1
                        if idx == len(names):
                            break
                        root_path = os.path.join(root_path, names[idx])
                    if os.path.isfile(f"{root_path}.py"):
                        idx += 1
                        root_path = f"{root_path}.py"
                    elif os.path.isdir(root_path):
                        root_path = os.path.join(root_path, "__init__.py")
                    else:
                        root_path = root_path[: root_path.rfind("/")]
                        root_path = os.path.join(root_path, "__init__.py")
                    mod_ns = Namespace.build(names[: idx])
                    sub_ns = Namespace.build(names[idx:])
                    self.names2path[mod_ns.to_str()] = root_path
                    return root_path, sub_ns, new_modules
                elif os.path.isfile(f"{root_path}.py"):
                    mod_ns = Namespace.build(names[: 1])
                    sub_ns = Namespace.build(names[1:])
                    root_path = f"{root_path}.py"
                    self.names2path[mod_ns.to_str()] = root_path
                    return root_path, sub_ns, new_modules
        raise ValueError(f"Cannot find root module in <{names[0]}>")

    # import to namespace, and gen the filepath

    # return the path of module imported and rest namespace to be resolved, and the new modules
    def get_import(self, cur_ns: Namespace, ir: IRImport) -> Tuple[str, Namespace, Any]:
        if isinstance(ir.stmt, ast.Import):
            names = ir.name[0].split('.')
            root_name = names[0]
            if root_name in builtin_module_names():
                ns = Namespace.build([root_name])
                filepath = f"__builtin__.{root_name}"
                self.names2path[ns.to_str()] = filepath
                return filepath, Namespace.build(names[1:]), []
            else:
                return self.add_import(ir.name.split('.'))
        elif isinstance(ir.stmt, ast.ImportFrom):
            if ir.level == 0:
                root_path, sub_ns, new_modules = self.add_import(ir.module.split('.'))
                if sub_ns.is_empty() and root_path.endswith('/__init__.py'):
                    path = os.path.join(root_path.rstrip('/__init__.py'), ir.name)
                    if os.path.isdir(path):
                        root_path = os.path.join(path, "__init__.py")
                        sub_ns = Namespace.get_empty_ns()
                        return root_path, sub_ns, new_modules
                    if os.path.isdir(f"{path}.py"):
                        root_path = f"{path}.py"
                        sub_ns = Namespace.get_empty_ns()
                        return root_path, sub_ns, new_modules
                sub_ns = sub_ns.get_item(ir.name)
                return root_path, sub_ns, new_modules
            else:
                level = ir.level
                root_path = self.ns2path(cur_ns)
                cur_names = cur_ns.names
                while level > 0:
                    root_path = root_path[: root_path.rfind('/')]
                    cur_names = cur_names[: -1]
                    level -= 1
                names = ir.module.split('.')
                idx = 0
                new_modules = []
                while os.path.isdir(root_path):
                    submodule_init = os.path.join(root_path, "__init__.py")
                    cur_names.append(names[idx])
                    submodule_names = '.'.join(cur_names)
                    if submodule_names not in self.names2path:
                        self.names2path[submodule_names] = submodule_init
                        new_modules.append((submodule_names, submodule_init))
                    idx += 1
                    if idx == len(names):
                        break
                    root_path = os.path.join(root_path, names[idx])
                # ...

        else:
            raise NotImplementedError("No such import stmt")

