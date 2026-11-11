"""Source-bound defensive audit candidates and explicit evidence boundaries.

No target code is imported or executed. API identity is resolved from source
imports and conservative lexical bindings. Local dependency facts assume the
declared function parameters, never that they are remotely attacker-controlled.
Solver paths establish reachability only, not interprocedural taint or a bug.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path


RULES = {
    "command_execution": {"os.system", "os.popen", "subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call", "subprocess.check_output", "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell"},
    "python_code_execution": {"builtins.eval", "builtins.exec", "builtins.compile"},
    "pickle_deserialization": {"pickle.load", "pickle.loads", "dill.load", "dill.loads", "cloudpickle.load", "cloudpickle.loads", "joblib.load"},
    "yaml_deserialization": {"yaml.load", "yaml.load_all", "yaml.unsafe_load", "yaml.full_load", "yaml.safe_load", "yaml.safe_load_all"},
    "model_deserialization": {"torch.load", "torch.serialization.load", "safetensors.torch.load_file"},
    "outbound_http": {"requests.get", "requests.post", "requests.request", "httpx.get", "httpx.post", "httpx.request", "urllib.request.urlopen", "urllib.request.urlretrieve", "aiohttp.ClientSession"},
    "filesystem_open": {"builtins.open", "io.open", "os.open", "pathlib.Path.open"},
    "template_trust": {"django.utils.safestring.mark_safe", "django.template.Template", "jinja2.Template", "jinja2.Environment"},
    "output_encoding": {"django.utils.html.escape", "django.utils.html.conditional_escape", "django.utils.html.format_html", "markupsafe.escape", "html.escape"},
    "path_guard": {"django.utils._os.safe_join", "os.path.commonpath"},
    "secret_logging": {"logging.debug", "logging.info", "logging.warning", "logging.error", "logging.exception", "logging.critical"},
}
METHOD_CANDIDATES = {
    "execute": "database_execution", "executemany": "database_execution", "raw": "database_execution",
    "from_pretrained": "remote_model_policy", "load_state_dict": "model_deserialization",
    "from_string": "template_trust", "add_api_route": "route_authentication",
    "debug": "secret_logging", "info": "secret_logging", "warning": "secret_logging", "error": "secret_logging",
    "exec_module": "python_code_execution", "loads": "deserialization_call",
}
POLICY_KEYS = {"shell", "verify", "weights_only", "trust_remote_code", "check", "Loader", "dependencies", "autoescape"}


def _text(node):
    return ast.unparse(node)[:300]


def _target_names(target):
    if isinstance(target, ast.Name): return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return set().union(*(_target_names(n) for n in target.elts)) if target.elts else set()
    return set()


def _parameters(node):
    args = node.args
    return [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs,
                            *(n for n in (args.vararg, args.kwarg) if n is not None))]


_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _child_statements(node):
    """Find compound-statement bodies without entering expression scopes."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.stmt):
            yield child
        elif isinstance(child, (ast.ExceptHandler, ast.match_case)):
            yield from _child_statements(child)


def _symbol(node, bindings):
    if isinstance(node, ast.Name):
        if node.id in bindings: return bindings[node.id]
        if node.id in {"eval", "exec", "compile", "open"}: return "builtins." + node.id
    if isinstance(node, ast.Attribute):
        parent = _symbol(node.value, bindings)
        if parent:
            full = parent + "." + node.attr
            if any(key.startswith("@invalid:") and (full == key[9:] or full.startswith(key[9:] + ".")) for key in bindings):
                return None
            return full
    return None


def _invalidate_attribute(node, bindings):
    if isinstance(node, ast.Attribute):
        parent = _symbol(node.value, bindings)
        if parent: bindings["@invalid:" + parent + "." + node.attr] = None


def _invalidate_calls(node, bindings):
    for child in ast.walk(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                and child.func.id in {"setattr", "delattr"} and len(child.args) >= 2):
            parent = _symbol(child.args[0], bindings)
            if parent:
                name = child.args[1]
                suffix = "." + name.value if isinstance(name, ast.Constant) and type(name.value) is str else ""
                bindings["@invalid:" + parent + suffix] = None


