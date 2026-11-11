"""Test Flask-like patterns (decorator factories + container registration)."""

import pytest


def test_flask_route_pattern(run_analysis):
    """Test Flask's @app.route() decorator factory pattern."""
    code = """
class Flask:
    def __init__(self):
        self.routes = {}
    
    def route(self, path):
        def decorator(func):
            self.routes[path] = func
            return func
        return decorator

app = Flask()

@app.route("/hello")
def hello():
    return "Hello"

@app.route("/world")
def world():
    return "World"

# Simulate request dispatch
handler = app.routes["/hello"]
result = handler()
"""
    checker = run_analysis(code)
    # Should have call to route method (decorator factory)
    edges_route = checker.get_call_edges(callee="route")
    # Should have calls to hello and world (either from decoration or dispatch)
    edges_hello = checker.get_call_edges(callee="hello")
    edges_world = checker.get_call_edges(callee="world")
    
    assert len(edges_route) > 0, "Expected calls to route() decorator factory"
    # At minimum, hello should be called in the dispatch
    assert len(edges_hello) > 0, "Expected call to hello handler"


def test_callback_registry(run_analysis):
    """Test callback registration pattern."""
    code = """
callbacks = []

def register(func):
    callbacks.append(func)
    return func

@register
def callback1():
    return 1

@register
def callback2():
    return 2

# Execute callbacks
for cb in callbacks:
    result = cb()
"""
    checker = run_analysis(code)
    edges_cb1 = checker.get_call_edges(callee="callback1")
    edges_cb2 = checker.get_call_edges(callee="callback2")
    
    # Both callbacks should be called in the loop
    assert len(edges_cb1) > 0, "Expected call to callback1"
    assert len(edges_cb2) > 0, "Expected call to callback2"


def test_handler_dict_pattern(run_analysis):
    """Test handler dictionary pattern."""
    code = """
handlers = {}

def register_handler(name):
    def decorator(func):
        handlers[name] = func
        return func
    return decorator

@register_handler("start")
def start_handler():
    return "started"

@register_handler("stop")
def stop_handler():
    return "stopped"

# Dispatch
def dispatch(cmd):
    handler = handlers[cmd]
    return handler()

result = dispatch("start")
"""
    checker = run_analysis(code)
    edges_dispatch = checker.get_call_edges(callee="dispatch")
    edges_start = checker.get_call_edges(callee="start_handler")
    edges_stop = checker.get_call_edges(callee="stop_handler")
    
    assert len(edges_dispatch) > 0, "Expected call to dispatch"
    # Both handlers should be possible callees from dispatch
    assert len(edges_start) > 0 or len(edges_stop) > 0, "Expected calls to handlers"


def test_method_decorator_with_registry(run_analysis):
    """Test decorator on method that registers in container."""
    code = """
class App:
    def __init__(self):
        self.views = {}
    
    def view(self, name):
        def decorator(func):
            self.views[name] = func
            return func
        return decorator

class Controller:
    def __init__(self, app):
        self.app = app
        self.setup_routes()
    
    def setup_routes(self):
        pass  # Routes defined via decorators

app = App()
controller = Controller(app)

@app.view("index")
def index_view():
    return "index"

# Dispatch
view_func = app.views["index"]
result = view_func()
"""
    checker = run_analysis(code)
    edges_view = checker.get_call_edges(callee="index_view")
    assert len(edges_view) > 0, "Expected call to registered view"

