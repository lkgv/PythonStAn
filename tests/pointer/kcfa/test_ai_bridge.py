"""Exercise live KCFA/AI integration through the source preprocessing pipeline."""

import pytest

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.world.pipeline import Pipeline


def analyze_source(tmp_path, source, **options):
    filename = tmp_path / "subject.py"
    filename.write_text(source)
    config = Config(max_iterations=options.pop("max_iterations", 50000), log_level="ERROR", **options)
    pipeline = Pipeline(config={
        "filename": str(filename), "project_path": str(tmp_path),
        "library_paths": [], "import_level": 0, "mock_libs": False,
        "time_count": False,
        "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                      "options": config.to_dict()}],
    })
    pipeline.run()
    query = pipeline.analysis_manager.get_results("pta").query()
    edges = {(edge.callsite.scope.stmt.get_qualname(), edge.callee.stmt.get_qualname())
             for edge in query.call_graph().edges}
    return query, edges


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("index_sensitive", [False, True])
def test_dynamic_configuration_getattr_uses_live_bridge(tmp_path, enabled, index_sensitive):
    query, edges = analyze_source(tmp_path, '''
class Service:
    def handle(self):
        return "handled"
class Configuration:
    handler = "handle"
def dispatch(service, config):
    callback = getattr(service, config.handler)
    return callback()
result = dispatch(Service(), Configuration())
''', enable_ai_intrinsics=enabled, index_sensitive=index_sensitive)

    assert ("subject.dispatch", "subject.Service.handle") in edges
    stats = query.get_statistics()
    assert stats["converged"] == 1
    assert (stats["ai_intrinsic_attempts"] > 0) == enabled
    assert (stats["ai_intrinsic_applied"] > 0) == enabled
    assert stats["builtin_dependency_replays"] > 0


def test_effect_only_setattr_keeps_callback_flow(tmp_path):
    query, edges = analyze_source(tmp_path, '''
def callback():
    return "done"
def attribute_name():
    return "callback"
class Box:
    pass
box = Box()
setattr(box, attribute_name(), callback)
getattr(box, attribute_name())()
''')

    assert ("subject", "subject.callback") in edges
    assert query.get_statistics()["ai_intrinsic_attempts"] > 0


@pytest.mark.parametrize("index_sensitive", [False, True])
def test_bound_async_callable_preserves_receiver_and_variadic_callback(tmp_path, index_sensitive):
    source = '''
events = []
def callback(message):
    events.append(message)
    return message
class Worker:
    def __init__(self, function):
        self.function = function
    async def __call__(self, *args, **kwargs):
        return self.function(*args, **kwargs)
worker = Worker(callback)
pending = worker("payload")
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["events"] == []
    try:
        namespace["pending"].send(None)
    except StopIteration:
        pass
    assert namespace["events"] == ["payload"]
    _, dormant = analyze_source(tmp_path, source, index_sensitive=index_sensitive)
    assert ("subject.Worker.__call__", "subject.callback") not in dormant
    source += "\ntry:\n    pending.send(None)\nexcept StopIteration:\n    pass\n"
    query, edges = analyze_source(tmp_path, source, index_sensitive=index_sensitive)

    assert ("subject.Worker.__call__", "subject.callback") in edges
    assert query.get_statistics()["converged"] == 1


@pytest.mark.parametrize("index_sensitive", [False, True])
def test_index_modes_preserve_container_method_and_value_flows(tmp_path, index_sensitive):
    _, edges = analyze_source(tmp_path, '''
def first():
    return 1
def second():
    return 2
callbacks = []
callbacks.append(first)
routes = {"primary": second}
callbacks[0]()
routes["primary"]()
''', index_sensitive=index_sensitive)
    assert ("subject", "subject.first") in edges
    assert ("subject", "subject.second") in edges


def test_bound_method_keeps_global_callback_capture(tmp_path):
    _, edges = analyze_source(tmp_path, '''
def callback():
    return "done"
class Worker:
    def run(self):
        return callback()
Worker().run()
''')

    assert ("subject.Worker.run", "subject.callback") in edges


def test_zero_cfa_selects_context_objects(tmp_path):
    query, edges = analyze_source(tmp_path, '''
def callback():
    return 1
class Worker:
    def run(self):
        return callback()
Worker().run()
''', context_policy="0-cfa")

    assert ("subject.Worker.run", "subject.callback") in edges
    assert query.get_statistics()["converged"] == 1


@pytest.mark.parametrize("context_policy", ["0-cfa", "1-cfa", "2-cfa"])
@pytest.mark.parametrize("index_sensitive", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_decorator_registry_survives_coarse_allocation_reuse(
    tmp_path, context_policy, index_sensitive, enabled,
):
    source = '''
class Registry:
    def __init__(self):
        self.callbacks = {}
    def register(self, name):
        def decorate(callback):
            self.callbacks[name] = callback
            return callback
        return decorate
    def dispatch(self):
        return self.callbacks["active"]()
registry = Registry()
@registry.register("active")
def selected_callback():
    return "selected"