def _imports(statements, module, package):
    values = {}
    for node in statements:
        if isinstance(node, ast.Import):
            for alias in node.names:
                values[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
            prefix = node.module or ""
            if node.level:
                parts = package.split(".")
                prefix = ".".join(parts[:len(parts) - node.level + 1] + ([prefix] if prefix else []))
            for alias in node.names:
                if alias.name != "*": values[alias.asname or alias.name] = prefix + "." + alias.name if prefix else alias.name
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            values[node.name] = module + "." + node.name
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in _target_names(target): values[name] = None
    return values


class _FunctionLocals(ast.NodeVisitor):
    def __init__(self): self.names = set()
    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)): self.names.add(node.id)
    def visit_Import(self, node): self.names.update(a.asname or a.name.split(".")[0] for a in node.names)
    def visit_ImportFrom(self, node): self.names.update(a.asname or a.name for a in node.names if a.name != "*")
    def visit_ExceptHandler(self, node):
        if node.name: self.names.add(node.name)
        self.generic_visit(node)
    def visit_FunctionDef(self, node): self.names.add(node.name)
    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef
    def visit_Lambda(self, node): pass
    def visit_ListComp(self, node): pass
    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp


class _Guards(ast.NodeVisitor):
    def __init__(self): self.guards = []
    def visit_If(self, node):
        self.guards.append({"line": node.lineno, "test": _text(node.test)})
        self.generic_visit(node)
    def visit_FunctionDef(self, node): pass
    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef
    def visit_Lambda(self, node): pass


