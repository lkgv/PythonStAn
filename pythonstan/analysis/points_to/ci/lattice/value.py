from abc import ABC, abstractmethod
from typing import Optional, Set, Union, Collection

from .obj_label import ObjLabel
from .property import Property


class VBool(ABC):
    @abstractmethod
    def is_maybe_any_bool(self) -> bool:
        ...

    @abstractmethod
    def is_maybe_true_but_not_false(self) -> bool:
        ...

    @abstractmethod
    def is_maybe_false_but_not_true(self) -> bool:
        ...

    @abstractmethod
    def is_maybe_true(self) -> bool:
        ...

    @abstractmethod
    def is_maybe_false(self) -> bool:
        ...

    @abstractmethod
    def is_not_bool(self) -> bool:
        ...

    @abstractmethod
    def is_maybe_other_than_bool(self) -> bool:
        ...

    @abstractmethod
    def join_any_bool(self) -> 'Value':
        ...

    @abstractmethod
    def join_bool(self, v: 'VBool') -> 'Value':
        ...

    @abstractmethod
    def join_literal_bool(self, v: bool) -> 'Value':
        ...

    @abstractmethod
    def restrict_to_not_bool(self) -> 'Value':
        ...


class VInt(ABC):
    @abstractmethod
    def is_maybe_any_int(self) -> bool:
        ...

    @abstractmethod
    def is_maybe_single_int(self) -> bool:
        ...

    @abstractmethod
    def is_maybe_zero(self) -> bool:
        ...

    @abstractmethod
    def is_not_int(self) -> bool:
        ...

    @abstractmethod
    def is_zero(self) -> bool:
        ...

    @abstractmethod
    def get_int(self) -> float:
        ...

    @abstractmethod
    def join_any_int(self) -> 'Value':
        ...

    @abstractmethod
    def join_int(self, v: 'VInt') -> 'Value':
        ...

    @abstractmethod
    def restrict_to_int(self) -> 'Value':
        ...

    @abstractmethod
    def restrict_to_not_int(self) -> 'Value':
        ...

    @abstractmethod
    def restrict_to_not_zero(self) -> 'Value':
        ...


class VFloat(ABC):
    @abstractmethod
    def is_maybe_any_float(self) -> bool:
        ...

    @abstractmethod
    def is_maybe_single_float(self) -> bool:
        ...

    @abstractmethod
    def is_maybe_zero(self) -> bool:
        ...

    @abstractmethod
    def is_not_float(self) -> bool:
        ...

    @abstractmethod
    def is_zero(self) -> bool:
        ...

    @abstractmethod
    def get_float(self) -> float:
        ...

    @abstractmethod
    def join_any_float(self) -> 'Value':
        ...

    @abstractmethod
    def join_float(self, v: 'VFloat') -> 'Value':
        ...

    @abstractmethod
    def restrict_to_float(self) -> 'Value':
        ...

    @abstractmethod
    def restrict_to_not_float(self) -> 'Value':
        ...

    @abstractmethod
    def restrict_to_not_zero(self) -> 'Value':
        ...


class VStr(ABC):
    @abstractmethod
    def is_maybe_any_str(self) -> bool:
        ...

    @abstractmethod
    def is_maybe_single_str(self) -> bool:
        ...

    @abstractmethod
    def get_str(self) -> str:
        ...

    @abstractmethod
    def is_not_str(self) -> bool:
        ...

    @abstractmethod
    def join_any_str(self) -> 'Value':
        ...

    @abstractmethod
    def join_str(self, v: 'VStr') -> 'Value':
        ...

    @abstractmethod
    def restrict_to_str(self) -> 'Value':
        ...

    @abstractmethod
    def is_maybe_str(self, s: str) -> bool:
        ...


class VNone(ABC):
    @abstractmethod
    def is_maybe_none(self) -> bool:
        ...

    @abstractmethod
    def is_not_none(self) -> bool:
        ...

    @abstractmethod
    def is_maybe_other_than_none(self) -> bool:
        ...

    @abstractmethod
    def join_none(self) -> 'Value':
        ...

    @abstractmethod
    def restrict_to_not_none(self) -> 'Value':
        ...

    @abstractmethod
    def restrict_to_null(self) -> 'Value':
        ...


class VUndef(ABC):
    @abstractmethod
    def is_maybe_undef(self) -> bool:
        ...

    @abstractmethod
    def is_not_undef(self) -> bool:
        ...

    @abstractmethod
    def is_maybe_other_than_undef(self) -> bool:
        ...

    @abstractmethod
    def join_undef(self) -> 'Value':
        ...

    @abstractmethod
    def restrict_to_undef(self) -> 'Value':
        ...

    @abstractmethod
    def restrict_to_not_undef(self) -> 'Value':
        ...