result = registry.dispatch()
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["result"] == "selected"
    query, edges = analyze_source(
        tmp_path, source, context_policy=context_policy,
        index_sensitive=index_sensitive, enable_ai_intrinsics=enabled,
    )

    assert ("subject", "subject.Registry.register.decorate") in edges
    assert ("subject.Registry.dispatch", "subject.selected_callback") in edges
    assert query.get_statistics()["converged"] == 1


@pytest.mark.parametrize("context_policy", ["0-cfa", "1-cfa", "2-cfa"])
@pytest.mark.parametrize("kind", ["async", "generator"])
@pytest.mark.parametrize("mutual", [False, True])
def test_recursive_suspended_objects_have_bounded_identity(tmp_path, context_policy, kind, mutual):
    other = "second" if mutual else "first"
    source = "events = []\n"
    if kind == "async":
        source += f"async def first(depth=2):\n    events.append('first')\n    if depth:\n        return await {other}(depth - 1)\n    return None\n"
        if mutual:
            source += "async def second(depth):\n    events.append('second')\n    return await first(depth - 1)\n"
    else:
        source += f"def first():\n    events.append('first')\n    yield {other}()\n"
        if mutual:
            source += "def second():\n    events.append('second')\n    yield first()\n"
    source += "pending = first()\n"
    namespace = {}
    exec(source, namespace)
    assert namespace["events"] == []
    if kind == "async":
        try:
            namespace["pending"].send(None)
        except StopIteration:
            pass
        resume = "try:\n    pending.send(None)\nexcept StopIteration:\n    pass\n"
    else:
        cursor = namespace["pending"]
        for _ in range(3):
            cursor = next(cursor)
        resume = "cursor = pending\nfor step in range(3):\n    cursor = next(cursor)\n"
    assert namespace["events"] == (["first", "second", "first"] if mutual else ["first"] * 3)
    _, dormant = analyze_source(tmp_path, source, context_policy=context_policy,
        enable_ai_intrinsics=False, max_iterations=1000)
    assert ("subject.first", "subject.second" if mutual else "subject.first") not in dormant
    source += resume
    query, edges = analyze_source(
        tmp_path, source, context_policy=context_policy,
        enable_ai_intrinsics=False, max_iterations=1000,
    )

    expected = {("subject", "subject.first"),
                ("subject.first", "subject.second" if mutual else "subject.first")}
    if mutual:
        expected.add(("subject.second", "subject.first"))
    assert expected <= edges
    stats = query.get_statistics()
    assert stats["converged"] == 1
    assert stats["iteration_limit_reached"] == 0
    objects = {edge.callee.obj for edge in query.call_graph().edges}
    assert len(objects) <= (2 if mutual else 1) * (int(context_policy[0]) + 2)
    if kind == "async":
        # Preserve the original unconditional recursive hardcase separately
        # from the finite CPython companion above. Never execute this program.
        unconditional = f"async def first():\n    return await {other}()\n"
        if mutual:
            unconditional += "async def second():\n    return await first()\n"
        unconditional += "pending = first()\npending.send(None)\n"
        hard_query, hard_edges = analyze_source(
            tmp_path, unconditional, context_policy=context_policy,
            enable_ai_intrinsics=False, max_iterations=1000)
        assert expected <= hard_edges
        hard_stats = hard_query.get_statistics()
        assert hard_stats["converged"] == 1
        assert hard_stats["iteration_limit_reached"] == 0
        hard_objects = {edge.callee.obj for edge in hard_query.call_graph().edges}
        assert len(hard_objects) <= (2 if mutual else 1) * (int(context_policy[0]) + 2)


@pytest.mark.parametrize("kind", ["async", "generator"])
def test_merged_suspended_calls_keep_all_caller_arguments(tmp_path, kind):
    declaration = "async def" if kind == "async" else "def"
    operation = "return" if kind == "async" else "yield"
    source = f'''
events = []
def first_callback():
    events.append("first")
    return "first"
def second_callback():
    events.append("second")
    return "second"
{declaration} invoke(callback):
    {operation} callback()
def first_caller():
    return invoke(first_callback)
def second_caller():
    return invoke(second_callback)
left = first_caller()
right = second_caller()
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["events"] == []
    for name in ("left", "right"):
        try:
            namespace[name].send(None) if kind == "async" else next(namespace[name])
        except StopIteration:
            pass
    assert namespace["events"] == ["first", "second"]
    _, dormant = analyze_source(tmp_path, source, context_policy="0-cfa",
        enable_ai_intrinsics=False, max_iterations=1000)
    assert ("subject.invoke", "subject.first_callback") not in dormant
    assert ("subject.invoke", "subject.second_callback") not in dormant
    # Each coroutine can complete by raising StopIteration; use separate calls.
    for name in ("left", "right"):
        action = f"{name}.send(None)" if kind == "async" else f"next({name})"
        source += f"try:\n    {action}\nexcept StopIteration:\n    pass\n"
    query, edges = analyze_source(tmp_path, source, context_policy="0-cfa",
        enable_ai_intrinsics=False, max_iterations=1000)

    assert ("subject.invoke", "subject.first_callback") in edges
    assert ("subject.invoke", "subject.second_callback") in edges
    assert query.get_statistics()["converged"] == 1


@pytest.mark.parametrize("kind", ["async", "generator"])
def test_suspended_function_keeps_defining_capture(tmp_path, kind):
    declaration = "async def" if kind == "async" else "def"
    operation = "return" if kind == "async" else "yield"
    source = f'''
