import ast
from typing import List, Dict, Tuple, Any, Optional
import os

from pythonstan.ir import IRImport
from pythonstan.utils.common import is_src_file, srcfile_to_name, builtin_module_names


def get_root(path: str, names: List[str]) -> str:
    prev_num = len(names)
    root_names = path.rstrip('/__init__.py').strip('.py').split('/')
    root_path = '/'.join(root_names[: -prev_num])
    if len(root_path) == 0 and path.startswith('/'):
        root_path = '/'
    return root_path


class Namespace:
    names: List[str]
    empty_ns = None

    ns_dict: Dict[str, 'Namespace'] = {}

    def __init__(self, names):
        assert len(names) > 0, "Cannot  construct empty namespace"
        self.names = names
        self.ns_dict['.'.join(names)] = self

    def __str__(self):
        return '.'.join(self.names)

    @classmethod
    def build(cls, names: List[str]) -> 'Namespace':
        name_str = '.'.join(names)
        if name_str in cls.ns_dict:
            return cls.ns_dict[name_str]
        else:
            return cls(names)

    @classmethod
    def from_str(cls, name_str: str) -> 'Namespace':
        return cls.build(name_str.split('.'))

    @classmethod
    def from_path(cls, filename: str) -> 'Namespace':
        s = filename.rstrip('/')
        if s.endswith('__init__.py'):
            s = s.rstrip('/__init__.py')
        if s.endswith('.py'):
            s = s.rstrip('.py')
        return cls(s.split('/'))

    def to_str(self):
        return '.'.join(self.names)
    
    def __str__(self):
        return self.to_str()
    
    def __repr__(self) -> str:
        return self.to_str()

    def to_filepath(self, rootpath: Optional[str] = None) -> str:
        path = f'{"/".join(self.names)}.py'
        if rootpath is not None:
            path = os.path.join(rootpath, path)
        return path

    def to_dirpath(self, rootpath: Optional[str] = None) -> str:
        path =  f'{"/".join(self.names)}/__init__.py'
        if rootpath is not None:
            path = os.path.join(rootpath, path)
        return path

    def base(self):
        return self.names[0]

    def relative_ns(self, names: List[str], level: int) -> 'Namespace':
        assert level >= 0
        assert len(self.names) >= level
        
        # TODO names=[''] is a special case, need to be clarified in the future
        if len(names) == 1 and names[0] == '':
            return self
        
        if level > 0:
            return self.build(self.names[: -level] + names)
        else:
            return self.build(self.names + names)

    def next_ns(self, names: List[str]) -> 'Namespace':
        return self.build(self.names + names)

    def subns(self, name):
        assert len(name) > 0
        return Namespace(self.names + [name])

    def prev_ns(self) -> 'Namespace':
        assert len(self.names) > 0
        return Namespace(self.names[:-1])

    def get_name(self) -> str:
        assert len(self.names) > 0
        return self.names[-1]