class _Audit:
    def __init__(self, module, path, digest, project_roots=()):
        self.module, self.path, self.digest = module, path, digest
        self.project_roots = frozenset(project_roots)
        self.findings, self.functions, self.residuals = [], [], []

    def scan(self, statements, inherited, parent=None, closure_names=(), definition_uncertain=False):
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = (parent + "." if parent else self.module + ".") + node.name
                params = _parameters(node)
                local = _FunctionLocals()
                for statement in node.body: local.visit(statement)
                captured = sorted({child.id for child in ast.walk(node)
                                   if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)}
                                  & set(closure_names) - local.names - set(params))
                if captured:
                    self.residuals.append({"function": qualified, "line": node.lineno,
                        "kind": "captured_free_variable_flow_unproved", "names": captured,
                        "interpretation": "Captured values are not represented by this function-local parameter dependency map."})
                bindings = dict(inherited)
                for name in local.names | set(params): bindings[name] = None
                deps = {name: {name} for name in params}
                guard_scan = _Guards()
                for statement in node.body: guard_scan.visit(statement)
                self.functions.append({"qualified_name": qualified, "parameters": params,
                                       "source": self.path, "line": node.lineno,
                                       "syntactic_guard_conditions": guard_scan.guards,
                                       "guard_dominance_proved": False,
                                       "definition_context_unproved": definition_uncertain})
                self.block(node.body, bindings, deps, qualified, [])
                self.scan(node.body, bindings, qualified,
                          set(closure_names) | local.names | set(params))
            elif isinstance(node, ast.ClassDef):
                qualified = (parent + "." if parent else self.module + ".") + node.name
                self.residuals.append({"function": qualified, "line": node.lineno,
                                       "kind": "class_body_execution_unproved"})
                # Methods do not capture a class body's local namespace.
                self.scan(node.body, inherited, qualified, closure_names, definition_uncertain)
            else:
                nested = list(_child_statements(node))
                if not nested:
                    continue
                # A definition may occur under a branch, handler, loop or with
                # statement. Discover it without promoting any possibly bound
                # import/name from that container to an unconditional binding.
                changed = _FunctionLocals()
                changed.visit(node)
                conservative = dict(inherited)
                for name in changed.names:
                    conservative[name] = None
                self.scan(nested, conservative, parent, closure_names, True)

    def dependencies(self, node, deps):
        if node is None: return set()
        if isinstance(node, ast.Name): return set(deps.get(node.id, ()))
        if isinstance(node, ast.Lambda): return set()
        if isinstance(node, _COMPREHENSIONS):
            local = {key:set(value) for key,value in deps.items()}
            for generator in node.generators:
                for name in _target_names(generator.target): local.pop(name, None)
            result = set()
            for index, generator in enumerate(node.generators):
                sources = self.dependencies(generator.iter, deps if index == 0 else local)
                result |= sources
                for name in _target_names(generator.target): local[name] = sources
                for condition in generator.ifs: result |= self.dependencies(condition, local)
            values = (node.key, node.value) if isinstance(node, ast.DictComp) else (node.elt,)
            for value in values: result |= self.dependencies(value, local)
            return result
        return set().union(*(self.dependencies(n, deps) for n in ast.iter_child_nodes(node)))

    def policy(self, call, symbol, bindings):
        result = []
        for keyword in call.keywords:
            if keyword.arg not in POLICY_KEYS: continue
            value = keyword.value
            if isinstance(value, ast.Constant) and type(value.value) in (bool, str, type(None)):
                status = "literal"
                literal = value.value
            else:
                status, literal = "unresolved_expression", None
            item = {"name": keyword.arg, "status": status, "expression": _text(value)}
            if status == "literal": item["value"] = literal
            elif keyword.arg == "Loader":
                loader = _symbol(value, bindings)
                if loader in {"yaml.SafeLoader", "yaml.CSafeLoader"} and "yaml" not in self.project_roots:
                    item.update(status="known_safe_loader_binding", symbol=loader)
            result.append(item)
        if (symbol in {"yaml.safe_load", "yaml.safe_load_all", "safetensors.torch.load_file"}
                and symbol.split(".")[0] not in self.project_roots):
            result.append({"name": "deserializer_api", "status": "known_restricted_api", "symbol": symbol})
        return result

    def expression(self, node, bindings, deps, function, guards):
        if node is None: return
        if isinstance(node, ast.Lambda):
            self.residuals.append({"function": function, "line": node.lineno,
                                   "kind": "lambda_body_unanalyzed"})
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): return
        if isinstance(node, _COMPREHENSIONS):
            local_b = dict(bindings)
            local_d = {key:set(value) for key,value in deps.items()}
            # All generator targets are local throughout the comprehension;
            # only the first iterable is evaluated in the enclosing scope.
            for generator in node.generators:
                for name in _target_names(generator.target):
                    local_b[name] = None
                    local_d.pop(name, None)
            for index, generator in enumerate(node.generators):
                iter_b, iter_d = (bindings, deps) if index == 0 else (local_b, local_d)
                self.expression(generator.iter, iter_b, iter_d, function, guards)
                sources = self.dependencies(generator.iter, iter_d)
                for name in _target_names(generator.target): local_d[name] = sources
                for condition in generator.ifs:
                    self.expression(condition, local_b, local_d, function, guards)
            values = (node.key, node.value) if isinstance(node, ast.DictComp) else (node.elt,)
            for value in values: self.expression(value, local_b, local_d, function, guards)
            self.residuals.append({"function": function, "line": node.lineno,
                "kind": "comprehension_control_or_resumption_unproved"})
            # Assignment expressions in comprehensions can bind the enclosing
            # scope. Zero iterations and delayed generator execution prevent
            # an unconditional value assertion, so invalidate rather than trust.
            assigned = {name for child in ast.walk(node) if isinstance(child, ast.NamedExpr)
                        for name in _target_names(child.target)}
            for name in assigned:
                bindings[name] = None
                deps[name] = set(deps.get(name, ())) | set(local_d.get(name, ()))
            if assigned:
                self.residuals.append({"function": function, "line": node.lineno,
                    "kind": "comprehension_outer_assignment_unproved", "names": sorted(assigned)})
            return
        if isinstance(node, ast.NamedExpr):
            self.expression(node.value, bindings, deps, function, guards)
            sources = self.dependencies(node.value, deps)
            for name in _target_names(node.target):
                bindings[name] = None
                deps[name] = sources
            return
        if isinstance(node, ast.Call):
            symbol = _symbol(node.func, bindings)
            rule = next((name for name, names in RULES.items() if symbol in names), None)
            resolution = "source_import_binding"
            if symbol and symbol.split(".")[0] in self.project_roots:
                resolution = "project_source_binding"
            if rule is None and isinstance(node.func, ast.Attribute) and node.func.attr in METHOD_CANDIDATES:
                rule = METHOD_CANDIDATES[node.func.attr]
                resolution = "unresolved_receiver_candidate"
            # Explicit model-loading trust flags are worth reviewing even when
            # the loader is a factory return or imported class re-export.
            if rule is None and any(k.arg == "trust_remote_code" for k in node.keywords):
                rule, resolution = "remote_model_policy", "keyword_policy_candidate"
            if rule is None and any(k.arg == "dependencies" for k in node.keywords):
                rule, resolution = "authorization_dependency_policy", "keyword_policy_candidate"
            if rule:
                inputs = set().union(*(self.dependencies(v, deps) for v in
                    (*node.args, *(k.value for k in node.keywords))))
                self.findings.append({"id": f"{self.path}:{node.lineno}:{node.col_offset}:{rule}",
                    "rule_id": rule, "source": self.path, "source_sha256": self.digest,
                    "line": node.lineno, "column": node.col_offset, "function": function,
                    "callee_expression": _text(node.func), "resolved_symbol": symbol,
                    "resolution": resolution, "origin": "source_ast_audit",
                    "trusted_external_api_identity": resolution == "source_import_binding",
                    "parameter_dependencies": sorted(inputs), "parameter_taint_assumption": bool(inputs),
                    "policy_evidence": self.policy(node, symbol, bindings),
                    "enclosing_branch_conditions": list(guards),
                    "vulnerability_confirmed": False, "sanitization_proved": False,
                    "interpretation": "Audit candidate; local parameter dependency and syntactic policy are not exploitability, interprocedural taint or guard correctness proofs."})
        for child in ast.iter_child_nodes(node): self.expression(child, bindings, deps, function, guards)

    def block(self, statements, bindings, deps, function, guards):
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bindings[node.name] = function + "." + node.name
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                bindings.update(_imports([node], self.module, self.module.rpartition(".")[0]))
                continue
            if isinstance(node, ast.If):
                self.expression(node.test, bindings, deps, function, guards)
                left_b, right_b = dict(bindings), dict(bindings)
                left_d, right_d = {k:set(v) for k,v in deps.items()}, {k:set(v) for k,v in deps.items()}
                self.block(node.body, left_b, left_d, function, [*guards, {"test": _text(node.test), "branch": True}])
                self.block(node.orelse, right_b, right_d, function, [*guards, {"test": _text(node.test), "branch": False}])
                for key in left_b.keys() | right_b.keys(): bindings[key] = left_b.get(key) if left_b.get(key) == right_b.get(key) else None
                for key in left_d.keys() | right_d.keys(): deps[key] = left_d.get(key,set()) | right_d.get(key,set())
                continue
            if isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith)):
                # Control-flow/heap semantics beyond branch-local facts remain
                # explicit. A union retains possible parameter dependencies.
                self.residuals.append({"function": function, "line": node.lineno, "kind": "control_or_heap_flow_unproved"})
                for field in ("test", "iter"):
                    self.expression(getattr(node,field,None), bindings,deps,function,guards)
                if isinstance(node,(ast.For,ast.AsyncFor)):
                    for name in _target_names(node.target): deps[name] = self.dependencies(node.iter,deps)
                nested = []
                if isinstance(node,(ast.With,ast.AsyncWith)):
                    for item in node.items:
                        self.expression(item.context_expr,bindings,deps,function,guards)
                        if item.optional_vars:
                            for name in _target_names(item.optional_vars): deps[name]=self.dependencies(item.context_expr,deps)
                nested.extend(getattr(node,"body",()))
                nested.extend(getattr(node,"orelse",()))
                for handler in getattr(node,"handlers",()): nested.extend(handler.body)
                nested.extend(getattr(node,"finalbody",()))
                before = {k:set(v) for k,v in deps.items()}
                self.block(nested,bindings,deps,function,guards)
                for key,value in before.items(): deps[key] = deps.get(key,set()) | value
                continue
            self.expression(node,bindings,deps,function,guards)
            _invalidate_calls(node, bindings)
            if isinstance(node,(ast.Assign,ast.AnnAssign,ast.AugAssign)):
                value = getattr(node,"value",None)
                targets = node.targets if isinstance(node,ast.Assign) else [node.target]
                for target in targets:
                    for name in _target_names(target):
                        old = deps.get(name,set()) if isinstance(node,ast.AugAssign) else set()
                        deps[name] = old | self.dependencies(value,deps)
                        bindings[name] = _symbol(value,bindings) if not isinstance(node,ast.AugAssign) else None
                    if isinstance(target,ast.Attribute):
                        _invalidate_attribute(target, bindings)
            if isinstance(node,ast.Delete):
                for target in node.targets:
                    for name in _target_names(target): bindings[name]=None;deps.pop(name,None)
                    _invalidate_attribute(target, bindings)


