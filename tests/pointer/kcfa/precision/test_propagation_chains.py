"""Test long propagation chains and cross-boundary propagation.

These tests verify that function objects propagate correctly through:
- Long chains of copy operations
- Multi-level returns
- Cross-class boundaries
- Cross-instance boundaries  
- Cross-container boundaries
- Multiple inheritance hierarchies
"""

import pytest


def test_long_copy_chain(run_analysis):
    """Test object flows through 15+ copy operations."""
    code = """
def target():
    return 42

a = target
b = a
c = b
d = c
e = d
f = e
g = f
h = g
i = h
j = i
k = j
l = k
m = l
n = m
o = n

result = o()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="target")
    assert len(edges) > 0, "Function should propagate through long copy chain"


def test_multi_level_return_propagation(run_analysis):
    """Test functions returned through multiple levels."""
    code = """
def target():
    return 1

def level1():
    return target

def level2():
    return level1()

def level3():
    return level2()

def level4():
    return level3()

func = level4()
result = func()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="target")
    assert len(edges) > 0, "Function should propagate through 4-level returns"


def test_return_chain_with_calls(run_analysis):
    """Test return values propagating through call chain."""
    code = """
def base():
    return 1

def f1():
    return base()

def f2():
    return f1()

def f3():
    return f2()

result = f3()
"""
    checker = run_analysis(code)
    # Should have calls: f3->f2, f2->f1, f1->base
    checker.assert_call_edge("f3", "f2")
    checker.assert_call_edge("f2", "f1")
    checker.assert_call_edge("f1", "base")


def test_cross_class_function_storage(run_analysis):
    """Test function stored in class field."""
    code = """
def handler():
    return 42

class Registry:
    pass

Registry.handler = handler
func = Registry.handler
result = func()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="handler")
    assert len(edges) > 0, "Function should propagate through class field"


def test_cross_instance_function_storage(run_analysis):
    """Test function stored in instance field."""
    code = """
def callback():
    return 42

class Container:
    pass

obj = Container()
obj.callback = callback
func = obj.callback
result = func()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="callback")
    assert len(edges) > 0, "Function should propagate through instance field"


def test_cross_container_function_storage(run_analysis):
    """Test function stored in list container."""
    code = """
def func1():
    return 1

def func2():
    return 2

funcs = [func1, func2]
f = funcs[0]
result = f()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="func1")
    assert len(edges) > 0, "Function should propagate through list"


def test_nested_container_propagation(run_analysis):
    """Test function in nested containers."""
    code = """
def handler():
    return 42

registry = {'level1': {'level2': {'func': handler}}}
func = registry['level1']['level2']['func']
result = func()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="handler")
    assert len(edges) > 0, "Function should propagate through nested dicts"


def test_method_propagation_cross_instance(run_analysis):
    """Test bound method propagation."""
    code = """
class Service:
    def process(self):
        return 42

obj = Service()
method = obj.process
result = method()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="process")
    assert len(edges) > 0, "Bound method should propagate"


def test_method_in_container(run_analysis):
    """Test methods stored in containers."""
    code = """
class Worker:
    def task1(self):
        return 1
    
    def task2(self):
        return 2

worker = Worker()
tasks = [worker.task1, worker.task2]
result = tasks[0]()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="task1")
    assert len(edges) > 0, "Bound method should propagate through container"


def test_single_inheritance_method_resolution(run_analysis):
    """Test method resolution through single inheritance."""
    code = """
class Base:
    def method(self):
        return 42

class Derived(Base):
    pass

obj = Derived()
result = obj.method()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="method")
    assert len(edges) > 0, "Method should resolve through inheritance"


def test_multi_level_inheritance(run_analysis):
    """Test method resolution through 3-level inheritance."""
    code = """
class A:
    def method(self):
        return 1

class B(A):
    pass

class C(B):
    pass

obj = C()
result = obj.method()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="method")
    assert len(edges) > 0, "Method should resolve through 3-level inheritance"


def test_inherited_method_via_variable(run_analysis):
    """Test inherited method accessed via variable."""
    code = """
class Base:
    def action(self):
        return 42

class Child(Base):
    pass

obj = Child()
m = obj.action
result = m()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="action")
    assert len(edges) > 0, "Inherited method should propagate via variable"


def test_class_field_with_function(run_analysis):
    """Test class-level field containing function."""
    code = """
def implementation():
    return 42

class Service:
    handler = implementation

result = Service.handler()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="implementation")
    assert len(edges) > 0, "Function in class field should be callable"


def test_class_method_accessing_class_field(run_analysis):
    """Test classmethod accessing class field."""
    code = """
def processor():
    return 42

class Manager:
    handler = processor
    
    @classmethod
    def get_handler(cls):
        return cls.handler

func = Manager.get_handler()
result = func()
"""
    checker = run_analysis(code)
    # Should have: module->get_handler, module->processor
    edges_get = checker.get_call_edges(callee="get_handler")
    edges_proc = checker.get_call_edges(callee="processor")
    assert len(edges_get) > 0, "Should call get_handler"
    assert len(edges_proc) > 0, "Should call processor through returned function"