class NamespaceManager:
    homepath: str
    paths: List[str]
    names2path: Dict[str, str]
    ns2path: Dict[Namespace, str]

    def build(self, homepath, paths: List[str]):
        self.homepath = homepath
        self.paths = [homepath] + paths
        self.names2path = {}
        self.ns2path = {}

    # path to namespace
    def get_module(self, filepath: str) -> Namespace:
        full_path = filepath
        if not os.path.isabs(filepath):
            for path in self.paths:
                cur_path = os.path.join(path, filepath)
                if (is_src_file(cur_path) and os.path.isfile(cur_path)) \
                        or os.path.isdir(cur_path):
                    full_path = cur_path
                    break
        ns = Namespace.from_str(full_path)
        return ns

    def get_ns2path(self, ns: Namespace) -> str:
        return self.names2path[ns.to_str()]
    
    def set_entry_module(self, module_path: str, root_path: str) -> Namespace:
        if root_path.endswith("/"):
            root_path = root_path[:-1]
        if module_path.startswith(root_path):
            rel_module_path = module_path[len(root_path) + 1:]
        else:
            rel_module_path = module_path
        ns = Namespace.from_path(rel_module_path)
        self.ns2path[ns] = module_path
        self.names2path[ns.to_str()] = module_path
        return ns

    def names_from_import(self, ir: IRImport) -> List[str]:
        ...

    def find_ns_in_path(self, paths: List[str], ns: Namespace) -> Optional[str]:
        for path in paths:
            if os.path.isfile(ns.to_filepath(rootpath=path)):
                mod_path = ns.to_filepath(path)
                self.ns2path[ns] = mod_path
                break
            elif os.path.isfile(ns.to_dirpath(rootpath=path)):
                mod_path = ns.to_dirpath(path)
                self.ns2path[ns] = mod_path
                break
        else:
            if ns.base() in builtin_module_names():
                mod_path = f"__builtin__.{ns.base()}"
                self.ns2path[ns] = mod_path
            else:
                return None
        return mod_path

    def resolve_import(self, name: str) -> Optional[Tuple[Namespace, str]]:
        """
        Resolve import from name to namespace and path.
        
        Args:
            name: Name of the module to import
        
        Returns:
            Tuple[Namespace, str]: Namespace and path of the imported module
        """
        ns = Namespace.from_str(name)
        path = self.find_ns_in_path(self.paths, ns)
        if path is not None:
            return ns, path
        return None

    def resolve_importfrom(self, module: str, name: str) -> Optional[Tuple[Namespace, str]]:
        """
        Resolve import from module.name to name.
        
        Args:
            module: Module name
            name: Name of the item to import
        
        Returns:
            Tuple[Namespace, str]: Namespace and path of the imported module
        """
        mod_ns = Namespace.from_str(module)
        succ_mod_ns = mod_ns.next_ns([name])
        succ_mod_path = self.find_ns_in_path(self.paths, succ_mod_ns)
        mod_path = self.find_ns_in_path(self.paths, mod_ns)
        if succ_mod_path is not None:
            self.ns2path[succ_mod_ns] = succ_mod_path
            return succ_mod_ns, succ_mod_path
        elif mod_path is not None:
            self.ns2path[mod_ns] = mod_path
            return mod_ns, mod_path
        return None

    def resolve_rel_importfrom(self, cur_ns: Namespace, module: str, name: str, level: int) -> Optional[Tuple[Namespace, str]]:
        """
        Resolve relative import from cur_ns to module.name with level.
        
        Args:
            cur_ns: Current namespace
            module: Module name
            name: Name of the item to import
            level: Level of the relative import
        
        Returns:
            Tuple[Namespace, str]: Namespace and path of the imported module
        """
        if self.ns2path[cur_ns].endswith('__init__.py'):
            level -= 1
        rel_ns = cur_ns.relative_ns(module.split('.'), level)
        root_path = get_root(self.ns2path[cur_ns], cur_ns.names)
        if os.path.isfile(rel_ns.to_filepath(root_path)):
            rel_ns_path = rel_ns.to_filepath(root_path)
            self.ns2path[rel_ns] = rel_ns_path
            return rel_ns, rel_ns_path
        
        elif os.path.isfile(rel_ns.to_dirpath(root_path)):
            succ_rel_ns = rel_ns.next_ns([name])
            
            if os.path.isfile(succ_rel_ns.to_filepath(root_path)):
                succ_rel_path = succ_rel_ns.to_filepath(root_path)
                self.ns2path[succ_rel_ns] = succ_rel_path
                return succ_rel_ns, succ_rel_path
            
            elif os.path.isfile(succ_rel_ns.to_dirpath(root_path)):
                succ_rel_path = succ_rel_ns.to_dirpath(root_path)
                self.ns2path[succ_rel_ns] = succ_rel_path
                return succ_rel_ns, succ_rel_path
            
            else:
                rel_path = rel_ns.to_dirpath(root_path)
                self.ns2path[rel_ns] = rel_path
                return rel_ns, rel_path
            
        return None

    def get_import(self, cur_ns: Namespace, ir: IRImport) -> Optional[Tuple[Namespace, str]]:
        """
        Get namespace and path of the imported module from cur_ns and ir.
        
        Args:
            cur_ns: Current namespace
            ir: Import statement
        
        Returns:
            Tuple[Namespace, str]: Namespace and path of the imported module
        """
        if isinstance(ir.stmt, ast.Import):
            return self.resolve_import(ir.name)
        elif isinstance(ir.stmt, ast.ImportFrom):
            if ir.level == 0:
                ret = self.resolve_importfrom(ir.module, ir.name)
                # assert ret is not None, f"Failed to resolve import: {ir.module}, {ir.name}"
                return ret
            else:
                ret = self.resolve_rel_importfrom(cur_ns, ir.module, ir.name, ir.level)
                # assert ret is not None, f"Failed to resolve relative import: {ir.module}, {ir.name}, {ir.level}"
                return ret
        return None

