"""Class metadata and namespace propagation through the source pipeline."""
import pytest

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.world.pipeline import Pipeline


def analyze(tmp_path, source, **options):
    path = tmp_path / "subject.py"
    path.write_text(source)
    config = Config(log_level="ERROR", **options)
    pipeline = Pipeline(config={
        "filename": str(path), "project_path": str(tmp_path), "library_paths": [],
        "import_level": 0, "mock_libs": False, "time_count": False,
        "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                      "options": config.to_dict()}],
    })
    pipeline.run()
    query = pipeline.analysis_manager.get_results("pta").query()
    edges = {(edge.callsite.scope.stmt.get_qualname(), edge.callee.stmt.get_qualname())
             for edge in query.call_graph().edges}
    assert query.get_statistics()["converged"] == 1
    return query, edges


@pytest.mark.parametrize("context_policy", ["0-cfa", "1-cfa", "2-cfa"])
def test_metaclass_method_receives_actual_class_and_installs_member(tmp_path, context_policy):
    query, edges = analyze(tmp_path, '''
def callback(self):
    return 42
class Meta(type):
    def install(cls, fn):
        cls.action = fn
class Product(metaclass=Meta):
    pass
Product.install(callback)
Product().action()
''', context_policy=context_policy)
    assert ("subject", "subject.Meta.install") in edges
    assert ("subject", "subject.callback") in edges
    assert any("metaclass" in item["reason"] for item in query._state._residual_effects.values())


def test_metaclass_expression_is_evaluated_and_return_flows(tmp_path):
    _, edges = analyze(tmp_path, '''
def callback():
    return 42
class Meta(type):
    def run(cls, fn):
        return fn()
def choose():
    return Meta
class Product(metaclass=choose()):
    pass
Product.run(callback)
''')
    assert ("subject", "subject.choose") in edges
    assert ("subject.Meta.run", "subject.callback") in edges


@pytest.mark.parametrize("index_sensitive", [False, True])
def test_type_namespace_functions_bind_instances(tmp_path, index_sensitive):
    _, edges = analyze(tmp_path, '''
def callback():
    return 42
def action(self):
    return self.callback()
Product = type("Product", (), {"action": action})
obj = Product()
obj.callback = callback
obj.action()
''', index_sensitive=index_sensitive)
    assert ("subject", "subject.action") in edges
    assert ("subject.action", "subject.callback") in edges


def test_type_new_namespace_classmethod_retains_owner_and_callback(tmp_path):
    _, edges = analyze(tmp_path, '''
def callback():
    return 42
def verify(cls):
    return cls.callback()
class Meta(type):
    def __new__(mcls, name, bases, namespace):
        return type.__new__(mcls, name, bases, namespace)
Product = Meta("Product", (), {"verify": classmethod(verify), "callback": staticmethod(callback)})
Product.verify()
''')
    assert ("subject", "subject.Meta.__new__") in edges
    assert ("subject", "subject.verify") in edges
    assert ("subject.verify", "subject.callback") in edges


def test_dynamic_namespace_late_update_keeps_both_callbacks(tmp_path):
    _, edges = analyze(tmp_path, '''
def first(self):
    return 1
def second(self):
    return 2
namespace = {}
def install(values):
    values.update({"first": first})
    values["second"] = second
install(namespace)
Product = type("Product", (), namespace)
obj = Product()
obj.first()
obj.second()
''')
    # Namespace facts arrive after allocation in worklist order, although the
    # concrete dictionary was filled before type() copies its namespace.
    assert ("subject", "subject.first") in edges
    assert ("subject", "subject.second") in edges


def test_custom_new_nonclass_return_is_not_discarded(tmp_path):
    _, edges = analyze(tmp_path, '''
def callback():
    return 42
class Meta(type):
    def __new__(mcls, name, bases, namespace):
        return callback
Product = Meta("Product", (), {})
Product()
''')
    assert ("subject", "subject.callback") in edges


def test_inherited_metaclass_method_uses_subclass_receiver(tmp_path):
    _, edges = analyze(tmp_path, '''
def callback():
    return 42
class Meta(type):
    def run(cls):
        return cls.callback()
class Base(metaclass=Meta):
    pass
class Child(Base):
    callback = staticmethod(callback)
Child.run()
''')
    assert ("subject.Meta.run", "subject.callback") in edges


def test_dynamic_class_retains_base_instance_methods(tmp_path):
    _, edges = analyze(tmp_path, '''
def callback():
    return 42
class Base:
    def __init__(self, fn):
        self.callback = fn
    def run(self):
        return self.callback()
Product = type("Product", (Base,), {})
obj = Product(callback)
obj.run()
''')
    assert ("subject", "subject.Base.__init__") in edges
    assert ("subject", "subject.Base.run") in edges
    assert ("subject.Base.run", "subject.callback") in edges


def test_coarse_namespace_keeps_distinct_known_callback_possibilities(tmp_path):
    _, edges = analyze(tmp_path, '''
def first(self):
    return 1
def second(self):
    return 2
Product = type("Product", (), {"first": first, "second": second})
obj = Product()
obj.first()
obj.second()
''', index_sensitive=False)
    assert ("subject", "subject.first") in edges
    assert ("subject", "subject.second") in edges
