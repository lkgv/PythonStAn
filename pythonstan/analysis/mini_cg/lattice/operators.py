from .value import Value


class Operators:
    def type_of(self, v: Value):
        maybe_bool = not v.is_not_bool()
        maybe_int = not v.is_not_int()