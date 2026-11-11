"""Project-solver ownership of transient statement-CFG representations.

Canonical IR, block CFG, source AST and scope/cell identities remain owned by
World. Only unambiguous statement CFGs are consumed early. ScopeManager's
historical qualified-name cache can alias distinct scopes, so ambiguous trees
remain on the legacy all-frontend-before-closure path.
"""
from __future__ import annotations

from collections import Counter


class FrontendLifetimeError(RuntimeError):
    """An ownership assumption failed; solving from retired data is unsafe."""


class ModuleClosureLifetime:
    def __init__(self, closure, inventory_module_names, report):
        self.closure = closure
        self.sm = closure.world.scope_manager
        self.module_names = frozenset(inventory_module_names)
        self.owners = {}
        self.completed = set()
        self.retired_names = set()
        self.report = report
        self.report.update(module_closure_modules=0, module_closure_scopes=0,
                           retired_statement_cfgs=0, deferred_modules=[],
                           final_closure_scopes=0, ownership_valid=True)

    def _fail(self, message):
        self.report["ownership_valid"] = False
        raise FrontendLifetimeError(message)

    def consume(self, module):
        scopes = self.closure.module_scopes(module)
        if self.completed.intersection(scopes):
            self._fail("module closure attempted to consume a retired scope twice: " + module.get_qualname())
        counts = Counter(scope.get_qualname() for scope in scopes)
        previous_owners = {name: self.owners.get(name) for name in counts}
        reasons = set()
        for scope in scopes:
            name = scope.get_qualname()
            previous = previous_owners[name]
            if previous is not None and previous is not scope:
                if name in self.retired_names:
                    self._fail("late qualified-name collision after CFG retirement: " + name)
                reasons.add("cross_tree_qualified_name_collision")
            if counts[name] > 1:
                reasons.add("duplicate_lexical_qualified_name")
            if scope is not module and name in self.module_names:
                reasons.add("lexical_name_matches_inventory_module")
            # Keep the original owner on collisions; never authorize eviction
            # through a newer scope that merely has the same qualified name.
            self.owners.setdefault(name, scope)
        if reasons:
            self.report["deferred_modules"].append({
                "module": module.get_qualname(), "reasons": sorted(reasons),
                "scope_count": len(scopes)})
            return False

        cfgs = {scope: self.sm.get_ir(scope, "cfg") for scope in scopes}
        self.closure.analyze_scopes(scopes)
        if set(self.closure.module_scopes(module)) != set(scopes):
            self._fail("lexical scope tree changed during closure: " + module.get_qualname())
        # Validate the entire deletion set first. A failed ownership check must
        # not silently evict a newer scope's cache entry or partially retire it.
        for scope, cfg in cfgs.items():
            name = scope.get_qualname()
            if self.owners.get(name) is not scope or self.sm.get_ir(scope, "cfg") is not cfg:
                self._fail("statement CFG ownership changed during closure: " + name)
        for scope, cfg in cfgs.items():
            if cfg is not None:
                if not self.sm.retire_ir(scope, "cfg", cfg):
                    self._fail("statement CFG retirement lost ownership: " + scope.get_qualname())
                self.report["retired_statement_cfgs"] += 1
            self.retired_names.add(scope.get_qualname())
        self.completed.update(scopes)
        self.report["module_closure_modules"] += 1
        self.report["module_closure_scopes"] += len(scopes)
        return True

    def finish(self):
        # One final World scan includes deferred and partially lowered trees.
        # No scan is performed per module; common-case traversal is linear in
        # the number of lexical scopes rather than modules times World size.
        remaining = self.sm.get_scopes() - self.completed
        for scope in self.completed:
            if any(child not in self.completed for child in self.sm.get_subscopes(scope)):
                self._fail("lexical child appeared after parent CFG retirement: " + scope.get_qualname())
        for scope in remaining:
            if scope.get_qualname() in self.retired_names:
                self._fail("late scope aliases a retired statement CFG: " + scope.get_qualname())
        self.closure.analyze_scopes(remaining)
        self.report["final_closure_scopes"] = len(remaining)
