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
        Returns module with IR/TAC already attached by Pipeline transformations.
        """
        if module_name in self._module_cache:
            return self._module_cache[module_name]
        
        if not self._use_world:
            return None
        
        try:
            from pythonstan.world import World
            from pythonstan.world.namespace import Namespace
            
            result = World().namespace_manager.resolve_import(module_name)
            if result:
                ns, module_path = result
                qualname = ns.to_str()
                module = World().scope_manager.get_module(qualname)
                
                if module:
                    self._module_cache[module_name] = module
                    logger.debug(f"Retrieved module IR: {module_name}")
                    return module
                    
        except Exception as e:
            logger.debug(f"Failed to get module IR for {module_name}: {e}")
        
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
        if not self._use_world:
            return None
        
        try:
            from pythonstan.world import World
            from pythonstan.world.namespace import Namespace
            
            cur_ns = Namespace.from_str(current_ns)
            result = World().namespace_manager.resolve_rel_importfrom(
                cur_ns, module_name, '', level
            )
            if result:
                ns, path = result
                logger.debug(f"Resolved relative import level={level} from {current_ns}: {ns.to_str()}")
                return ns.to_str()
                
        except Exception as e:
            logger.debug(f"Failed to resolve relative import: {e}")
        
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
