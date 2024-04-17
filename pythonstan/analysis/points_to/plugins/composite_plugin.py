from .plugin import Plugin
from typing import List


class CompositePlugin(Plugin):
    plugins: List[Plugin]

    def __init__(self):
        self.plugins = []

    def add_plugin(self, plugin: Plugin):
        self.plugins.append(plugin)

    def get_plugins(self) -> List[Plugin]:
        return self.plugins

    def on_start(self):
        for plugin in self.plugins:
            plugin.on_start()

    def on_finish(self):
        for plugin in self.plugins:
            plugin.on_finish()

    def on_new_pts(self, cs_var, pts):
        for plugin in self.plugins:
            plugin.on_new_pts(cs_var, pts)

    def on_new_call_edge(self, edge):
        for plugin in self.plugins:
            plugin.on_new_call_edge(edge)

    def on_new_scope(self, scope):
        for plugin in self.plugins:
            plugin.on_new_scope(scope)

    def on_new_stmt(self, stmt, container):
        for plugin in self.plugins:
            plugin.on_new_stmt(stmt, container)

    def on_new_cs_scope(self, cs_scope):
        for plugin in self.plugins:
            plugin.on_new_cs_scope(cs_scope)

    def on_unresolved_call(self, recv, context, invoke):
        for plugin in self.plugins:
            plugin.on_unresolved_call(recv, context, invoke)
