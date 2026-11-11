"""Conservative framework entry discovery with explicit external assumptions.

These records are entry assumptions, never solver-derived call edges or a proof
that a registration is reachable. Imported names and receiver constructors must
resolve to declared external framework identities. Unknown wrappers, factories,
registrations and callback arguments remain residual. Target code is not run.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import importlib.util
from pathlib import Path
from typing import Mapping


_ROOTS = frozenset({"flask", "fastapi", "click", "pluggy"})
_CONSTRUCTORS = {"flask.Flask", "flask.Blueprint", "fastapi.FastAPI", "fastapi.APIRouter"}
_WEB_METHODS = {"route", "get", "post", "put", "patch", "delete", "options", "head", "websocket"}
_FLASK_LIFECYCLE = {"before_request", "after_request", "teardown_request", "teardown_appcontext",
                    "before_app_request", "after_app_request", "teardown_app_request", "errorhandler", "app_errorhandler"}
_CLICK_DECORATORS = {"click.command", "click.group"}
_CLICK_WRAPPERS = {"click.option", "click.argument", "click.pass_context", "click.pass_obj"}
_REGISTRATION_METHODS = _WEB_METHODS | _FLASK_LIFECYCLE | {"command", "group", "add_url_rule",
    "register_blueprint", "include_router", "add_api_route", "add_route", "register", "add_command"}


@dataclass(frozen=True)
class _Binding:
    kind: str
    path: str
    identity: str | None = None


class _Assigned(ast.NodeVisitor):
    def __init__(self):
        self.names = set()

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_FunctionDef(self, node):
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Import(self, node):
        self.names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)

    def visit_ImportFrom(self, node):
        self.names.update(alias.asname or alias.name for alias in node.names)


class _Scanner:
    def __init__(self, sources, versions, exports, *, collect, invalidated=None, package_modules=()):
        self.sources, self.versions, self.exports = sources, versions, exports
        self.collect = collect
        self.entries, self.residuals = [], []
        self.identity_contracts = []
        self.module = ""
        self.digest = ""
        self.function_nodes = {}
        self.invalidated = set() if invalidated is None else invalidated
        self.package_modules = frozenset(package_modules)

    @staticmethod
    def binding_key(binding):
        return binding.identity or (binding.kind, binding.path)

    def valid(self, binding):
        return binding if binding is None or self.binding_key(binding) not in self.invalidated else None

    def invalidate_target(self, target, env):
        base = target
        while isinstance(base, (ast.Attribute, ast.Subscript)):
            base = base.value
        if isinstance(base, ast.Name):
            victim = self.resolve(target.value, env) if isinstance(target, (ast.Attribute, ast.Subscript)) else env.get(base.id)
            if victim is not None and isinstance(target, (ast.Attribute, ast.Subscript)):
                self.invalidated.add(self.binding_key(victim))
                self.residual(target, "framework_binding_mutated_or_deleted", binding=victim.path)
                for name, value in env.items():
                    if value is not None and self.binding_key(value) == self.binding_key(victim):
                        env[name] = None
            else:
                env[base.id] = None

    def residual(self, node, reason, **details):
        if self.collect:
            self.residuals.append({"module": self.module, "line": getattr(node, "lineno", None),
                "source_sha256": self.digest, "reason": reason,
                "effects": ["may_invoke_callbacks", "unmodeled_external_arguments"], **details})

    def imported(self, module, member=None):
        path = module + ("." + member if member else "")
        root = path.split(".")[0]
        for prefix in (module, module.split(".")[0]):
            if ("project", prefix) in self.invalidated or ("external", prefix) in self.invalidated:
                return None
        if any(name == root or name.startswith(root + ".") for name in self.sources):
            if member is not None and member in self.exports.get(module, {}):
                return self.exports[module][member]
            return _Binding("project", path) if path in self.sources else None
        if module in self.sources:
            return self.exports.get(module, {}).get(member) if member else _Binding("project", module)
        if root in self.versions or root == "functools":
            return _Binding("external", path)
        return None

    def resolve(self, expression, env):
        if isinstance(expression, ast.Name):
            return self.valid(env.get(expression.id))
        if isinstance(expression, ast.Attribute):
            receiver = self.resolve(expression.value, env)
            if receiver is not None:
                if receiver.kind == "project":
                    return self.valid(self.imported(receiver.path, expression.attr))
                if receiver.kind == "external":
                    return _Binding("external", receiver.path + "." + expression.attr)
                return _Binding("method", receiver.path + "." + expression.attr, receiver.identity)
        if isinstance(expression, ast.Call):
            callee = self.resolve(expression.func, env)
            if callee is not None:
                if callee.kind == "external" and callee.path in _CONSTRUCTORS:
                    return self.valid(_Binding("receiver", callee.path, f"{self.module}:{expression.lineno}:{expression.col_offset}"))
                if callee.kind == "external" and callee.path == "pluggy.HookimplMarker":
                    return _Binding("marker", callee.path)
        return None

    def classify(self, decorator, env):
        expression = decorator.func if isinstance(decorator, ast.Call) else decorator
        binding = self.resolve(expression, env)
        if binding is None:
            return None
        if binding.kind == "external" and binding.path in _CLICK_DECORATORS:
            return "click", "cli_command", binding.path
        if binding.kind == "marker" and binding.path == "pluggy.HookimplMarker":
            return "pluggy", "hook_implementation", binding.path
        if binding.kind == "method":
            owner, method = binding.path.rsplit(".", 1)
            root = owner.split(".")[0]
            if owner in _CONSTRUCTORS and method in _WEB_METHODS:
                return root, "web_handler", binding.path
            if owner in {"flask.Flask", "flask.Blueprint"} and method in _FLASK_LIFECYCLE:
                return "flask", "lifecycle_callback", binding.path
            if owner == "flask.Flask.cli" and method in {"command", "group"}:
                return "flask", "cli_command", binding.path
            if owner == "click.group" and method in {"command", "group"}:
                return "click", "cli_command", binding.path
        return None

    def identity_contract(self, node, prefix, decorator, env, decorator_index):
        if not self.collect:
            return
        expression = decorator.func if isinstance(decorator, ast.Call) else decorator
        binding = self.resolve(expression, env)
        if binding is None:
            return
        classified = self.classify(decorator, env)
        valid = binding.path == "functools.wraps" and binding.kind == "external" and isinstance(decorator, ast.Call)
        if classified is not None and classified[0] == "flask":
            # Route and error-handler methods return a decorator; lifecycle
            # registration methods directly consume the decorated callable.
            method = binding.path.rsplit(".", 1)[-1]
            factory = method in _WEB_METHODS | {"errorhandler", "app_errorhandler"}
            valid = isinstance(decorator, ast.Call) if factory else not isinstance(decorator, ast.Call)
        if not valid:
            return
        self.identity_contracts.append({
            "module": self.module, "qualified_name": self.module + "." + prefix + node.name,
            "function_line": node.lineno, "decorator_line": decorator.lineno,
            "decorator_index": decorator_index,
            "decorator_end_line": getattr(decorator, "end_lineno", decorator.lineno),
            "source_sha256": self.digest, "binding": binding.path,
            "origin": "conditional_external_decorator_contract",
            "operation": "applied_decorator_preserves_input_callable_identity",
            "allowed_use": "add input-to-result may-flow for this exact decorator application",
            "solver_derived": False, "framework_version": self.versions.get("flask") if classified else "CPython 3.12 functools.wraps",
            "assumptions": ["The recorded external binding denotes the configured implementation.",
                "The decorator application returns normally; unknown exception and metadata effects remain residual.",
                "Identity applies to the input of this application, not across intervening unknown decorators."],
        })

    def entry(self, node, prefix, classification, decorators, env, conditional):
        if not self.collect:
            return
        framework, kind, binding = classification
        qualname = prefix + node.name
        unknown = []
        for decorator in decorators:
            if self.classify(decorator, env) is not None:
                continue
            value = self.resolve(decorator.func if isinstance(decorator, ast.Call) else decorator, env)
            if value is None or value.path not in _CLICK_WRAPPERS:
                unknown.append(ast.unparse(decorator))
        item = {"module": self.module, "qualname": qualname,
            "qualified_name": self.module + "." + qualname, "framework": framework, "kind": kind,
            "line": node.lineno, "source_sha256": self.digest, "binding": binding,
            "framework_version": self.versions.get(framework, "unversioned"),
            "binding_identity_basis": "lexical imports, aliases and admitted constructor expressions",
            "origin": "conditional_framework_entry", "solver_derived": False,
            "parameter_coverage": "unmodeled_unknown_external_arguments",
            "conditional_declaration": conditional, "unknown_decorators": unknown,
            "assumptions": ["The declared external framework import resolves to the configured package identity.",
                "Reflective rebinding and mutations outside the modeled lexical assignments do not replace the recognized bindings.",
                "This declaration is reached and its containing factory/registration is activated.",
                "The framework may invoke the registered callback; external argument values are not modeled."],
            "callback_identity_verified": not unknown}
        if unknown:
            item["assumptions"].append("Unresolved decorators preserve a may-path to the declared source callback.")
            self.residual(node, "unresolved_decorator_chain_may_replace_handler", decorators=unknown)
        self.entries.append(item)

    def imperative(self, node, env):
        """Admit only a resolved receiver and a named source function value."""
        binding = self.resolve(node.func, env)
        if binding is None or binding.kind != "method":
            return False
        owner, method = binding.path.rsplit(".", 1)
        callback = None
        kind = "web_handler"
        if owner in {"flask.Flask", "flask.Blueprint"} and method in _FLASK_LIFECYCLE:
            if method not in {"errorhandler", "app_errorhandler"} and len(node.args) == 1:
                callback = node.args[0];kind = "lifecycle_callback"
        elif owner in {"flask.Flask", "flask.Blueprint"} and method == "add_url_rule":
            callback = next((item.value for item in node.keywords if item.arg == "view_func"), None)
            if callback is None and len(node.args) >= 3: callback = node.args[2]
        elif owner in {"fastapi.FastAPI", "fastapi.APIRouter"} and method in {"add_api_route", "add_route"}:
            callback = next((item.value for item in node.keywords if item.arg == "endpoint"), None)
            if callback is None and len(node.args) >= 2: callback = node.args[1]
        if callback is None:
            return False
        function = self.resolve(callback, env)
        if function is None or function.kind != "function" or function.path not in self.function_nodes:
            return False
        module, qualname, target = self.function_nodes[function.path]
        origin_module, origin_digest = self.module, self.digest
        self.module, self.digest = module, hashlib.sha256(self.sources[module].encode()).hexdigest()
        before = len(self.entries)
        try:
            self.entry(target, qualname[:-len(target.name)],
                (owner.split(".")[0], kind, binding.path), target.decorator_list, {}, True)
        finally:
            self.module, self.digest = origin_module, origin_digest
        if len(self.entries) > before:
            self.entries[-1].update(registration_module=origin_module,
                registration_line=node.lineno, registration_source_sha256=origin_digest,
                origin="conditional_imperative_framework_entry")
        return True

    def block(self, statements, env, prefix="", *, conditional=False, descend=True, function_closure_env=None):
        for node in statements:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    env[name] = self.imported(alias.name if alias.asname else alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                package = self.module if self.module in self.package_modules or any(n.startswith(self.module + ".") for n in self.sources) else self.module.rpartition(".")[0]
                try:
                    module = importlib.util.resolve_name("." * node.level + (node.module or ""), package) if node.level else node.module
                except (ImportError, ValueError):
                    module = None
                for alias in node.names:
                    if alias.name == "*":
                        for name in env: env[name] = None
                        self.residual(node, "wildcard_import_binding_unresolved")
                    else:
                        env[alias.asname or alias.name] = self.imported(module, alias.name) if module else None
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = self.resolve(node.value, env) if node.value is not None else None
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        env[target.id] = value
                    else:
                        self.invalidate_target(target, env)
                        assigned = _Assigned(); assigned.visit(target)
                        for name in assigned.names:
                            env[name] = None
            elif isinstance(node, ast.Delete):
                for target in node.targets: self.invalidate_target(target, env)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                classifications = [self.classify(decorator, env) for decorator in node.decorator_list]
                for decorator_index, decorator in enumerate(node.decorator_list):
                    self.identity_contract(node, prefix, decorator, env, decorator_index)
                for classification in classifications:
                    if classification is not None:
                        self.entry(node, prefix, classification, node.decorator_list, env, conditional)
                for decorator, classification in zip(node.decorator_list, classifications):
                    raw = decorator.func if isinstance(decorator, ast.Call) else decorator
                    if classification is None and isinstance(raw, ast.Attribute) and raw.attr in _REGISTRATION_METHODS:
                        self.residual(decorator, "framework_like_decorator_receiver_unresolved", expression=ast.unparse(raw))
                # Click groups are callable receiver objects for subcommands.
                group = any(c and c[0] == "click" and c[2].endswith("group") for c in classifications)
                env[node.name] = _Binding("receiver", "click.group") if group else _Binding("function", self.module + "." + prefix + node.name)
                if descend:
                    local = dict(function_closure_env if function_closure_env is not None else env)
                    assigned = _Assigned()
                    for statement in node.body: assigned.visit(statement)
                    parameters = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                    locals_ = assigned.names | {a.arg for a in parameters}
                    locals_.update(a.arg for a in (node.args.vararg, node.args.kwarg) if a is not None)
                    for name in locals_: local[name] = None
                    # IRTransformer uses lexical dotted scopes, without the
                    # CPython display-only <locals> segment.
                    self.block(node.body, local, prefix + node.name + ".", conditional=True)
            elif isinstance(node, ast.ClassDef):
                env[node.name] = None
                if descend:
                    self.block(node.body, dict(env), prefix + node.name + ".", conditional=True,
                               function_closure_env=dict(env))
            else:
                # Structured control may introduce or rebind names. Analyze
                # branches under unknown values, then invalidate every write.
                assigned = _Assigned(); assigned.visit(node)
                for name in assigned.names: env[name] = None
                for field in ("body", "orelse", "finalbody"):
                    children = getattr(node, field, ())
                    if children and isinstance(children, list):
                        self.block(children, dict(env), prefix, conditional=True, descend=descend)
                for handler in getattr(node, "handlers", ()):
                    local = dict(env)
                    if handler.name: local[handler.name] = None
                    self.block(handler.body, local, prefix, conditional=True, descend=descend)
                if self.collect:
                    # Bodies have already been visited with their own lexical
                    # environments. Do not re-read nested function parameters
                    # using this outer environment via an unrestricted walk.
                    roots = []
                    for field, value in ast.iter_fields(node):
                        if field in {"body", "orelse", "finalbody", "handlers"}: continue
                        if isinstance(value, ast.AST): roots.append(value)
                        elif isinstance(value, list): roots.extend(v for v in value if isinstance(v, ast.AST))
                    for child in (child for root in roots for child in ast.walk(root)):
                        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr in _REGISTRATION_METHODS:
                            if not self.imperative(child, env):
                                self.residual(child, "imperative_registration_requires_framework_transfer", expression=ast.unparse(child.func))
        return env


def discover_framework_entries(sources: Mapping[str, str | Path], *, external_frameworks: Mapping[str, str] | None = None):
    """Read every source and derive conditional declarations plus open obligations.

    ``sources`` maps canonical module identities to complete source text or file
    paths. ``external_frameworks`` names permitted external package identities
    and versions. The default admits four known roots under an explicit
    unversioned boundary assumption; it never treats a first-party shadow module
    as that dependency. No returned record is a solver call edge.
    """
    if not isinstance(sources, Mapping) or any(type(name) is not str or not name for name in sources):
        raise ValueError("sources must map canonical module names to complete source")
    versions = dict(external_frameworks) if external_frameworks is not None else {root: "unversioned" for root in _ROOTS}
    if any(root not in _ROOTS or type(version) is not str for root, version in versions.items()):
        raise ValueError("external framework identities must name supported roots and string versions")
    texts, trees, errors = {}, {}, []
    package_modules = {name for name, value in sources.items() if isinstance(value, Path) and value.name == "__init__.py"}
    for module, value in sorted(sources.items()):
        try:
            if isinstance(value, Path): value = value.read_text()
            if type(value) is not str: raise ValueError("source must be exact text or Path")
            texts[module] = value
            trees[module] = ast.parse(value)
        except (SyntaxError, OSError, ValueError, UnicodeError) as exc:
            errors.append({"module": module, "reason": "source_unavailable", "diagnostic": str(exc)})
    exports = {module: {} for module in texts}
    invalidated = set()
    stable = False
    for _ in range(len(texts) + 2):
        new = {}
        for module, tree in trees.items():
            scan = _Scanner(texts, versions, exports, collect=False, invalidated=invalidated, package_modules=package_modules);scan.module = module
            new[module] = scan.block(tree.body, {}, descend=False)
        new = {module: {name: (binding if binding is None or _Scanner.binding_key(binding) not in invalidated else None)
                        for name, binding in values.items()} for module, values in new.items()}
        if new == exports:
            stable = True;break
        exports = new
    entries, residuals, contracts = [], list(errors), []
    digests = {}
    # Any discovered source function can be activated under the external-entry
    # assumptions. Collect lexical attribute mutations before emitting entries,
    # so a later module/body cannot leave an earlier alias spuriously trusted.
    for _ in range(len(texts) + 2):
        before = set(invalidated)
        for module, tree in trees.items():
            scan = _Scanner(texts, versions, exports, collect=False, invalidated=invalidated, package_modules=package_modules)
            scan.module = module
            scan.block(tree.body, {})
        if invalidated == before: break
    function_nodes = {}
    def collect_functions(module, statements, prefix=""):
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = prefix + node.name
                function_nodes[module + "." + qualname] = (module, qualname, node)
                collect_functions(module, node.body, qualname + ".")
            elif isinstance(node, ast.ClassDef):
                collect_functions(module, node.body, prefix + node.name + ".")
            else:
                for field in ("body", "orelse", "finalbody"):
                    children = getattr(node, field, ())
                    if isinstance(children, list): collect_functions(module, children, prefix)
                for handler in getattr(node, "handlers", ()):
                    collect_functions(module, handler.body, prefix)
    for module, tree in trees.items(): collect_functions(module, tree.body)
    for module, tree in trees.items():
        scan = _Scanner(texts, versions, exports, collect=True, invalidated=invalidated, package_modules=package_modules);scan.module = module
        scan.function_nodes = function_nodes
        scan.digest = digests[module] = hashlib.sha256(texts[module].encode()).hexdigest()
        scan.block(tree.body, {})
        entries.extend(scan.entries);residuals.extend(scan.residuals)
        contracts.extend(scan.identity_contracts)
    if not stable:
        residuals.append({"reason": "framework_binding_exports_not_stable", "effects": ["may_invoke_callbacks"]})
    return {"schema_version": 1, "entries": entries, "residuals": residuals,
        "identity_decorator_contracts": contracts,
        "source_sha256": digests, "binding_exports_stable": stable, "source_modules": len(sources),
        "parsed_modules": len(trees), "external_frameworks": versions,
        "origin": "conditional_source_framework_declarations", "solver_edges_emitted": 0,
        "complete_registration_coverage": False, "complete_external_argument_coverage": False,
        "full_python_soundness_proved": False,
        "assumptions": ["Framework declarations are potential external entries under the recorded package and activation conditions.",
            "Registration reachability, callback wrappers, request/command arguments and external framework effects require additional transfer semantics."]}
