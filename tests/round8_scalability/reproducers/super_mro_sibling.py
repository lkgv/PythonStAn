"""Known open may-call miss: B.value must reach C.value on a D receiver.

This is an explicit counterexample fixture, not a passing regression or a
benchmark success. Production analysis reads it without executing the source.
"""


class A:
    def value(self):
        return 1


class B(A):
    def value(self):
        return super().value()


class C(A):
    def value(self):
        return 2


class D(B, C):
    pass


result = D().value()
