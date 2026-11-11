"""Independent call-preservation attacks against forward guard activation."""
import pytest

from tests.pointer.kcfa.test_ai_bridge import analyze_source


CASES = [
    ('''def choose(flag):
    if flag:
        flag = False
        if flag:
            no()
        else:
            yes()
choose(True)
''', ("subject.choose", "subject.yes")),
    ('''flag = False
def mutate():
    global flag
    flag = True
def choose():
    mutate()
    if flag:
        yes()
    else:
        no()
choose()
''', ("subject.choose", "subject.yes")),
    ('''def outer():
    flag = False
    def mutate():
        nonlocal flag
        flag = True
    def choose():
        mutate()
        if flag:
            yes()
        else:
            no()
    choose()
outer()
''', ("subject.outer.choose", "subject.yes")),
    ('''def identity(value):
    return value
def choose(flag):
    if identity(flag):
        yes()
    else:
        no()
choose(True)
''', ("subject.choose", "subject.yes")),
    ('''def choose(flag):
    if flag:
        selected = yes
    else:
        selected = no
    selected()
choose(True)
''', ("subject.choose", "subject.yes")),
    ('''def choose(flag):
    if flag:
        if flag:
            yes()
        else:
            no()
choose(True)
''', ("subject.choose", "subject.yes")),
    ('''def choose(flag):
    if flag:
        return yes()
    else:
        return no()
choose(1e999)
''', ("subject.choose", "subject.yes")),
]


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("body,required", CASES)
def test_guard_activation_preserves_concrete_call_after_mutation_or_binding(tmp_path, policy, body, required):
    source = "def yes(): return 'yes'\ndef no(): return 'no'\n" + body
    namespace = {}
    exec(source, namespace)
    query, edges = analyze_source(tmp_path, source, context_policy=policy, enable_ai_intrinsics=False)
    assert query.get_statistics()["converged"] == 1
    assert required in edges, (source, required, edges)


@pytest.mark.parametrize("expression", [
    "1 + 1", "1 < 2", "len([1])", "bool(1)", "int('1')", "any([True])", "eval('True')", "-1", "not False", "f'{1}'", "(x := 1)", "(True and True)", "(1 if True else 0)",
])
def test_known_false_does_not_hide_a_second_unmodeled_truthy_producer(tmp_path, expression):
    source = f'''def yes(): return 'yes'
def no(): return 'no'
def choose():
    flag = False
    flag = {expression}
    if flag:
        yes()
    else:
        no()
choose()
'''
    namespace = {}
    exec(source, namespace)
    query, edges = analyze_source(tmp_path, source, enable_ai_intrinsics=False, context_policy="0-cfa")
    assert ("subject.choose", "subject.yes") in edges, (expression, query.get_semantic_status() if hasattr(query, "get_semantic_status") else "", edges)