class UnknownValueResolver:
    ...


BOOL_TRUE = 0x0000_0001
BOOL_FALSE = 0x0000_0002
UNDEF = 0x0000_0004
NONE = 0x0000_0008
STR_OTHER = 0x0000_0200
FLOAT_OTHER = 0x0000_1000
INT_OTHER = 0x0000_8000

ATTR_DONTENUM = 0x0001_0000
ATTR_NOTDONTENUM = 0x0002_0000
ATTR_READONLY = 0x0004_0000
ATTR_NOTREADONLY = 0x0008_0000
ATTR_DONTDELETE = 0x0010_0000
ATTR_NOTDONTDELETE = 0x0020_0000

MODIFIED = 0x0100_0000
ABSENT = 0x0200_0000
PRESENT_DATA = 0x0400_0000
PRESENT_ACCESOR = 0x0800_0000
UNKNOWN = 0x1000_0000
INT_ZERO = 0x4000_0000
FLOAT_ZERO = 0x8000_0000

BOOL = BOOL_TRUE | BOOL_FALSE
STR = STR_OTHER
INT = INT_OTHER
FLOAT = FLOAT_OTHER
ATTR_DONTENUM_ANY = ATTR_DONTENUM | ATTR_NOTDONTENUM
ATTR_READONLY_ANY = ATTR_READONLY | ATTR_NOTREADONLY
ATTR_DONTDELETE_ANY = ATTR_DONTDELETE | ATTR_NOTDONTDELETE
ATTR = ATTR_DONTENUM_ANY | ATTR_READONLY_ANY | ATTR_DONTDELETE_ANY
PROPERTYDATA = ATTR | MODIFIED
META = ABSENT | PROPERTYDATA | PRESENT_DATA | PRESENT_ACCESOR
PRIMITIVE = UNDEF | NONE | BOOL | INT | FLOAT | STR


