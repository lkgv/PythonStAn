"""Module resolution for pointer analysis.

This module handles import resolution and module loading for the analysis.
Integrates with pythonstan.world infrastructure for module management.
"""

from typing import Optional, TYPE_CHECKING, Tuple, List
import logging

if TYPE_CHECKING:
    from .config import Config
    from pythonstan.ir import IRModule, IRScope

logger = logging.getLogger(__name__)

__all__ = ["ModuleFinder"]


class ModuleFinder:
    """Resolves and loads modules for analysis.
    
    Integrates with pythonstan.world.World for module management.
    """
    
    def __init__(self, config: 'Config'):
        self.config = config
        self._module_cache = {}
        self._use_world = self._try_setup_world()
    
    def _try_setup_world(self) -> bool:
        try:
            from pythonstan.world import World
            
            if hasattr(World(), 'scope_manager'):
                logger.debug("Using existing World infrastructure")
                return True
            
            return False
            
        except (ImportError, AttributeError) as e:
            logger.debug(f"World infrastructure not available: {e}")
            return False
    
    def get_module_ir(self, module_name: str) -> Optional['IRModule']:
        """Retrieve module IR using World infrastructure.
        
        Uses scope_manager to get module that has been processed by Pipeline.
        If module is not in World but is a project module, loads it.
        Returns module with IR/TAC already attached by Pipeline transformations.
        """
        if module_name in self._module_cache:
            return self._module_cache[module_name]
        
        if not self._use_world:
            return None
        
        try:
            from pythonstan.world import World
            from pythonstan.world.namespace import Namespace
            
            # First try to get from World
            result = World().namespace_manager.resolve_import(module_name)
            if result:
                ns, module_path = result
                qualname = ns.to_str()
                module = World().scope_manager.get_module(qualname)
                
                if module:
                    self._module_cache[module_name] = module
                    logger.debug(f"Retrieved module IR: {module_name}")
                    return module
            
            # If not in World, check if it's a project module and load it
            if self.config.project_path:
                loaded_module = self._load_project_module(module_name)
                if loaded_module:
                    self._module_cache[module_name] = loaded_module
                    return loaded_module
                    
        except Exception as e:
            logger.debug(f"Failed to get module IR for {module_name}: {e}")
        
        return None
    
    def _load_project_module(self, module_name: str) -> Optional['IRModule']:
        """Load a module from the project directory if it exists."""
        if not self.config.project_path:
            return None
        
        try:
            from pathlib import Path
            from pythonstan.ir.ir_statements import IRModule
            from pythonstan.world.pipeline import Pipeline
            import ast
            
            project_path = Path(self.config.project_path)
            
            # Convert module name to file path
            # e.g., "flask.app" -> "flask/app.py"
            parts = module_name.split('.')
            
            # Find the module file
            module_file = None
            
            # Try direct path from project root
            package_path = project_path / '/'.join(parts) / '__init__.py'
            if package_path.exists():
                module_file = package_path
            else:
                module_py = project_path / '/'.join(parts[:-1]) / f"{parts[-1]}.py"
                if module_py.exists():
                    module_file = module_py
            
            # If not found and project_path contains a package, try inside it
            if not module_file:
                for item in project_path.iterdir():
                    if item.is_dir() and (item / '__init__.py').exists():
                        # This might be the package
                        # Try to find module inside
                        # For "flask.app", try "flask/app.py"
                        if parts[0] == item.name:
                            # Module name starts with package name
                            rest_parts = parts[1:]
                            if rest_parts:
                                test_file = item / '/'.join(rest_parts[:-1]) / f"{rest_parts[-1]}.py"
                                if test_file.exists():
                                    module_file = test_file
                                    break
            
            if not module_file or not module_file.exists():
                return None
            
            logger.debug(f"Loading project module {module_name} from {module_file}")
            
            # Load through Pipeline to get processed IR
            config = {
                'filename': str(module_file),
                'project_path': self.config.project_path,
                'library_paths': self.config.library_paths or [],
                'analysis': []
            }
            
            ppl = Pipeline(config=config)
            ppl.run()
            
            # Get the loaded module from World
            from pythonstan.world import World
            result = World().namespace_manager.resolve_import(module_name)
            if result:
                ns, _ = result
                qualname = ns.to_str()
                module = World().scope_manager.get_module(qualname)
                if module:
                    logger.debug(f"Successfully loaded project module: {module_name}")
                    return module
            
        except Exception as e:
            logger.debug(f"Failed to load project module {module_name}: {e}")
        
        return None
    
    def resolve_import_from(self, module_name: str, item_name: str) -> Optional[Tuple[str, str]]:
        """Resolve 'from module import item' using namespace_manager.
        
        Returns (resolved_namespace, module_path) or None
        """
        if not self._use_world:
            return None
        
        try:
            from pythonstan.world import World
            
            result = World().namespace_manager.resolve_importfrom(module_name, item_name)
            if result:
                ns, path = result
                logger.debug(f"Resolved 'from {module_name} import {item_name}' -> {ns.to_str()}")
                return (ns.to_str(), path)
                
        except Exception as e:
            logger.debug(f"Failed to resolve 'from {module_name} import {item_name}': {e}")
        
        return None
    
    def resolve_relative_import(self, current_ns: str, module_name: str, level: int) -> Optional[str]:
        """Resolve relative import using namespace_manager.
        
        Returns resolved module namespace string or None
        """
        # Try World's namespace_manager first (may not work for all cases)
        if self._use_world:
            try:
                from pythonstan.world import World
                from pythonstan.world.namespace import Namespace
                
                cur_ns = Namespace.from_str(current_ns)
                result = World().namespace_manager.resolve_rel_importfrom(
                    cur_ns, module_name, '', level
                )
                if result:
                    if isinstance(result, tuple):
                        ns, path = result
                    else:
                        ns = result
                    
                    if hasattr(ns, 'to_str'):
                        resolved = ns.to_str()
                    elif isinstance(ns, str):
                        resolved = ns
                    else:
                        resolved = str(ns)
                        
                    logger.debug(f"Resolved relative import level={level} from {current_ns}: {resolved}")
                    return resolved
                    
            except (KeyError, AttributeError) as e:
                # World's namespace_manager may fail for relative imports
                # Fall through to manual resolution
                pass
        
        # Manual resolution for simple relative imports (Flask pattern)
        # e.g., from __main__ with level=1, module='app' -> 'flask.app'
        if level == 1 and current_ns:
            # For __main__, try to infer package name from project_path
            if '__main__' in current_ns and self.config.project_path:
                from pathlib import Path
                project_path = Path(self.config.project_path)
                
                # project_path might be .../flask/src, we want 'flask'
                # Look for a Python package directory
                package_name = project_path.name
                if package_name in ('src', 'lib'):
                    # Check if there's a package inside src
                    for item in project_path.iterdir():
                        if item.is_dir() and (item / '__init__.py').exists():
                            package_name = item.name
                            break
                
                if module_name:
                    resolved = f"{package_name}.{module_name}"
                else:
                    resolved = package_name
                
                logger.debug(f"Manually resolved relative import level={level} from {current_ns}: {resolved}")
                return resolved
        
        return None
    
    def get_scope_ir(self, scope: 'IRScope') -> List:
        """Get IR/TAC for scope using scope_manager."""
        if not self._use_world:
            return []
        
        try:
            from pythonstan.world import World
            ir = World().scope_manager.get_ir(scope, 'ir')
            return ir if ir is not None else []
        except Exception as e:
            logger.debug(f"Failed to get scope IR: {e}")
            return []
    
    def get_subscopes(self, scope: 'IRScope') -> List['IRScope']:
        """Get subscopes (functions, classes) for scope using scope_manager."""
        if not self._use_world:
            return []
        
        try:
            from pythonstan.world import World
            subscopes = World().scope_manager.get_subscopes(scope)
            return subscopes if subscopes is not None else []
        except Exception as e:
            logger.debug(f"Failed to get subscopes: {e}")
            return []
    
    def resolve_import(self, module_name: str) -> Optional[str]:
        """Resolve module import to module path (legacy compatibility)."""
        if not self._use_world:
            return None
        
        try:
            from pythonstan.world import World
            result = World().namespace_manager.resolve_import(module_name)
            if result:
                _, path = result
                logger.debug(f"Resolved {module_name} -> {path}")
                return path
        except Exception as e:
            logger.debug(f"Failed to resolve {module_name}: {e}")
        
        return None
    
    def has_module(self, module_name: str) -> bool:
        """Check if module can be resolved."""
        return self.resolve_import(module_name) is not None
