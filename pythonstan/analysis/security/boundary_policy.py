"""Source-bound finite keyword-transfer checks, separate from safety proofs.

This component models original function bodies with bool/None policy inputs.
Opaque operations/conditions require explicit frame/binding stability contracts.
It establishes model may-values at boundary calls, not Python/global soundness,
call feasibility, dependency effects, trust, authentication, or native safety.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json


MODEL = "finite-boundary-policy-v1"
REQUIRED_CONTRACTS = frozenset({
    "original_function_body_entry",
    "stable_import_and_callable_bindings",
    "opaque_operations_do_not_mutate_analyzed_frames",
    "normal_paths_to_boundary_only",
})
DOMAIN = (False, True, None)


class UnsupportedPolicyModel(ValueError):
    pass


def _key(value):
    return "none" if value is None else "true" if value is True else "false"


def _literal(node):
    if isinstance(node, ast.Constant) and (node.value is None or type(node.value) is bool):
        return node.value
    raise UnsupportedPolicyModel("Policy values must be exact bool or None")


def _root(node):
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _functions(source, module):
    tree = ast.parse(source)
    # Parsing alone accepts context-invalid break/return and duplicate argument
    # constructs. Compilation validates them without executing target code.
    compile(tree, module, "exec", dont_inherit=True)
    found = {}
    def visit(body, prefix):
        for statement in body:
            if isinstance(statement, ast.ClassDef):
                visit(statement.body, prefix + [statement.name])
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[".".join([module, *prefix, statement.name])] = statement
    visit(tree.body, [])
    return tree, found


@dataclass
class _Program:
    sources: dict
    request: dict

    def __post_init__(self):
        if (type(self.sources) is not dict or not self.sources or len(self.sources) > 32
                or any(type(k) is not str or not k or type(v) is not str for k,v in self.sources.items())
                or sum(len(s.encode()) for s in self.sources.values()) > 4 * 1024 * 1024):
            raise UnsupportedPolicyModel("Model requires up to 32 text source modules totaling at most 4 MiB")
        allowed = {"entry","policy","forwarders","boundaries","boundary_imports","entry_values","contracts","max_steps"}
        if (type(self.request) is not dict or set(self.request) - allowed
                or type(self.request.get('entry')) is not str or type(self.request.get('policy')) is not str
                or type(self.request.get('contracts')) is not list
                or any(type(v) is not str for v in self.request['contracts'])
                or type(self.request.get('boundaries')) is not list
                or any(type(v) is not str for v in self.request['boundaries'])
                or type(self.request.get('forwarders',{})) is not dict
                or any(type(k) is not str or type(v) is not str for k,v in self.request.get('forwarders',{}).items())
                or type(self.request.get('boundary_imports',{})) is not dict
                or any(type(k) is not str or type(v) is not str for k,v in self.request.get('boundary_imports',{}).items())):
            raise UnsupportedPolicyModel("Malformed finite-model request")
        if set(self.request.get("contracts", [])) != REQUIRED_CONTRACTS:
            raise UnsupportedPolicyModel("All explicit model contracts are required; they are assumptions, not proved facts")
        self.policy = self.request["policy"]
        self.steps = 0
        self.max_steps = self.request.get("max_steps", 10000)
        if type(self.max_steps) is not int or not 0 < self.max_steps <= 1000000:
            raise UnsupportedPolicyModel("Finite path budget must be an integer in 1..1000000")
        self.links = dict(self.request.get("forwarders", {}))
        self.boundaries = set(self.request["boundaries"])
        self.boundary_imports = self.request.get("boundary_imports", {})
        if not self.boundaries or not self.sources:
            raise UnsupportedPolicyModel("Declared sources and boundaries required")
        self.functions = {}
        self.trees = {}
        for module, source in self.sources.items():
            tree, functions = _functions(source, module)
            self.trees[module] = tree
            for name, node in functions.items():
                self.functions[name] = (module, node)
        if self.request["entry"] not in self.functions:
            raise UnsupportedPolicyModel("Original entry body missing")
        self.entry_values = self.request.get("entry_values", [False, True, None, "omitted"])
        if type(self.entry_values) is not list or not self.entry_values or any(not (v is None or type(v) is bool or type(v) is str and v == "omitted") for v in self.entry_values):
            raise UnsupportedPolicyModel("Unsupported finite entry domain")
        if len({('omitted' if type(v) is str else _key(v)) for v in self.entry_values}) != len(self.entry_values):
            raise UnsupportedPolicyModel("Duplicate finite entry input")
        self.checked = set()
        self.prepare(self.request["entry"], set())

    def tick(self):
        self.steps += 1
        if self.steps > self.max_steps:
            raise UnsupportedPolicyModel("Finite path budget exhausted; no coverage certificate is issued")

    def default(self, function):
        node = self.functions[function][1]
        positional = [*node.args.posonlyargs, *node.args.args]
        defaults = {arg.arg: value for arg, value in zip(positional[-len(node.args.defaults):], node.args.defaults)} if node.args.defaults else {}
        defaults.update({arg.arg: value for arg, value in zip(node.args.kwonlyargs, node.args.kw_defaults) if value is not None})
        if self.policy not in defaults:
            raise UnsupportedPolicyModel("Omitted policy has no supported default")
        return _literal(defaults[self.policy])

    def prepare(self, function, active):
        if len(active) >= 32 or len(self.checked) >= 256:
            raise UnsupportedPolicyModel("Forwarding composition budget exhausted")
        if function in active:
            raise UnsupportedPolicyModel("Recursive forwarders need a separate fixpoint proof")
        if function in self.checked:
            return
        module, node = self.functions[function]
        if isinstance(node, ast.AsyncFunctionDef) or node.args.vararg or node.args.kwarg:
            raise UnsupportedPolicyModel("Async/variadic function bodies are outside this model")
        params = {a.arg for a in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]}
        if self.policy not in params:
            raise UnsupportedPolicyModel("Every forwarder must declare the policy parameter")
        forbidden = (ast.Try, ast.TryStar, ast.For, ast.AsyncFor, ast.While, ast.Match,
                     ast.Global, ast.Nonlocal, ast.Lambda, ast.Yield, ast.YieldFrom,
                     ast.Await, ast.NamedExpr, ast.ListComp, ast.SetComp, ast.DictComp,
                     ast.GeneratorExp, ast.Delete, ast.AugAssign)
        for statement in node.body:
            for item in ast.walk(statement):
                if isinstance(item, forbidden) or isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    raise UnsupportedPolicyModel("Unsupported control, scope, mutation, or continuation")
                if isinstance(item, ast.With) and (item is not node.body[-1] or len(item.body) != 1 or not isinstance(item.body[0], ast.Return)):
                    raise UnsupportedPolicyModel("Only final top-level with containing one return is modeled; exception suppression otherwise changes continuation")
                if isinstance(item, ast.Call):
                    if any(isinstance(a, ast.Starred) for a in item.args) or any(k.arg is None for k in item.keywords):
                        raise UnsupportedPolicyModel("Expanded call arguments are outside the finite scalar model")
                    if isinstance(item.func, ast.Name) and item.func.id in {"eval", "exec", "locals", "globals", "vars", "getattr", "setattr", "delattr"}:
                        raise UnsupportedPolicyModel("Reflective call is outside the model")
                    expression = ast.unparse(item.func)
                    if expression in self.links or expression in self.boundaries:
                        self.check_binding(module, node, item.func, self.links.get(expression))
                        if expression in self.links:
                            target = self.links[expression]
                            if target not in self.functions:
                                raise UnsupportedPolicyModel("Forwarder body unavailable")
                            self.prepare(target, active | {function})
        self.checked.add(function)

    def check_binding(self, module, function, expression, target):
        root = _root(expression)
        if root is None:
            raise UnsupportedPolicyModel("Boundary/forwarder needs a lexical name/attribute path")
        declarations = []
        class Bindings(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                if node.name == root:
                    declarations.append("other")
            visit_AsyncFunctionDef = visit_FunctionDef
            def visit_ClassDef(self, node):
                if node.name == root:
                    declarations.append("other")
            def visit_Import(self, node):
                for alias in node.names:
                    if (alias.asname or alias.name.split('.')[0]) == root:
                        declarations.append(alias.name)
            def visit_ImportFrom(self, node):
                for alias in node.names:
                    if alias.name == '*':
                        declarations.append("other")
                    if (alias.asname or alias.name) == root:
                        declarations.append((node.module or '') + '.' + alias.name if not node.level else "relative")
            def visit_Name(self, node):
                if isinstance(node.ctx, (ast.Store, ast.Del)) and node.id == root:
                    declarations.append("other")
            def visit_Attribute(self, node):
                if isinstance(node.ctx, (ast.Store, ast.Del)) and _root(node) == root:
                    declarations.append("mutation")
                self.generic_visit(node)
        Bindings().visit(self.trees[module])
        if len(declarations) != 1 or declarations[0] in {"other", "mutation", "relative"}:
            raise UnsupportedPolicyModel("Callable identity has ambiguous/mutated module binding")
        if target is not None and declarations[0] != target:
            raise UnsupportedPolicyModel("Forwarder import alias does not name the supplied source body")
        if target is None and not ast.unparse(expression).startswith(root + '.'):
            raise UnsupportedPolicyModel("External boundary must have an imported module path")
        if target is None and declarations[0] != self.boundary_imports.get(root):
            raise UnsupportedPolicyModel("Boundary module import does not match the explicit source contract")
        params = {a.arg for a in [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]}
        if root in params:
            raise UnsupportedPolicyModel("Callable import shadowed by a parameter")
        for statement in function.body:
            for item in ast.walk(statement):
                if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del)) and item.id == root:
                    raise UnsupportedPolicyModel("Callable import shadowed by a local binding")
                if isinstance(item, ast.Attribute) and isinstance(item.ctx, (ast.Store, ast.Del)) and _root(item) == root:
                    raise UnsupportedPolicyModel("Callable module attribute replaced")

    def argument(self, call, target, env, evaluate):
        keywords = [keyword for keyword in call.keywords if keyword.arg == self.policy]
        if len(keywords) > 1:
            raise UnsupportedPolicyModel("Repeated policy keyword")
        if target:
            node = self.functions[target][1]
            positional = [*node.args.posonlyargs, *node.args.args]
            _bind_call(call, node)
            position = next((i for i, arg in enumerate(positional) if arg.arg == self.policy), None)
            positional_value = call.args[position] if position is not None and len(call.args) > position else None
            if keywords and positional_value is not None:
                raise UnsupportedPolicyModel("Policy supplied both positionally and by keyword")
            if keywords:
                return evaluate(keywords[0].value, env)
            if positional_value is not None:
                return evaluate(positional_value, env)
            return {_key(self.default(target))}
        if not keywords:
            raise UnsupportedPolicyModel("Boundary policy omission has no supplied dependency contract")
        return evaluate(keywords[0].value, env)


def _abstract_value(node, env):
    if isinstance(node, ast.Name) and node.id in env:
        return env[node.id]
    return {_key(_literal(node))}


def _bind_call(call, function):
    """Validate ordinary Python argument binding before composing body models."""
    positional = [*function.args.posonlyargs, *function.args.args]
    names = [arg.arg for arg in positional]
    kwonly = {arg.arg for arg in function.args.kwonlyargs}
    if len(call.args) > len(positional):
        raise UnsupportedPolicyModel("Too many positional forwarding arguments")
    occupied = set(names[:len(call.args)])
    posonly = {a.arg for a in function.args.posonlyargs}
    for keyword in call.keywords:
        if keyword.arg is None or keyword.arg in occupied or keyword.arg in posonly or keyword.arg not in set(names) | kwonly:
            raise UnsupportedPolicyModel("Invalid/duplicate forwarding keyword binding")
        occupied.add(keyword.arg)
    required = set(names[:len(names)-len(function.args.defaults)])
    required.update(arg.arg for arg, default in zip(function.args.kwonlyargs, function.args.kw_defaults) if default is None)
    if required - occupied:
        raise UnsupportedPolicyModel("Forwarding call omits a required argument")


def _abstract_test(node, env):
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
        if isinstance(node.left, ast.Name) and node.left.id in env and isinstance(node.ops[0], (ast.Is, ast.IsNot)):
            value = _key(_literal(node.comparators[0]))
            matches = {v == value for v in env[node.left.id]}
            return {not b for b in matches} if isinstance(node.ops[0], ast.IsNot) else matches
        if any(isinstance(n, ast.Name) and n.id in env for n in ast.walk(node)):
            raise UnsupportedPolicyModel("Tracked policy comparisons must use identity against bool/None")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return {not v for v in _abstract_test(node.operand, env)}
    if isinstance(node, ast.Constant) and type(node.value) is bool:
        return {node.value}
    if isinstance(node, ast.Name) and node.id in env:
        raise UnsupportedPolicyModel("Implicit policy truthiness is outside this model")
    return {False, True}


def infer_boundary_policy(sources, request):
    """Infer a candidate finite may relation; callers must run the checker."""
    program = _Program(sources, request)
    observations = set()
    def expression(node, env, function, input_key):
        for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
            name = ast.unparse(call.func)
            target = program.links.get(name)
            if target or name in program.boundaries:
                values = program.argument(call, target, env, _abstract_value)
                if target:
                    body(target, [{program.policy: values}], input_key)
                else:
                    for value in values:
                        observations.add((input_key, function, call.lineno, name, value))
    def block(statements, states, function, input_key):
        for statement in statements:
            output = []
            for env in states:
                program.tick()
                if isinstance(statement, ast.If):
                    outcomes = _abstract_test(statement.test, env)
                    expression(statement.test, env, function, input_key)
                    for outcome in outcomes:
                        branch = statement.body if outcome else statement.orelse
                        output.extend(block(branch, [dict(env)], function, input_key))
                elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
                    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                    if any(not isinstance(t, ast.Name) for t in targets):
                        raise UnsupportedPolicyModel("Heap/tuple assignments outside model")
                    if any(t.id == program.policy for t in targets):
                        if statement.value is None:
                            raise UnsupportedPolicyModel("Annotation-only policy binding outside model")
                        if len(targets) != 1:
                            raise UnsupportedPolicyModel("Policy alias assignment outside model")
                        env = {**env, program.policy: _abstract_value(statement.value, env)}
                    else:
                        if statement.value is not None:
                            expression(statement.value, env, function, input_key)
                    output.append(env)
                elif isinstance(statement, ast.With):
                    for item in statement.items:
                        expression(item.context_expr, env, function, input_key)
                        if item.optional_vars is not None and any(isinstance(n, ast.Name) and n.id == program.policy for n in ast.walk(item.optional_vars)):
                            raise UnsupportedPolicyModel("With target overwrites policy")
                    output.extend(block(statement.body, [dict(env)], function, input_key))
                elif isinstance(statement, ast.Return):
                    if statement.value is not None:
                        expression(statement.value, env, function, input_key)
                elif isinstance(statement, ast.Raise):
                    if statement.exc is not None:
                        expression(statement.exc, env, function, input_key)
                    if statement.cause is not None:
                        expression(statement.cause, env, function, input_key)
                elif isinstance(statement, (ast.Expr, ast.Pass)):
                    if isinstance(statement, ast.Expr):
                        expression(statement.value, env, function, input_key)
                    output.append(env)
                else:
                    raise UnsupportedPolicyModel("Unsupported statement " + type(statement).__name__)
            states = output
        return states
    def body(function, states, input_key):
        block(program.functions[function][1].body, states, function, input_key)
    for value in program.entry_values:
        actual = program.default(request['entry']) if value == 'omitted' else value
        body(request['entry'], [{program.policy: {_key(actual)}}], 'omitted' if value == 'omitted' else _key(value))
    return {"model": MODEL, "source_sha256": {m: hashlib.sha256(s.encode()).hexdigest() for m, s in sources.items()},
        "request_sha256": hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest(),
        "observations": [dict(input=i, function=f, line=l, boundary=b, value=v) for i, f, l, b, v in sorted(observations)],
        "status": "candidate_unchecked", "whole_program_soundness_proved": False,
        "native_safety_proved": False}


def check_boundary_policy(sources, request, candidate):
    """Independent concrete finite-path enumeration checks candidate coverage.

    The checker reparses original source and does not invoke abstract inference.
    Shared validation defines the admitted fragment; correspondence from arbitrary
    Python/external operations to this finite model remains a stated assumption.
    """
    try:
        p = _Program(sources, request)
        if (type(candidate) is not dict or type(candidate.get('observations')) is not list
                or any(type(r) is not dict or set(r) != {'input','function','line','boundary','value'}
                       or any(type(r.get(k)) is not str for k in ('input','function','boundary','value'))
                       or type(r.get('line')) is not int or r['line'] < 1 for r in candidate['observations'])):
            return {"status":"rejected","reason":"Malformed candidate relation"}
        hashes = {m: hashlib.sha256(s.encode()).hexdigest() for m, s in sources.items()}
        if candidate.get('model') != MODEL or candidate.get('source_sha256') != hashes or candidate.get('request_sha256') != hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest():
            return {"status": "rejected", "reason": "source/request/model binding mismatch"}
        claimed = {(r['input'], r['function'], r['line'], r['boundary'], r['value']) for r in candidate['observations']}
        if any(type(r.get('line')) is not int or r.get('value') not in {'false','true','none'} for r in candidate['observations']):
            return {"status": "rejected", "reason": "invalid candidate values"}
        actual = _concrete_relations(p)
        if not actual:
            return {"status": "unmeasured", "reason": "No declared source boundary case reached in the model", "whole_program_soundness_proved": False, "native_safety_proved": False}
        inputs = {('omitted' if type(v) is str else _key(v)) for v in p.entry_values}
        locations = set()
        for function in p.checked:
            for statement in p.functions[function][1].body:
                for call in (n for n in ast.walk(statement) if isinstance(n, ast.Call)):
                    expression = ast.unparse(call.func)
                    if expression in p.boundaries:
                        locations.add((function, call.lineno, expression))
        if any(r['input'] not in inputs or (r['function'],r['line'],r['boundary']) not in locations for r in candidate['observations']):
            return {"status": "rejected", "reason": "Candidate claims undeclared input or non-source boundary location"}
        if len(claimed) != len(candidate['observations']):
            return {"status": "rejected", "reason": "Duplicate candidate boundary case"}
        missing = actual - claimed
        if missing:
            return {"status": "rejected", "reason": "candidate omits modeled boundary values", "missing": sorted(missing)}
        return {"status": "conditional_model_verified", "model": MODEL,
                "boundary_cases_checked": len(actual), "candidate_extra_cases": len(claimed - actual),
                "source_function_bodies_checked": sorted(p.checked),
                "contracts": sorted(REQUIRED_CONTRACTS),
                "whole_program_soundness_proved": False, "native_safety_proved": False,
                "end_to_end_challenges_discharged": 0,
                "interpretation": "Finite original-body normal-path argument coverage in the modeled entry/direct-forwarding activations under explicit frame/import contracts; opaque callback/reentry activations and native effects remain outside the relation, not excluded runtime behaviors. No runtime dispatch, feasibility or security theorem."}
    except (UnsupportedPolicyModel, KeyError, TypeError, ValueError, SyntaxError) as exc:
        return {"status": "unsupported", "reason": str(exc), "whole_program_soundness_proved": False, "native_safety_proved": False}


def _concrete_relations(program):
    """Evaluate each concrete bool/None input; fork unknown guards directly."""
    observations = set()
    def scalar(node, value):
        if isinstance(node, ast.Name) and node.id == program.policy:
            return value
        return _literal(node)
    def condition(node, value):
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return tuple(not b for b in condition(node.operand, value))
        if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1:
            if isinstance(node.left, ast.Name) and node.left.id == program.policy and isinstance(node.ops[0], (ast.Is, ast.IsNot)):
                same = value is _literal(node.comparators[0])
                return (not same if isinstance(node.ops[0], ast.IsNot) else same,)
            if any(isinstance(n, ast.Name) and n.id == program.policy for n in ast.walk(node)):
                raise UnsupportedPolicyModel("Concrete checker rejects nonidentity policy comparison")
        if isinstance(node, ast.Constant) and type(node.value) is bool:
            return (node.value,)
        if isinstance(node, ast.Name) and node.id == program.policy:
            raise UnsupportedPolicyModel("Concrete checker rejects policy truthiness")
        return (False, True)
    def value_at_call(call, target, value):
        matches = [k.value for k in call.keywords if k.arg == program.policy]
        if len(matches) > 1:
            raise UnsupportedPolicyModel("Duplicate policy keyword")
        if target:
            _bind_call(call, program.functions[target][1])
            args = [*program.functions[target][1].args.posonlyargs, *program.functions[target][1].args.args]
            pos = next((i for i, arg in enumerate(args) if arg.arg == program.policy), None)
            supplied = call.args[pos] if pos is not None and len(call.args) > pos else None
            if supplied is not None and matches:
                raise UnsupportedPolicyModel("Duplicate positional policy")
            if supplied is not None:
                return scalar(supplied, value)
            if not matches:
                return program.default(target)
        if not matches:
            raise UnsupportedPolicyModel("Missing explicit boundary policy")
        return scalar(matches[0], value)
    def inspect(node, value, function, input_key):
        for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
            expression = ast.unparse(call.func)
            if expression in program.links:
                target = program.links[expression]
                evaluate(program.functions[target][1].body, value_at_call(call, target, value), target, input_key)
            elif expression in program.boundaries:
                observations.add((input_key, function, call.lineno, expression, _key(value_at_call(call, None, value))))
    def evaluate(statements, value, function, input_key):
        if not statements:
            return
        program.tick()
        current, *rest = statements
        if isinstance(current, ast.If):
            inspect(current.test, value, function, input_key)
            for truth in condition(current.test, value):
                evaluate((current.body if truth else current.orelse) + rest, value, function, input_key)
        elif isinstance(current, (ast.Assign, ast.AnnAssign)):
            targets = current.targets if isinstance(current, ast.Assign) else [current.target]
            if any(not isinstance(t, ast.Name) for t in targets):
                raise UnsupportedPolicyModel("Concrete checker rejects heap/tuple assignments")
            if any(t.id == program.policy for t in targets):
                if current.value is None:
                    raise UnsupportedPolicyModel("Concrete checker rejects annotation-only policy")
                if len(targets) != 1:
                    raise UnsupportedPolicyModel("Concrete checker rejects policy aliases")
                value = scalar(current.value, value)
            else:
                if current.value is not None:
                    inspect(current.value, value, function, input_key)
            evaluate(rest, value, function, input_key)
        elif isinstance(current, ast.With):
            for item in current.items:
                inspect(item.context_expr, value, function, input_key)
                if item.optional_vars is not None and any(isinstance(n, ast.Name) and n.id == program.policy for n in ast.walk(item.optional_vars)):
                    raise UnsupportedPolicyModel("Concrete checker rejects with policy binding")
            evaluate(current.body + rest, value, function, input_key)
        elif isinstance(current, ast.Return):
            if current.value is not None:
                inspect(current.value, value, function, input_key)
        elif isinstance(current, ast.Raise):
            if current.exc is not None:
                inspect(current.exc, value, function, input_key)
            if current.cause is not None:
                inspect(current.cause, value, function, input_key)
            return
        elif isinstance(current, (ast.Expr, ast.Pass)):
            if isinstance(current, ast.Expr):
                inspect(current.value, value, function, input_key)
            evaluate(rest, value, function, input_key)
        else:
            raise UnsupportedPolicyModel("Concrete checker rejects statement")
    for supplied in program.entry_values:
        value = program.default(program.request['entry']) if supplied == 'omitted' else supplied
        evaluate(program.functions[program.request['entry']][1].body, value, program.request['entry'], 'omitted' if supplied == 'omitted' else _key(supplied))
    return observations
