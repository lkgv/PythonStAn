"""Source-grounded reductions, never substitutes for whole-source experiments.

These tests lower trusted reductions without executing target projects. They
exercise durable closure facts and representation lifetime, not API dispatch,
framework summaries, exhaustive external arguments, or generator resumption.
"""
import ast

import pytest

from pythonstan.analysis import AnalysisConfig
from pythonstan.analysis.closure import ClosureAnalysis
from pythonstan.ir import IRModule
from pythonstan.world import World
from pythonstan.world.analysis_manager import AnalysisManager
from pythonstan.world.config import Config
from tests.analysis.legacy_ai_support import isolated_world


REDUCTIONS = {
    "fastapi": '''
class Application:
    def api_route(self, path, dependencies):
        def decorator(func):
            self.router.add_api_route(path, func, dependencies=dependencies)
            return func
        return decorator
''',
    "langserve": '''
def add_routes(app, api_handler, dependencies, enabled):
    if enabled:
        @app.post('/invoke', dependencies=dependencies)
        async def invoke(request):
            return await api_handler.invoke(request)
    return app
''',
    "django": '''
def lazy(func):
    class Proxy:
        def __init__(self, args):
            self._args = args
        def cast(self):
            return func(*self._args)
    for method_name in ('upper', 'lower'):
        def __wrapper__(self, *args, __method_name=method_name, **kwargs):
            return getattr(func(*self._args), __method_name)(*args, **kwargs)
        setattr(Proxy, method_name, __wrapper__)
    def __wrapper__(*args):
        return Proxy(args)
    return __wrapper__
''',
    "agno": '''
def combine(lifespans):
    @asynccontextmanager
    async def combined(app):
        async def nested(index):
            if index >= len(lifespans):
                yield
                return
            async with lifespans[index](app):
                async for value in nested(index + 1):
                    yield value
        async for value in nested(0):
            yield value
    return combined
''',
}


def lower(tmp_path, sources):
    path = tmp_path / "subject.py"
    path.write_text(next(iter(sources.values())))
    World.setup()
    world = World()
    world.build(Config(str(path), str(tmp_path), mock_libs=False))
    manager = AnalysisManager()
    manager.build([])
    world.analysis_manager = manager
    modules = []
    for name, source in sources.items():
        module = IRModule(name, ast.parse(source), name, str(tmp_path / (name + '.py')))
        world.scope_manager.scopes.add(module)
        world.scope_manager.names2scope[name] = module
        for phase in ("three address", "ir", "block cfg", "cfg"):
            manager.analysis(phase, module)
        modules.append(module)
    return world, modules


def closure():
    return ClosureAnalysis(AnalysisConfig("closure", "Closure", options={"type": "closure analysis"}))


def snapshot(world):
    # Qualified name alone would hide the very alias defect under test.
    return {(s.get_qualname(), getattr(s.get_ast(), "lineno", 0)): frozenset(s.cell_vars)
            for s in world.scope_manager.get_scopes()}


@pytest.mark.parametrize("project", sorted(REDUCTIONS))
def test_module_local_closure_matches_global_and_keeps_canonical_ir(tmp_path, project):
    with isolated_world():
        world, modules = lower(tmp_path, {project: REDUCTIONS[project],
                                         "sibling": "def make(other):\n def leaf(): return other\n return leaf\n"})
        analysis = closure()
        analysis.analyze(modules[0], {})
        expected = snapshot(world)
        canonical = {(s, fmt): world.scope_manager.get_ir(s, fmt)
                     for s in world.scope_manager.get_scopes() for fmt in ("ir", "block cfg")}
        for s in world.scope_manager.get_scopes():
            s.cell_vars = set()
        for module in modules:
            analysis.analyze_module(module)
        assert snapshot(world) == expected
        assert all(world.scope_manager.get_ir(s, fmt) is value
                   for (s, fmt), value in canonical.items())
        assert analysis.liveness_analysis.results == {"per_scope": {}}
        names = world.scope_manager.names2scope
        if project == "fastapi":
            assert {"self", "path", "dependencies"} <= names["fastapi.Application.api_route.decorator"].cell_vars
        elif project == "langserve":
            assert {"api_handler"} <= names["langserve.add_routes.invoke"].cell_vars
        elif project == "django":
            assert {"func"} <= names["django.lazy.Proxy.cast"].cell_vars
            wrappers = sorted((s for s in world.scope_manager.get_scopes()
                               if s.get_qualname() == "django.lazy.__wrapper__"), key=lambda s: s.get_ast().lineno)
            assert len(wrappers) == 2
            assert {"func"} <= wrappers[0].cell_vars
            assert "__method_name" not in wrappers[0].cell_vars
            assert {"Proxy"} <= wrappers[1].cell_vars
        elif project == "agno":
            assert {"lifespans", "app", "nested"} <= names["agno.combine.combined.nested"].cell_vars
        assert names["sibling.make.leaf"].cell_vars == {"other"}


def test_conditional_same_name_definitions_keep_distinct_closure_bodies(tmp_path):
    source = '''ambient = 1
def factory(token, flag):
    if flag:
        def endpoint(): return token
    else:
        def endpoint(): return ambient
    return endpoint
'''
    with isolated_world():
        world, modules = lower(tmp_path, {"subject": source})
        endpoints = sorted((s for s in world.scope_manager.get_scopes()
                            if s.get_qualname() == "subject.factory.endpoint"), key=lambda s: s.get_ast().lineno)
        assert len(endpoints) == 2
        for fmt in ("ir", "cfg", "block cfg"):
            assert world.scope_manager.get_ir(endpoints[0], fmt) is not world.scope_manager.get_ir(endpoints[1], fmt)
        closure().analyze_module(modules[0])
        assert endpoints[0].cell_vars == {"token"}
        assert endpoints[1].cell_vars == {"ambient"}


def test_explicit_class_cell_synthetic_semantic_adjunct(tmp_path):
    # Synthetic adjunct: tests the __class__ cell explicitly, not a claim that
    # the Django reduction models arbitrary descriptors or zero-arg super.
    source = "def factory(token):\n class Proxy:\n  def value(self): return token, __class__\n return Proxy\n"
    with isolated_world():
        world, modules = lower(tmp_path, {"adjunct": source})
        closure().analyze_module(modules[0])
        assert {"token", "__class__"} <= world.scope_manager.names2scope["adjunct.factory.Proxy.value"].cell_vars