class Value(VUndef, VNone, VBool, VInt, VFloat, VStr):
    the_none: 'Value'
    the_none_modified: 'Value'
    the_undef: 'Value'
    the_bool_true: 'Value'
    the_bool_false: 'Value'
    the_bool_any: 'Value'
    the_str_any: 'Value'
    the_int_any: 'Value'
    the_float_any: 'Value'
    the_absent: 'Value'
    the_absent_modified: 'Value'
    the_unknown: 'Value'

    @staticmethod
    def canonicalize(v: 'Value') -> 'Value':
        v.locked = True
        return v

    @classmethod
    def really_make_none(cls) -> 'Value':
        return cls()

    @classmethod
    def make_none(cls) -> 'Value':
        return cls.the_none

    @classmethod
    def really_make_none_modified(cls) -> 'Value':
        return cls().join_modified()

    @classmethod
    def make_none_modified(cls) -> 'Value':
        return cls.the_none_modified

    @classmethod
    def really_make_undef(cls, v: Optional['Value'] = None) -> 'Value':
        r = cls(v)
        r.flags |= UNDEF
        return cls.canonicalize(r)

    @classmethod
    def make_undef(cls) -> 'Value':
        return cls.the_undef

    @classmethod
    def really_make_bool(cls, b: Optional[bool] = None) -> 'Value':
        ret = cls()
        if b is None:
            ret.flags |= BOOL
        elif b:
            ret.flags |= BOOL_TRUE
        else:
            ret.flags |= BOOL_FALSE
        return cls.canonicalize(ret)

    @classmethod
    def make_any_bool(cls) -> 'Value':
        return cls.the_bool_any

    @classmethod
    def make_bool(cls, b: Union[bool, VBool]) -> 'Value':
        if isinstance(b, VBool):
            if b.is_maybe_any_bool():
                return cls.the_bool_any
            elif b.is_maybe_true_but_not_false():
                return cls.the_bool_true
            elif b.is_maybe_false_but_not_true():
                return cls.the_bool_false
        else:
            return cls.the_bool_true if b else cls.the_bool_false

    @classmethod
    def really_make_any_str(cls) -> 'Value':
        ret = cls()
        ret.flags |= STR
        return cls.canonicalize(ret)

    @classmethod
    def make_any_str(cls) -> 'Value':
        return cls.the_str_any

    @classmethod
    def make_str(cls, s: str) -> 'Value':
        ret = Value()
        ret.str = s
        return cls.canonicalize(ret)

    @classmethod
    def really_make_any_int(cls) -> 'Value':
        ret = Value()
        ret.flags = INT
        return cls.canonicalize(ret)

    @classmethod
    def make_int(cls, i: int) -> 'Value':
        ret = cls()
        ret.int_num = i
        return cls.canonicalize(ret)

    @classmethod
    def make_any_int(cls) -> 'Value':
        return cls.the_int_any

    @classmethod
    def really_make_any_float(cls) -> 'Value':
        ret = Value()
        ret.flags = FLOAT
        return cls.canonicalize(ret)

    @classmethod
    def make_float(cls, i: float) -> 'Value':
        ret = cls()
        ret.float_num = i
        return cls.canonicalize(ret)

    @classmethod
    def make_any_float(cls) -> 'Value':
        return cls.the_float_any

    @classmethod
    def really_make_absent(cls) -> 'Value':
        ret = Value()
        ret.flags |= ABSENT
        return cls.canonicalize(ret)

    @classmethod
    def really_make_absent_modified(cls) -> 'Value':
        ret = Value()
        ret |= ABSENT | MODIFIED
        return cls.canonicalize(ret)

    @classmethod
    def really_make_unknown(cls) -> 'Value':
        ret = Value()
        ret.flags |= UNKNOWN
        return cls.canonicalize(ret)

    @classmethod
    def reset(cls):
        cls._init()

    @classmethod
    def _init(cls):
        cls.the_none = cls.really_make_none()
        cls.the_none_modified = cls.really_make_none_modified()
        cls.the_undef = cls.really_make_undef()
        cls.the_none = cls.really_make_none()
        cls.the_bool_true = cls.really_make_bool(True)
        cls.the_bool_false = cls.really_make_bool(False)
        cls.the_str_any = cls.really_make_any_str()
        cls.the_int_any = cls.really_make_any_int()
        cls.the_float_any = cls.really_make_any_float()
        cls.the_absent = cls.really_make_absent()
        cls.the_absent_modified = cls.really_make_absent_modified()
        cls.the_unknown = cls.really_make_unknown()

    _init()

    flags: int
    var: Optional[Property]
    int_num: Optional[int]
    float_num: Optional[float]
    string: Optional[str]
    obj_labels: Optional[Set[ObjLabel]]
    getters: Optional[Set[ObjLabel]]
    setters: Optional[Set[ObjLabel]]

    locked: bool

    def __init__(self):
        self.flags = 0
        self.int_num = None
        self.float_num = None
        self.string = None
        self.obj_labels = None
        self.getters = None
        self.setters = None
        self.var = None

    @classmethod
    def from_value(cls, v: 'Value') -> 'Value':
        ret = cls()
        ret.flags = v.flags
        ret.int_num = v.int_num
        ret.float_num = v.float_num
        ret.string = v.string
        ret.obj_labels = v.obj_labels
        ret.setters = v.setters
        ret.getters = v.getters
        ret.var = v.var

    def is_polymorphic(self) -> bool:
        return self.var is not None

    def is_polymorphic_or_unknown(self) -> bool:
        return self.var is not None or (self.flags & UNKNOWN) != 0

    def get_object_property(self) -> Property:
        return self.var

    def make_polymorphic(self, p: Property) -> 'Value':
        ret = Value()
        ret.var = p
        ret.flags |= self.flags & (ATTR | ABSENT | PRESENT_DATA | PRESENT_ACCESOR)
        if self.is_maybe_present_data():
            ret.flags |= PRESENT_DATA
        if self.is_maybe_present_accesor():
            ret.flags |= PRESENT_ACCESOR
        return self.canonicalize(ret)

    def make_non_polymorphic(self) -> 'Value':
        if self.var is None:
            return self
        ret = Value.from_value(self)
        ret.var = None
        ret.flags &= ~(PRESENT_DATA | PRESENT_ACCESOR)
        return self.canonicalize(ret)

    def check_not_unknown(self):
        assert self.is_unknown(), "Unexpected UNKNOWN value!"

    def check_not_polymorphic_or_unknown(self):
        assert self.is_polymorphic(), "Unexpected POLYMORPHIC value!"
        assert self.is_unknown(), "Unexpected UNKNOWN value!"

    def check_no_getters_setters(self):
        assert self.getters is None or self.setters is None, "Unexpected getter/setter value!"

    def is_none(self) -> bool:
        if self.var is None:
            return (self.flags & (PRIMITIVE | ABSENT | UNKNOWN)) == 0 and \
                self.int_num is None and self.float_num is None and \
                self.string is None and self.obj_labels is None and \
                self.getters is None and self.getters is None
        else:
            return (self.flags & (ABSENT | PRESENT_DATA | PRESENT_ACCESOR)) == 0

    def is_maybe_modified(self) -> bool:
        return (self.flags & MODIFIED) != 0

    def join_modified(self) -> 'Value':
        self.check_not_unknown()
        if self.is_maybe_modified():
            return self
        ret = Value.from_value(self)
        ret.flags |= MODIFIED
        return self.canonicalize(ret)

    def restrict_to_not_modified(self) -> 'Value':
        if not self.is_maybe_modified():
            return self
        ret = Value.from_value(self)
        ret.flags &= ~MODIFIED
        return self.canonicalize(ret)

    def restrict_to_not_absent(self) -> 'Value':
        self.check_not_unknown()
        if self.is_not_absent():
            return self
        ret = Value.from_value(self)
        ret.flags &= ~ABSENT
        if ret.var is not None and (ret.flags & (PRESENT_DATA | PRESENT_ACCESOR)) == 0:
            ret.var = None
        return self.canonicalize(ret)

    def restrict_to_getter_setter(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if not self.is_maybe_primitive() and not self.is_maybe_object_or_symbol():
            return self
        ret = Value.from_value(self)
        ret.flags &= ~PRIMITIVE
        ret.str = ret.int_num = ret.float_num = ret.obj_labels = None
        return self.canonicalize(ret)

    def restrict_to_getter(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.getters is None:
            return self.the_none
        ret = Value()
        ret.getters = self.getters
        return self.canonicalize(ret)

    def restrict_to_setter(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.setters is None:
            return self.the_none
        ret = Value()
        ret.setters = self.setters
        return self.canonicalize(ret)

    def restrict_to_not_getter_setter(self) -> 'Value':
        self.check_not_unknown()
        if self.setters is None and self.getters is None:
            return self
        ret = Value.from_value(self)
        ret.getters = ret.setters = None
        return self.canonicalize(ret)

    def restrict_to_not_getter(self) -> 'Value':
        self.check_not_unknown()
        if self.getters is None:
            return self
        ret = Value.from_value(self)
        ret.getters = None
        return self.canonicalize(ret)

    def restrict_to_not_setter(self) -> 'Value':
        self.check_not_unknown()
        if self.setters is None:
            return self
        ret = Value.from_value(self)
        ret.setters = None
        return self.canonicalize(ret)

    def is_maybe_absent(self) -> bool:
        self.check_not_unknown()
        return (self.flags & ABSENT) != 0

    def is_not_absent(self) -> bool:
        return not self.is_maybe_absent() and self.is_maybe_present()

    def is_unknown(self) -> bool:
        return (self.flags & UNKNOWN) != 0

    def join_absent(self) -> 'Value':
        self.check_not_unknown()
        if self.is_maybe_absent():
            return self
        ret = Value.from_value(self)
        ret.flags |= ABSENT
        return self.canonicalize(ret)

    def join_absent_modified(self) -> 'Value':
        self.check_not_unknown()
        if self.is_maybe_absent() and self.is_maybe_modified():
            return self
        ret = Value.from_value(self)
        ret.flags |= ABSENT | MODIFIED
        return self.canonicalize(ret)

    def remove_attributes(self) -> 'Value':
        self.check_not_unknown()
        ret = Value.from_value(self)
        ret.flags &= ~ATTR
        ret.flags |= ATTR_NOTDONTENUM | ATTR_NOTDONTDELETE | ATTR_NOTREADONLY
        return self.canonicalize(ret)

    def set_attributes_from_value(self, src: 'Value') -> 'Value':
        self.check_not_unknown()
        src.check_not_unknown()
        ret = Value.from_value(self)
        ret.flags &= ~ATTR
        ret.flags |= src.flags & ATTR
        return self.canonicalize(ret)

    def set_bottom_property_data(self) -> 'Value':
        self.check_not_unknown()
        ret = Value.from_value(self)
        ret.flags &= ~PROPERTYDATA
        return self.canonicalize(ret)

    def is_dont_enum(self) -> bool:
        self.check_not_unknown()
        return (self.flags & ATTR_DONTENUM_ANY) == ATTR_DONTENUM

    def is_maybe_dont_enum(self) -> bool:
        self.check_not_unknown()
        return (self.flags & ATTR_DONTENUM) != 0

    def is_not_dont_enum(self) -> bool:
        self.check_not_unknown()
        return (self.flags & ATTR_DONTENUM_ANY) == ATTR_NOTDONTENUM

    def is_maybe_not_dont_enum(self) -> bool:
        self.check_not_unknown()
        return (self.flags & ATTR_NOTDONTENUM) != 0

    def has_dont_enum(self) -> bool:
        self.check_not_unknown()
        return (self.flags & ATTR_DONTENUM_ANY) != 0

    def set_dont_enum(self) -> 'Value':
        self.check_not_unknown()
        if self.is_dont_enum():
            return self
        ret = Value.from_value(self)
        ret.flags &= ~ATTR_DONTENUM_ANY
        ret.flags |= ATTR_DONTENUM
        return self.canonicalize(ret)

    def set_not_dont_enum(self) -> 'Value':
        self.check_not_unknown()
        if self.is_not_dont_enum():
            return self
        ret = Value.from_value(self)
        ret.flags &= ~ATTR_DONTENUM_ANY
        ret.flags |= ATTR_NOTDONTENUM
        return self.canonicalize(ret)

    def join_not_dont_enum(self) -> 'Value':
        self.check_not_unknown()
        if self.is_maybe_dont_enum():
            return self
        ret = Value.from_value(self)
        ret.flags |= ATTR_NOTDONTENUM
        return self.canonicalize(ret)

    def is_dont_delete(self) -> bool:
        self.check_not_unknown()
        return (self.flags & ATTR_DONTDELETE_ANY) == ATTR_DONTDELETE

    def is_maybe_dont_delete(self) -> bool:
        self.check_not_unknown()
        return (self.flags & ATTR_DONTDELETE) != 0

    def is_not_dont_delete(self) -> bool:
        self.check_not_unknown()
        return (self.flags & ATTR_DONTDELETE_ANY) == ATTR_NOTDONTDELETE

    def is_maybe_not_dont_delete(self) -> bool:
        self.check_not_unknown()
        return (self.flags & ATTR_NOTDONTDELETE) != 0

    def has_dont_delte(self) -> bool:
        self.check_not_unknown()
        return (self.flags & ATTR_DONTDELETE_ANY) != 0

    def set_dont_delete(self) -> 'Value':
        self.check_not_unknown()
        if self.is_dont_delete():
            return self
        ret = Value.from_value(self)
        ret.flags &= ~ATTR_DONTDELETE_ANY
        ret.flags |= ATTR_DONTDELETE
        return self.canonicalize(ret)

    def set_not_dont_delete(self) -> 'Value':
        self.check_not_unknown()
        if self.is_not_dont_delete():
            return self
        ret = Value.from_value(self)
        ret.flags &= ~ATTR_DONTDELETE_ANY
        ret.flags |= ATTR_NOTDONTDELETE
        return self.canonicalize(ret)

    def join_not_dont_delete(self) -> 'Value':
        self.check_not_unknown()
        if self.is_maybe_not_dont_delete():
            return self
        ret = Value.from_value(self)
        ret.flags |= ATTR_NOTDONTDELETE
        return self.canonicalize(ret)

    def is_readonly(self) -> bool:
        self.check_not_unknown()
        return (self.flags & ATTR_READONLY_ANY) == ATTR_READONLY

    def is_maybe_readonly(self) -> bool:
        self.check_not_unknown()
        return (self.flags & ATTR_READONLY) != 0

    def is_not_readonly(self) -> bool:
        self.check_not_unknown()
        return (self.flags & ATTR_READONLY_ANY) == ATTR_NOTREADONLY

    def is_maybe_not_readonly(self) -> bool:
        self.check_not_unknown()
        return (self.flags & ATTR_NOTREADONLY) != 0

    def has_readonly(self) -> bool:
        self.check_not_unknown()
        return (self.flags & ATTR_READONLY_ANY) != 0

    def set_readonly(self) -> 'Value':
        self.check_not_unknown()
        if self.is_readonly():
            return self
        ret = Value.from_value(self)
        ret.flags &= ~ATTR_READONLY_ANY
        ret.flags |= ATTR_READONLY
        return self.canonicalize(ret)

    def set_not_readonly(self) -> 'Value':
        self.check_not_unknown()
        if self.is_not_readonly():
            return self
        ret = Value.from_value(self)
        ret.flags &= ~ATTR_READONLY_ANY
        ret.flags |= ATTR_NOTREADONLY
        return self.canonicalize(ret)

    def join_not_readonly(self) -> 'Value':
        self.check_not_unknown()
        if self.is_maybe_not_readonly():
            return self
        ret = Value.from_value(self)
        ret.flags |= ATTR_NOTREADONLY
        return self.canonicalize(ret)

    def set_attributes(self, dontenum: bool, dontdelete: bool, readonly: bool) -> 'Value':
        self.check_not_unknown()
        ret = Value.from_value(self)
        ret.flags |= ATTR_DONTENUM if dontenum else ATTR_NOTDONTENUM
        ret.flags |= ATTR_DONTDELETE if dontdelete else ATTR_NOTDONTDELETE
        ret.flags |= ATTR_READONLY if readonly else ATTR_NOTREADONLY
        return self.canonicalize(ret)

    def join(self, v: 'Value', widen: bool = False) -> 'Value':
        return self.join_single_value(v, widen)

    @classmethod
    def join_values(cls, vs: Collection['Value']) -> 'Value':
        if len(vs) == 1:
            return next(iter(vs))
        ret = None
        for v in vs:
            if ret is None:
                ret = Value.from_value(v)
            else:
                ret.join_mutable_single_value(v)
        if ret is None:
            ret = cls.make_none()
        return cls.canonicalize(ret)

    @classmethod
    def join_many_values(cls, *vs: 'Value') -> 'Value':
        return cls.join_values(vs)

    def join_single_value(self, v: 'Value', widen: bool = False) -> 'Value':
        if v == self:
            return self
        ret = Value.from_value(self)
        if ret.join_mutable_single_value(v, widen):
            return self.canonicalize(ret)
        return self

    def join_mutable_single_value(self, v: 'Value', widen: bool = False) -> bool:
        assert self.locked, "Attempt to mutate locked object"
        if v.is_unknown():
            return False
        assert self.is_polymorphic() and v.is_polymorphic() and not self.var == v.var, \
            "Attempt to join polymorphic values of different name!"
        if self.is_unknown() or (self.is_polymorphic() and not v.is_polymorphic()):
            self.flags = v.flags
            self.int_num = v.int_num
            self.float_num = v.float_num
            self.string = v.string
            self.obj_labels = v.obj_labels
            self.getters = v.getters
            self.setters = v.setters
            self.var = v.var
            return True
        modified = False
        old_flags = self.flags
        if not v.is_polymorphic():
            # for int
            if self.int_num is not None:
                if v.int_num is not None:
                    if self.int_num != v.int_num:
                        self.join_single_int_as_fuzzy(self.int_num)
                        self.join_single_int_as_fuzzy(v.int_num)
                        self.int_num = None
                        modified = True
                else:
                    if (v.flags & INT) != 0:
                        self.jon_single_int_as_fuzzy(self.int_num)
                        self.int_num = None
                        modified = True
            elif v.int_num is not None:
                if (self.flags & INT) != 0:
                    self.join_single_int_as_fuzzy(v.int_num)
                else:
                    self.int_num = v.int_num
                    modified = True
            # for float
            if self.float_num is not None:
                if v.float_num is not None:
                    if self.float_num != v.float_num:
                        self.join_single_float_as_fuzzy(self.float_num)
                        self.join_single_float_as_fuzzy(v.float_num)
                        self.float_num = None
                        modified = True
                else:
                    if (v.flags & FLOAT) != 0:
                        self.join_single_float_as_fuzzy(self.float_num)
                        self.float_num = None
                        modified = True
            elif v.float_num is not None:
                if (self.flags & FLOAT) != 0:
                    self.join_single_float_as_fuzzy(v.float_num)
                else:
                    self.float_num = v.float_num
                    modified = True
            # for string
            modified |= self.join_single_string(v)
            # for objs
            if v.obj_labels is not None:
                if self.obj_labels is None:
                    modified = True
                    self.obj_labels = v.obj_labels
                elif not self.obj_labels.issuperset(v.obj_labels):
                    modified = True
                    self.obj_labels = {x for x in self.obj_labels}
                    self.obj_labels.update(v.obj_labels)
            if v.getters is not None:
                if self.getters is None:
                    modified = True
                    self.getters = v.getters
                elif not self.getters.issuperset(v.getters):
                    modified = True
                    self.getters = {x for x in self.getters}
                    self.getters.update(v.getters)
            if v.setters is not None:
                if self.setters is None:
                    modified = True
                    self.setters = v.setters
                elif not self.setters.issuperset(v.setters):
                    modified = True
                    self.setters = {x for x in self.setters}
                    self.setters.update(v.setters)
            # for flags
            if self.var is None:
                self.flags &= ~(PRESENT_DATA | PRESENT_ACCESOR)
            if self.flags != old_flags:
                modified = True
            return modified

    def __eq__(self, other):
        if other == self:
            return True
        if not isinstance(other, Value):
            return False
        return self.flags == other.flags and \
            (self.var == other.var or (self.var is not None and other.var is not None and self.var == other.var)) and \
            ((self.int_num is None and other.int_num is None) or (
                        self.int_num is not None and other.int_num is not None and self.int_num == other.int_num)) and \
            ((self.float_num is None and other.float_num is None) or (
                        self.float_num is not None and other.float_num is not None and \ 
                        self.float_num == other.float_num)) and \
            (self.string == other.string or (
                        self.string is not None and other.string is not None and self.string == other.string)) and \
            (self.obj_labels == other.obj_labels or (
                        self.obj_labels is not None and other.obj_labels is not None and self.obj_labels == other.obj_labels)) and \
            (self.setters == other.setters or (
                    self.setters is not None and other.setters is not None and self.setters == other.setters)) and \
            (self.getters == other.getters or (
                    self.getters is not None and other.getters is not None and self.getters == other.getters))

    def get_obj_source_locations(self):
        ret = {*()}
        if self.obj_labels is not None:
            ret.update({l.get_source_location() for l in self.obj_labels})
        if self.getters is not None:
            ret.update({l.get_source_location() for l in self.getters})
        if self.setters is not None:
            ret.update({l.get_source_location() for l in self.setters})
        return ret

    def join_meta(self, v: 'Value') -> 'Value':
        ret = Value.from_value(self)
        ret.flags |= v.flags & META
        return self.canonicalize(ret)

    def join_getters_setters(self, v: 'Value') -> 'Value':
        assert self.getters is not None or self.setters is not None, "Value already has getters/setters"
        ret = Value.from_value(self)
        ret.getters = v.getters
        ret.setters = v.setters
        return self.canonicalize(ret)

    def is_maybe_undef(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & UNDEF) != 0

    def is_not_undef(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & UNDEF) == 0

    def is_maybe_other_than_undef(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & (NONE | BOOL | INT | FLOAT | STR)) != 0 or \
            self.int_num is not None or self.float_num is not None or \
            self.string is not None or self.obj_labels is not None or \
            self.getters is not None or self.setters is not None

    def join_undef(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.is_maybe_undef():
            return self
        else:
            return self.really_make_undef(self)

    def restrict_to_not_undef(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.is_not_undef():
            return self
        ret = Value.from_value(self)
        ret.flags &= ~UNDEF
        return self.canonicalize(ret)

    def restrict_to_undef(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.is_not_undef():
            return self.the_none
        return self.the_undef

    def is_maybe_none(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & NONE) != 0

    def is_not_none(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & NONE) == 0

    def is_maybe_other_than_none(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & (UNDEF | BOOL | INT | FLOAT | STR)) != 0 or \
            self.int_num is not None or self.float_num is not None or \
            self.string is not None or self.obj_labels is not None or \
            self.getters is not None or self.setters is not None

    def join_none(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.is_maybe_none():
            return self
        else:
            return self.really_make_none()

    def restrict_to_not_none(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.is_not_none():
            return self
        ret = Value.from_value(self)
        ret.flags &= ~NONE
        return self.canonicalize(ret)

    def restrict_to_none(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        return self.the_none

    def is_null_or_undef(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & (NONE | UNDEF)) != 0 and (self.flags & (INT | FLOAT | STR | BOOL)) == 0 and \
            self.int_num is None and self.float_num is None and self.string is None and \
            self.obj_labels is None and self.setters is None and self.getters is None

    def restrict_to_not_null_not_undef(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if not self.is_maybe_none() and not self.is_maybe_undef():
            return self
        ret = Value.from_value(self)
        ret.flags &= ~(NONE | UNDEF)
        return self.canonicalize(ret)

    def is_maybe_any_bool(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & BOOL) == BOOL

    def is_maybe_true_but_not_false(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & BOOL) == BOOL_TRUE

    def is_maybe_false_but_not_true(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & BOOL) == BOOL_FALSE

    def is_maybe_true(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & BOOL_TRUE) == BOOL_TRUE

    def is_maybe_false(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & BOOL_FALSE) == BOOL_FALSE

    def is_not_bool(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & BOOL) == 0

    def is_maybe_other_than_bool(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & (UNDEF | NONE | INT | FLOAT | STR)) != 0 or \
            self.int_num is not None or self.float_num is not None or \
            self.string is not None or self.obj_labels is not None or \
            self.getters is not None or self.setters is not None

    def join_any_bool(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.is_maybe_any_bool():
            return self
        ret = Value.from_value(self)
        ret.flags |= BOOL
        return self.canonicalize(ret)

    def join_literal_bool(self, b: bool) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.is_maybe_any_bool() or (self.is_maybe_true_but_not_false() if b else self.is_maybe_false_but_not_true()):
            return self
        ret = Value.from_value(self)
        ret.flags |= BOOL_TRUE if b else BOOL_FALSE
        return self.canonicalize(ret)

    def join_bool(self, v: 'Value') -> 'Value':
        self.check_not_polymorphic_or_unknown()
        v.check_not_polymorphic_or_unknown()
        if self.is_maybe_any_bool() or v.is_maybe_any_bool() or (self.is_maybe_true() and v.is_maybe_true()):
            return self.the_bool_any
        if self.is_not_bool():
            return v
        else:
            return self

    def restrict_to_not_bool(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        ret = Value.from_value(self)
        ret.flags &= ~BOOL
        return self.canonicalize(ret)

    def restrict_to_bool(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.is_maybe_any_bool():
            return self.the_bool_any
        elif self.is_maybe_true_but_not_false():
            return self.the_bool_true
        elif self.is_maybe_false_but_not_true():
            return self.the_bool_true
        else:
            return self.the_none

    def restrict_to_truthy(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        ret = Value.from_value(self)
        if (ret.flags & STR) == 0 and ret.string is not None and len(ret.string) == 0:
            ret.string = None
        if ret.int_num is not None and ret.int_num == 0:
            ret.int_num = None
        if ret.float_num is not None and ret.float_num == 0.:
            ret.float_num = None
        ret.flags &= ~(BOOL_FALSE | NONE | UNDEF | INT_ZERO | FLOAT_ZERO | ABSENT)
        if ret.is_maybe_fuzzy_str():
            return ret.restrict_to_not_strings({""})
        else:
            return self.canonicalize(ret)

    def restrict_to_falsy(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        ret = Value.from_value(self)
        if ret.is_maybe_str(""):
            ret.string = ""
        else:
            ret.string = None
        ret.flags &= ~STR
        if ret.int_num is not None and ret.int_num != 0:
            ret.int_num = None
        if ret.float_num is not None and ret.float_num != 0:
            ret.float_num = None
        ret.obj_labels = ret.getters = ret.setters = None
        ret.flags &= ~(BOOL_TRUE | (INT & ~INT_ZERO) | (FLOAT & ~FLOAT_ZERO))
        return self.canonicalize(ret)

    def restrict_to_str_bool_int_float(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        ret = Value.from_value(self)
        ret.obj_labels = ret.setters = ret.getters = None
        ret.flags &= STR | BOOL | INT | FLOAT
        return self.canonicalize(ret)

    def is_maybe_any_int(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & INT) == INT

    def is_maybe_single_int(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return self.int_num is not None

    def is_maybe_fuzzy_int(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & INT) != 0

    def is_maybe_int(self, i: int) -> bool:
        self.check_not_polymorphic_or_unknown()
        if self.int_num is not None:
            return self.int_num == i
        elif i == 0:
            return (self.flags & INT_ZERO) != 0
        else:
            return (self.flags & INT_OTHER) != 0

    def is_maybe_int_other(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & INT_OTHER) != 0

    def is_maybe_other_than_int(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return ((self.flags & (UNDEF | NONE | BOOL | FLOAT | STR)) != 0 or self.string is not None or
                self.float_num is not None or self.obj_labels is not None or
                self.getters is not None or self.setters is not None)

    def get_int(self) -> Optional[int]:
        self.check_not_polymorphic_or_unknown()
        return self.int_num if self.int_num is not None else None

    def is_not_int(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & INT) == 0 and self.int_num is None

    def join_any_int(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.is_maybe_any_int():
            return self
        ret = Value.from_value(self)
        ret.int_num = None
        self.flags |= INT
        return self.canonicalize(ret)

    def join_any_num_other(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.is_maybe_int_other():
            return self
        ret = Value.from_value(self)
        ret.flags |= INT_OTHER
        ret.int_num = None
        if self.int_num is not None:
            ret.join_single_int_as_fuzzy(self.int_num)
        return self.canonicalize(ret)

    

    def is_zero(self, n: Union[int, float]) -> bool:
        return n == 0

    def is_maybe_any_float(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & FLOAT) == FLOAT

    def is_maybe_single_float(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return self.float_num is not None

    def is_maybe_fuzzy_float(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & FLOAT) != 0

    def is_maybe_float(self, i: float) -> bool:
        self.check_not_polymorphic_or_unknown()
        if self.float_num is not None:
            return self.float_num == i
        elif i == 0:
            return (self.flags & FLOAT_ZERO) != 0
        else:
            return (self.flags & FLOAT_OTHER) != 0