def test_instance_method_chain(run_analysis):
    """Test method calling another method on same instance."""
    code = """
class Calculator:
    def compute_base(self):
        return 10
    
    def compute_result(self):
        base = self.compute_base()
        return base * 2

calc = Calculator()
result = calc.compute_result()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("compute_result", "compute_base")


def test_cross_instance_method_call(run_analysis):
    """Test method calling method on different instance."""
    code = """
class Helper:
    def help(self):
        return 42

class Main:
    def __init__(self):
        self.helper = Helper()
    
    def process(self):
        return self.helper.help()

obj = Main()
result = obj.process()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("process", "help")


def test_function_in_instance_dict_field(run_analysis):
    """Test function stored in instance dict field."""
    code = """
def view():
    return "OK"

class App:
    def __init__(self):
        self.routes = {}
    
    def add_route(self, path, handler):
        self.routes[path] = handler

app = App()
app.add_route("/", view)
handler = app.routes["/"]
result = handler()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="view")
    assert len(edges) > 0, "Function should propagate through instance dict field"


def test_function_in_class_dict_field(run_analysis):
    """Test function stored in class-level dict."""
    code = """
def handler1():
    return 1

class Registry:
    handlers = {}

Registry.handlers['h1'] = handler1
func = Registry.handlers['h1']
result = func()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="handler1")
    assert len(edges) > 0, "Function should propagate through class dict field"


def test_method_stored_in_external_dict(run_analysis):
    """Test method stored in external data structure."""
    code = """
class Service:
    def handle(self):
        return 42

registry = {}
obj = Service()
registry['service'] = obj.handle

func = registry['service']
result = func()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="handle")
    assert len(edges) > 0, "Bound method should propagate through external dict"


def test_cross_function_closure_propagation(run_analysis):
    """Test function propagation through closure."""
    code = """
def target():
    return 42

def make_wrapper():
    func = target
    def wrapper():
        return func()
    return wrapper

w = make_wrapper()
result = w()
"""
    checker = run_analysis(code)
    # Should have: module->make_wrapper, wrapper->target
    checker.assert_call_edge("wrapper", "target")


def test_closure_capturing_function(run_analysis):
    """Test closure that captures and returns function."""
    code = """
def handler():
    return 42

def make_caller(f):
    def caller():
        return f()
    return caller

wrapped = make_caller(handler)
result = wrapped()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("caller", "handler")


def test_multiple_inheritance_diamond(run_analysis):
    """Test method resolution in diamond inheritance."""
    code = """
class A:
    def method(self):
        return 1

class B(A):
    pass

class C(A):
    pass

class D(B, C):
    pass

obj = D()
result = obj.method()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="method")
    assert len(edges) > 0, "Method should resolve through diamond inheritance"


def test_cross_module_style_function_import(run_analysis):
    """Test function imported and called (simulated in single module)."""
    code = """
# Simulating: from module import function
def library_function():
    return 42

imported_func = library_function

def user_code():
    return imported_func()

user_code()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("user_code", "library_function")


def test_complex_flask_like_pattern(run_analysis):
    """Test Flask-like decorator pattern with dict storage."""
    code = """
class App:
    def __init__(self):
        self.routes = {}
    
    def route(self, path):
        def decorator(func):
            self.routes[path] = func
            return func
        return decorator

app = App()

@app.route("/")
def index():
    return "OK"

# Simulate request handling
handler = app.routes["/"]
result = handler()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="index")
    assert len(edges) > 0, "View function should be callable from routes dict"


def test_factory_pattern_with_storage(run_analysis):
    """Test factory pattern with instance storage."""
    code = """
class Service:
    def process(self):
        return 42

def create_service():
    return Service()

class Container:
    def __init__(self):
        self.services = []
    
    def register(self, factory):
        service = factory()
        self.services.append(service)

container = Container()
container.register(create_service)
service = container.services[0]
result = service.process()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("register", "create_service")
    # Should also resolve process call (depends on container field tracking)


def test_callback_registry_pattern(run_analysis):
    """Test callback registry pattern."""
    code = """
def callback1():
    return 1

def callback2():
    return 2

class EventSystem:
    def __init__(self):
        self.callbacks = []
    
    def register(self, cb):
        self.callbacks.append(cb)
    
    def trigger(self):
        for cb in self.callbacks:
            cb()

system = EventSystem()
system.register(callback1)
system.register(callback2)
system.trigger()
"""
    checker = run_analysis(code)
    # Should resolve callback calls in trigger
    edges_cb1 = checker.get_call_edges(callee="callback1")
    edges_cb2 = checker.get_call_edges(callee="callback2")
    # At minimum, should have trigger calling callbacks
    assert len(edges_cb1) > 0 or len(edges_cb2) > 0, "Should resolve some callback calls"

