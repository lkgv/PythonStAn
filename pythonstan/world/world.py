from typing import TYPE_CHECKING

from pythonstan.ir import IRModule
from pythonstan.utils.common import Singleton

if TYPE_CHECKING:
    from .analysis_manager import AnalysisManager
    from .class_hierarchy import ClassHierarchy
    from .config import Config
    from .import_manager import ImportManager
    from .namespace import Namespace, NamespaceManager
    from .scope_manager import ScopeManager


class World(Singleton):
    scope_manager: "ScopeManager"
    analysis_manager: "AnalysisManager"
    namespace_manager: "NamespaceManager"
    entry_module: IRModule | None
    class_hierarchy: "ClassHierarchy"
    import_manager: "ImportManager"
    module2ns: dict[IRModule, "Namespace"]

    @classmethod
    def setup(cls) -> None:
        from .class_hierarchy import ClassHierarchy
        from .import_manager import ImportManager
        from .namespace import NamespaceManager
        from .scope_manager import ScopeManager

        # Services are read through the singleton instance. Resetting only the
        # class attributes leaves any instance-level replacement in force and
        # can import an earlier program's modules into a fresh analysis.
        world = cls()
        world.scope_manager = ScopeManager()
        world.namespace_manager = NamespaceManager()
        world.class_hierarchy = ClassHierarchy()
        world.import_manager = ImportManager()
        world.module2ns = {}
        world.entry_module = None

    def build(self, config: "Config") -> None:
        self.scope_manager.build()
        self.import_manager.build()
        self.namespace_manager.build(
            config.project_path,
            config.library_paths,
            mock_libs=config.mock_libs,
            prefer_mock_libs=config.prefer_mock_libs,
        )

    def set_entry_module(self, module: IRModule) -> None:
        self.entry_module = module

    def get_entry_module(self) -> IRModule | None:
        return self.entry_module
