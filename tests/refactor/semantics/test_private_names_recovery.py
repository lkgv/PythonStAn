import ast,subprocess,sys,json
import pytest
from pythonstan import Source,ValueQuery,analyze
from pythonstan.frontend.private_names import lower_private_names

CASES=[
("class C:\n    def __init__(self):self.__x=7\nresult=getattr(C(),'_C__x','missing')",7),
("class C:\n    __x=8\nresult=C.__dict__['_C__x']",8),
("class C:\n    def __f(self):return 9\n    def call(self):return self.__f()\nresult=C().call()",9),
("class C:\n    def f(self,__x):return __x\nresult=C().f(10)",10),
("class C:\n    def f(self,__x):return __x\nresult=C().f(_C__x=11)",11),
("class C:\n    def f(self,__x):return __x\n    def g(self):\n        try:return self.f(__x=3)\n        except TypeError:return 'literal-keyword'\nresult=C().g()",'literal-keyword'),
("class C:\n    def f(self):\n        global __x\n        __x=12\nC().f()\nresult=_C__x",12),
("class ___:\n    def __init__(self):self.__x=13\nresult=getattr(___(),'__x')",13),
]
@pytest.mark.parametrize('source,expected',CASES)
def test_private_bindings_follow_python_compiler_scope(source,expected):
    concrete=subprocess.run([sys.executable,'-c',source+'\nimport json\nprint(json.dumps(result))'],text=True,capture_output=True,check=True)
    assert json.loads(concrete.stdout)==expected
    q=ValueQuery('subject','result');r=analyze(Source(source,module='subject'),config={'engine':'ai_primary'},queries=[q])
    value=r.query(q).value
    assert value['constants']==[expected] and not value['top'] and not value['top_sorts'] and not value['may_unbound'],value


def test_pass_does_not_mutate_literals_keywords_or_display_names():
    tree=lower_private_names(ast.parse("class C:\n    def __f(self,__x):return getattr(self,'__x')\n    def g(self):return self.__f(__x=1)"))
    cls=tree.body[0];fn=cls.body[0];call=cls.body[1].body[0].value
    assert fn.name=='_C__f' and fn._source_declared_name=='__f' and fn.args.args[1].arg=='_C__x'
    assert call.func.attr=='_C__f' and call.keywords[0].arg=='__x'
    assert fn.body[0].value.args[1].value=='__x'


def test_raw_future_annotation_text_survives_identifier_lowering():
    tree=lower_private_names(ast.parse("class C:\n    def f(self, __x: __T) -> __R: pass\n    __x: list[__T]"))
    cls=tree.body[0];fn=cls.body[0];assignment=cls.body[1]
    assert fn.args.args[1].annotation.id=='_C__T'
    assert fn.args.args[1].annotation._source_annotation_text=='__T'
    assert fn.returns._source_annotation_text=='__R'
    assert assignment.annotation._source_annotation_text=='list[__T]'