def analyze_security(project_root, source_roots=(".",), *, import_roots=(".",),
                     call_edges=(), solver_completed=False, entry_points=(),
                     max_files=5000, max_source_bytes=128*1024*1024):
    """Inventory defensive audit evidence over full declared runtime roots.

    Supplied solver edges are optional external evidence. Reachability never
    substitutes for argument flow, and a failed fixpoint cannot support it.
    """
    root=Path(project_root).resolve()
    if not root.is_dir(): raise ValueError("project root must exist")
    if type(max_files) is not int or max_files<1 or type(max_source_bytes) is not int or max_source_bytes<1:
        raise ValueError("budgets must be positive integers")
    if type(solver_completed) is not bool: raise ValueError("solver_completed must be Boolean")
    bases=[(root/p).resolve() for p in import_roots]
    if not bases or any(not p.is_relative_to(root) or not p.is_dir() for p in bases): raise ValueError("invalid import roots")
    files=set()
    for relative in source_roots:
        source=(root/relative).resolve()
        if not source.is_relative_to(root) or not source.exists(): raise ValueError("invalid source root")
        files.update([source] if source.is_file() else source.rglob("*.py"))
    project_modules=set()
    for path in files:
        base=max((b for b in bases if path.is_relative_to(b)),key=lambda x:len(x.parts),default=None)
        if base is not None:
            parts=list(path.relative_to(base).with_suffix("").parts)
            if parts and parts[-1]=="__init__":parts.pop()
            if parts:project_modules.add(parts[0])
    inventory=[];findings=[];functions=[];residuals=[];consumed=0
    for index,path in enumerate(sorted(files)):
        if not path.resolve().is_relative_to(root): raise ValueError("source symlink escapes root")
        row={"source":str(path.relative_to(root)),"status":"unmeasured_budget"}
        inventory.append(row)
        size=path.stat().st_size
        if index>=max_files or consumed+size>max_source_bytes: continue
        consumed+=size;raw=path.read_bytes();row.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        base=max((b for b in bases if path.is_relative_to(b)),key=lambda x:len(x.parts),default=None)
        if base is None:row["status"]="outside_import_roots";continue
        parts=list(path.relative_to(base).with_suffix("").parts)
        package=parts[-1]=="__init__"
        if package:parts.pop()
        module=".".join(parts)
        if not module:row["status"]="invalid_module_identity";continue
        try:tree=ast.parse(raw,filename=str(path))
        except (SyntaxError,ValueError) as exc:row.update(status="parse_error",diagnostic=str(exc));continue
        row.update(status="parsed",module=module)
        audit=_Audit(module,row["source"],row["sha256"],project_modules)
        bindings=_imports(tree.body,module,module if package else module.rpartition(".")[0])
        audit.block(tree.body,bindings,{},module,[])
        audit.scan(tree.body,bindings)
        findings.extend(audit.findings);functions.extend(audit.functions);residuals.extend(audit.residuals)
    edges=[]
    for pair in call_edges:
        if type(pair) not in (tuple,list) or len(pair)!=2 or any(type(v) is not str for v in pair): raise ValueError("call edges require string pairs")
        edges.append(tuple(pair))
    graph={}
    for caller,callee in edges:graph.setdefault(caller,set()).add(callee)
    reaches={}
    if solver_completed:
        for entry in entry_points:
            seen={entry};todo=[entry]
            while todo:
                current=todo.pop()
                for nxt in graph.get(current,()):
                    if nxt not in seen:seen.add(nxt);todo.append(nxt)
            reaches[entry]=seen
    paths=[]
    for finding in findings:
        for entry,seen in reaches.items():
            if finding["function"] in seen:
                paths.append({"entry":entry,"finding_id":finding["id"],"sink_function":finding["function"],
                              "origin":"supplied_completed_solver_call_projection",
                              "interprocedural_taint_proved":False,"vulnerability_confirmed":False})
    completed=all(row["status"]=="parsed" for row in inventory)
    return {"schema_version":1,"status":"inventory_completed_with_residuals" if completed else "incomplete_inventory",
            "inventory":inventory,"source_files":len(inventory),"parsed_files":sum(r["status"]=="parsed" for r in inventory),
            "unmeasured_files":sum(r["status"]!="parsed" for r in inventory),"source_bytes_read":consumed,
            "findings":findings,"functions":functions,"residuals":residuals,
            "local_parameter_flow_candidates":sum(bool(f["parameter_dependencies"]) for f in findings),
            "solver_supported_reachability":paths,"solver_completed":solver_completed,
            "solver_edges_supplied":len(edges),"interprocedural_taint_proved":False,
            "vulnerabilities_confirmed":0,"security_complete":False,"target_source_executed":False,
            "interpretation":"Passive audit inventory and explicit conditional evidence. API presence, local parameter dependence, policy syntax and solver reachability are separate; none alone proves a vulnerability or safety."}