events = []
def selected():
    events.append("selected")
    return "selected"
def caller_local():
    return "caller"
def factory(callback):
    {declaration} invoke():
        {operation} callback()
    return invoke
def launch(function):
    callback = caller_local
    return function()
pending = launch(factory(selected))
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["events"] == []
    try:
        namespace["pending"].send(None) if kind == "async" else next(namespace["pending"])
    except StopIteration:
        pass
    assert namespace["events"] == ["selected"]
    _, dormant = analyze_source(tmp_path, source, enable_ai_intrinsics=False, max_iterations=1000)
    assert ("subject.factory.invoke", "subject.selected") not in dormant
    resume = "pending.send(None)" if kind == "async" else "next(pending)"
    source += f"try:\n    {resume}\nexcept StopIteration:\n    pass\n"
    query, edges = analyze_source(tmp_path, source, enable_ai_intrinsics=False, max_iterations=1000)

    assert ("subject.factory.invoke", "subject.selected") in edges
    assert ("subject.factory.invoke", "subject.caller_local") not in edges
    assert query.get_statistics()["converged"] == 1


@pytest.mark.parametrize("entry_points, expected", [([], False), (["bootstrap"], True), (["subject.bootstrap"], True)])
def test_explicit_entry_points_seed_uninvoked_function(tmp_path, entry_points, expected):
    _, edges = analyze_source(tmp_path, '''
def callback():
    return 1
def bootstrap():
    return callback()
''', entry_points=entry_points)

    assert (("subject.bootstrap", "subject.callback") in edges) == expected


def test_finite_dictionary_keys_keep_string_and_numeric_entries_separate(tmp_path):
    _, edges = analyze_source(tmp_path, '''
def selected():
    return "selected"
def other():
    return "other"
handlers = {"1": selected, 1: other}
handlers["1"]()
''')
    assert ("subject", "subject.selected") in edges
    assert ("subject", "subject.other") not in edges


@pytest.mark.parametrize("lookup", ["True", "1", "1.0"])
def test_numeric_dictionary_keys_use_python_equality(tmp_path, lookup):
    _, edges = analyze_source(tmp_path, '''
def selected():
    return "selected"
handlers = {1: selected}
handlers[''' + lookup + ''']()
''')
    assert ("subject", "subject.selected") in edges


@pytest.mark.parametrize("name_expression", ["input()", '"first" if input() else input()'])
def test_unknown_or_mixed_dictionary_key_retains_all_targets(tmp_path, name_expression):
    _, edges = analyze_source(tmp_path, '''
def first():
    return 1
def second():
    return 2
handlers = {"first": first, "second": second}
name = ''' + name_expression + '''
handlers[name]()
''')
    assert ("subject", "subject.first") in edges
    assert ("subject", "subject.second") in edges


def test_unknown_key_write_reaches_later_constant_read(tmp_path):
    _, edges = analyze_source(tmp_path, '''
def first():
    return 1
def second():
    return 2
def replacement():
    return 3
handlers = {"first": first, "second": second}
handlers[input()] = replacement
handlers["first"]()
''')
    assert ("subject", "subject.first") in edges
    assert ("subject", "subject.replacement") in edges
    assert ("subject", "subject.second") not in edges


@pytest.mark.parametrize("mutation", ['handlers.update({"second": second})', 'handlers.update(second=second)'])
def test_dictionary_update_preserves_known_key_partition(tmp_path, mutation):
    _, edges = analyze_source(tmp_path, '''
def first():
    return 1
def second():
    return 2
handlers = {"first": first}
''' + mutation + '''
handlers["first"]()
''')
    assert ("subject", "subject.first") in edges
    assert ("subject", "subject.second") not in edges


def test_dictionary_update_transfers_unknown_key_writes(tmp_path):
    _, edges = analyze_source(tmp_path, '''
def callback():
    return 1
source = {}
source[input()] = callback
handlers = {}
handlers.update(source)
handlers["any"]()
''')
    assert ("subject", "subject.callback") in edges


@pytest.mark.parametrize("construction, mutation", [('{**source}', ''), ('{}', 'handlers.update(selected=callback)')])
def test_dictionary_unpack_and_keyword_update_preserve_callback(tmp_path, construction, mutation):
    _, edges = analyze_source(tmp_path, '''
def callback():
    return 1
source = {"selected": callback}
handlers = ''' + construction + '''
''' + mutation + '''
handlers["selected"]()
''')
    assert ("subject", "subject.callback") in edges
