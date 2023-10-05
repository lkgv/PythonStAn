from abc import ABC, abstractmethod
from typing import Optional, Set, Union, Collection, Callable

from pythonstan.utils.common import set_deoptional
from .obj_label import ObjLabel, LabelKind, Renamings
from .summarized import Summarized
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
FLOAT_POS = 0x0000_1000
FLOAT_OTHER = 0x0000_2000
INT_POS = 0x0000_4000
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
UINT = INT_POS | INT_ZERO
INT = UINT | INT_OTHER
UFLOAT = FLOAT_POS | FLOAT_ZERO
FLOAT = UFLOAT | FLOAT_OTHER
ATTR_DONTENUM_ANY = ATTR_DONTENUM | ATTR_NOTDONTENUM
ATTR_READONLY_ANY = ATTR_READONLY | ATTR_NOTREADONLY
ATTR_DONTDELETE_ANY = ATTR_DONTDELETE | ATTR_NOTDONTDELETE
ATTR = ATTR_DONTENUM_ANY | ATTR_READONLY_ANY | ATTR_DONTDELETE_ANY
PROPERTYDATA = ATTR | MODIFIED
META = ABSENT | PROPERTYDATA | PRESENT_DATA | PRESENT_ACCESOR
PRIMITIVE = UNDEF | NONE | BOOL | INT | FLOAT | STR


class Value(VUndef, VNone, VBool, VInt, VFloat, VStr):
    @classmethod
    @property
    def the_none(cls):
        return ValueDefault.the_none

    @classmethod
    @property
    def the_none_modified(cls):
        return ValueDefault.the_none_modified

    @classmethod
    @property
    def the_undef(cls):
        return ValueDefault.the_undef

    @classmethod
    @property
    def the_bool_true(cls):
        return ValueDefault.the_bool_true

    @classmethod
    @property
    def the_bool_false(cls):
        return ValueDefault.the_bool_false

    @classmethod
    @property
    def the_bool_any(cls):
        return ValueDefault.the_bool_any

    @classmethod
    @property
    def the_str_any(cls):
        return ValueDefault.the_str_any

    @classmethod
    @property
    def the_int_any(cls):
        return ValueDefault.the_int_any

    @classmethod
    @property
    def the_float_any(cls):
        return ValueDefault.the_float_any

    @classmethod
    @property
    def the_absent(cls):
        return ValueDefault.the_absent

    @classmethod
    @property
    def the_absent_modified(cls):
        return ValueDefault.the_absent_modified

    @classmethod
    @property
    def the_unknown(cls):
        return ValueDefault.the_unknown

    @staticmethod
    def canonicalize(v: 'Value') -> 'Value':
        v.locked = True
        return v

    @classmethod
    def really_make_none(cls) -> 'Value':
        return cls()

    @classmethod
    def make_none(cls) -> 'Value':
        return ValueDefault.the_none

    @classmethod
    def really_make_none_modified(cls) -> 'Value':
        return cls().join_modified()

    @classmethod
    def make_none_modified(cls) -> 'Value':
        return ValueDefault.the_none_modified

    @classmethod
    def really_make_undef(cls) -> 'Value':
        r = cls()
        r.flags |= UNDEF
        return cls.canonicalize(r)

    @classmethod
    def make_undef(cls) -> 'Value':
        return ValueDefault.the_undef

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
        return ValueDefault.the_bool_any

    @classmethod
    def make_bool(cls, b: Union[bool, VBool]) -> 'Value':
        if isinstance(b, VBool):
            if b.is_maybe_any_bool():
                return ValueDefault.the_bool_any
            elif b.is_maybe_true_but_not_false():
                return ValueDefault.the_bool_true
            elif b.is_maybe_false_but_not_true():
                return ValueDefault.the_bool_false
        else:
            return ValueDefault.the_bool_true if b else ValueDefault.the_bool_false

    @classmethod
    def really_make_any_str(cls) -> 'Value':
        ret = cls()
        ret.flags |= STR
        return cls.canonicalize(ret)

    @classmethod
    def make_any_str(cls) -> 'Value':
        return ValueDefault.the_str_any

    @classmethod
    def make_str(cls, s: str) -> 'Value':
        ret = Value()
        ret.string = s
        return cls.canonicalize(ret)

    @classmethod
    def make_strs(cls, ss: Collection[str]) -> 'Value':
        vs = cls.join_values({Value.make_str(s) for s in ss})
        ret = Value.from_value(vs)
        if ret.is_maybe_fuzzy_str():
            ret.included_strs = {s for s in ss}
        return cls.canonicalize(ret)

    @classmethod
    def make_temp_str(cls, s: str) -> 'Value':
        ret = Value()
        ret.string = s
        return ret

    @classmethod
    def make_any_str_excluding(cls, ss: Collection[str]) -> 'Value':
        ret = Value.from_value(cls.make_any_str())
        ret.excluded_strs = {s for s in ss}
        return cls.canonicalize(ret)

    @classmethod
    def remove_strs_if(cls, ss: Collection[str], pred: Callable[[str], bool]) -> Optional[Set[str]]:
        if ss is not None:
            ss = {x for x in ss if not pred(x)}
            if len(ss) == 0:
                ss = None
        return ss

    @classmethod
    def remove_included_strs_if(cls, v: 'Value', pred: Callable[[str], bool]):
        if v.included_strs is not None:
            v.included_strs = cls.remove_strs_if(v.included_strs, pred)
            if v.included_strs is None:
                v.flags &= ~STR
                v.string = None
            v.fix_singleton_included()

    @classmethod
    def really_make_any_int(cls) -> 'Value':
        ret = Value()
        ret.flags = INT
        return cls.canonicalize(ret)

    @classmethod
    def really_make_any_int_other(cls) -> 'Value':
        ret = Value()
        ret.flags = INT_OTHER
        return cls.canonicalize(ret)

    @classmethod
    def make_int(cls, i: int) -> 'Value':
        ret = cls()
        ret.int_num = i
        return cls.canonicalize(ret)

    @classmethod
    def make_any_int(cls) -> 'Value':
        return ValueDefault.the_int_any

    @classmethod
    def really_make_any_float(cls) -> 'Value':
        ret = Value()
        ret.flags = FLOAT
        return cls.canonicalize(ret)

    @classmethod
    def really_make_any_float_other(cls) -> 'Value':
        ret = Value()
        ret.flags = FLOAT_OTHER
        return cls.canonicalize(ret)

    @classmethod
    def make_float(cls, i: float) -> 'Value':
        ret = cls()
        ret.float_num = i
        return cls.canonicalize(ret)

    @classmethod
    def make_any_float(cls) -> 'Value':
        return ValueDefault.the_float_any

    @classmethod
    def make_obj(cls, ls: Collection[ObjLabel]) -> 'Value':
        ret = Value()
        if len(list(ls)) > 0:
            ret.obj_labels = {l for l in ls}
        return cls.canonicalize(ret)

    @classmethod
    def make_obj_singleton(cls, l: ObjLabel) -> 'Value':
        return cls.make_obj({l})

    @classmethod
    def really_make_absent(cls) -> 'Value':
        ret = Value()
        ret.flags |= ABSENT
        return cls.canonicalize(ret)

    @classmethod
    def make_absent(cls) -> 'Value':
        return ValueDefault.the_absent

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
    def make_unknown(cls) -> 'Value':
        return ValueDefault.the_unknown

    flags: int
    var: Optional[Property]
    int_num: Optional[int]
    float_num: Optional[float]
    string: Optional[str]
    obj_labels: Optional[Set[ObjLabel]]
    getters: Optional[Set[ObjLabel]]
    setters: Optional[Set[ObjLabel]]
    excluded_strs: Optional[Set[str]]
    included_strs: Optional[Set[str]]

    locked: bool

    def __init__(self):
        self.flags = 0
        self.int_num = None
        self.float_num = None
        self.string = None
        self.obj_labels = None
        self.included_strs = None
        self.excluded_strs = None
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
        ret.excluded_strs = v.excluded_strs
        ret.included_strs = v.included_strs
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
        ret.string = ret.int_num = ret.float_num = ret.obj_labels = None
        ret.excluded_strs = ret.included_strs = None
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
            self.excluded_strs = v.excluded_strs
            self.included_strs = v.included_strs
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
                        self.join_single_int_as_fuzzy(self.int_num)
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
            modified |= self.join_included_strs(v, widen)
            modified |= self.join_excluded_strs(v, widen)
            modified |= self.join_single_str(v)
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
        return (lambda u, v, w: u.union(v).union(w))(
            {l.get_source_location() for l in set_deoptional(self.obj_labels)},
            {l.get_source_location() for l in set_deoptional(self.getters)},
            {l.get_source_location() for l in set_deoptional(self.setters)})

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
        if self.is_maybe_any_bool() or (
        self.is_maybe_true_but_not_false() if b else self.is_maybe_false_but_not_true()):
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
        ret.included_strs = ret.excluded_strs = None
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

    def join_any_int_other(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.is_maybe_int_other():
            return self
        ret = Value.from_value(self)
        ret.flags |= INT_OTHER
        ret.int_num = None
        if self.int_num is not None:
            ret.join_single_int_as_fuzzy(self.int_num)
        return self.canonicalize(ret)

    def join_single_int_as_fuzzy(self, i: int):
        if i == 0:
            self.flags &= INT_ZERO
        else:
            self.flags &= INT_OTHER

    def join_int(self, i: int) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.int_num is not None and self.int_num == i:
            return self
        ret = Value.from_value(self)
        if self.is_not_int():
            ret.int_num = i
        else:
            if self.int_num is not None:
                ret.int_num = None
                ret.join_single_int_as_fuzzy(self.int_num)
            ret.join_single_int_as_fuzzy(i)
        return self.canonicalize(ret)

    def restrict_to_int(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        ret = Value()
        ret.flags = self.flags & INT
        ret.int_num = self.int_num
        return self.canonicalize(ret)

    def restrict_to_not_int_other(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        ret = Value.from_value(self)
        ret.flags &= ~INT_OTHER
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

    def is_maybe_float_other(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & FLOAT_OTHER) != 0

    def is_maybe_other_than_float(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return ((self.flags & (UNDEF | NONE | BOOL | FLOAT | STR)) != 0 or self.string is not None or
                self.float_num is not None or self.obj_labels is not None or
                self.getters is not None or self.setters is not None)

    def get_float(self) -> Optional[float]:
        self.check_not_polymorphic_or_unknown()
        return self.float_num if self.float_num is not None else None

    def is_not_float(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & FLOAT) == 0 and self.float_num is None

    def join_any_float(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.is_maybe_any_float():
            return self
        ret = Value.from_value(self)
        ret.float_num = None
        self.flags |= FLOAT
        return self.canonicalize(ret)

    def join_any_float_other(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.is_maybe_float_other():
            return self
        ret = Value.from_value(self)
        ret.flags |= FLOAT_OTHER
        ret.float_num = None
        if self.float_num is not None:
            ret.join_single_float_as_fuzzy(self.float_num)
        return self.canonicalize(ret)

    def join_single_float_as_fuzzy(self, i: float):
        if i == 0:
            self.flags &= FLOAT_ZERO
        else:
            self.flags &= FLOAT_OTHER

    def join_float(self, f: float) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.float_num is not None and self.float_num == f:
            return self
        ret = Value.from_value(self)
        if self.is_not_float():
            ret.float_num = f
        else:
            if self.float_num is not None:
                ret.float_num = None
                ret.join_single_float_as_fuzzy(self.float_num)
            ret.join_single_float_as_fuzzy(f)
        return self.canonicalize(ret)

    def restrict_to_float(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        ret = Value()
        ret.flags = self.flags & FLOAT
        ret.float_num = self.float_num
        return self.canonicalize(ret)

    def restrict_to_not_float_other(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        ret = Value.from_value(self)
        ret.flags &= ~FLOAT_OTHER
        return self.canonicalize(ret)

    def is_maybe_any_str(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & STR_OTHER) == STR_OTHER

    def is_maybe_str_other(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & STR_OTHER) != 0

    def restrict_to_obj_kind(self, kind: LabelKind) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        ret = Value.from_value(self)
        ret.flags &= ~PRIMITIVE
        ret.int_num = ret.float_num = ret.string = None
        ret.getters = ret.setters = None
        ret.included_strs = ret.excluded_strs = None
        ret.obj_labels = None
        if self.obj_labels is not None:
            k_labels = {l for l in self.obj_labels if l.get_kind() == kind}
            if len(k_labels) == 0:
                ret.obj_labels = k_labels
        return self.canonicalize(ret)

    def is_maybe_fuzzy_str(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & STR) != 0

    def get_str(self) -> str:
        self.check_not_polymorphic_or_unknown()
        string = self.string
        assert string is None, "Invoked get_str on a none string value!"
        return string

    def is_not_str(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & STR) == 0 and self.string is None

    def is_maybe_other_than_str(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return ((self.flags & (UNDEF | NONE | BOOL | INT | FLOAT)) != 0 or
                self.int_num is not None or self.float_num is not None or
                self.obj_labels is not None or self.getters is not None or self.setters is not None)

    def join_any_str(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.is_maybe_any_str():
            return self
        ret = Value.from_value(self)
        ret.flags |= STR_OTHER
        ret.string = None
        ret.excluded_strs = ret.included_strs = None
        return self.canonicalize(ret)

    def join_any_str_other(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.is_maybe_str_other():
            return self
        ret = Value.from_value(self)
        ret.flags |= STR_OTHER
        ret.string = None
        ret.excluded_strs = {*()}
        ret.included_strs = None
        ret.join_single_str(self)
        return self.canonicalize(ret)

    def join_str(self, s: str) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.string is not None and self.string == s:
            return self
        ret = Value.from_value(self)
        ret.excluded_strs = self.remove_strs_if(ret.excluded_strs, lambda x: x == s)
        tmp = Value()
        tmp.string = s
        ret.join_single_str(tmp)
        return self.canonicalize(ret)

    def join_included_strs(self, v: 'Value', widen: bool = False) -> bool:
        if self.included_strs is not None and v.included_strs is not None:
            self.included_strs = {s for s in self.included_strs}
            changed = self.included_strs.issuperset(v.included_strs)
            self.included_strs.update(v.included_strs)
            if widen and changed:
                self.included_strs = None
            return changed
        if self.included_strs is not None:
            if v.is_not_str():
                return False
            else:
                if v.string is not None:
                    if v.string in self.included_strs:
                        return False
                    elif not widen:
                        self.included_strs = {s for s in self.included_strs}
                        self.included_strs.add(v.string)
                        return True
                self.included_strs = None
                return True
        if v.included_strs is not None:
            if self.is_not_str():
                self.included_strs = v.included_strs
                return True
            else:
                if self.string is not None:
                    if self.string in v.included_strs:
                        self.included_strs = v.included_strs
                        return True
                    else:
                        self.included_strs = {s for s in v.included_strs}
                        self.included_strs.add(self.string)
                        return True
                return False
        return False

    def join_excluded_strs(self, v: 'Value', widen: bool = True) -> bool:
        if self.excluded_strs is None and v.excluded_strs is None:
            return False
        new_excluded_strs = {*()} if self.excluded_strs is None else {s for s in self.excluded_strs if
                                                                      not v.is_maybe_str(s)}
        if v.excluded_strs is not None:
            new_excluded_strs.update({s for s in v.excluded_strs if not self.is_maybe_str(s)})
        if len(new_excluded_strs) == 0:
            new_excluded_strs = None
        if widen and new_excluded_strs is not None and self.excluded_strs is not None and \
                new_excluded_strs != self.excluded_strs:
            new_excluded_strs = None
        changed = ((new_excluded_strs is None) != (self.excluded_strs is None)) or \
                  (new_excluded_strs is not None and new_excluded_strs != self.excluded_strs)
        self.excluded_strs = new_excluded_strs
        return changed

    def join_single_str_as_fuzzy(self, s: str) -> bool:
        old_flags = self.flags
        self.flags |= STR_OTHER
        return self.flags != old_flags

    def join_single_str(self, v: 'Value') -> bool:
        modified = False
        if self.string is not None:
            if v.string is not None:
                if self.string == v.string:
                    return False
                else:
                    self.included_strs = {self.string, v.string}
                    modified = True
            else:
                if (v.flags & STR) != 0:
                    old_str = self.string
                    self.string = None
                    self.join_single_str_as_fuzzy(old_str)
                    modified = True
        elif v.string is not None:
            if (self.flags & STR) == 0:
                self.string = v.string
                modified = True
            else:
                modified = self.join_single_str_as_fuzzy(v.string)
        if self.included_strs is not None and v.string is not None:
            modified = v.string in self.included_strs
            if modified:
                self.included_strs = {s for s in self.included_strs}
                self.included_strs.add(v.string)
        return modified

    def is_maybe_str(self, s: str) -> bool:
        self.check_not_polymorphic_or_unknown()
        if self.excluded_strs is not None and s in self.excluded_strs:
            return False
        if self.included_strs is not None and s not in self.included_strs:
            return False
        return self.is_maybe_str_ignore_included_excluded(s)

    def is_maybe_str_ignore_included_excluded(self, s: str) -> bool:
        if self.string is not None:
            return self.string == s
        else:
            return (self.flags & STR_OTHER) != 0

    def restrict_to_not_strs(self, ss: Set[str]) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.is_not_str():
            return self
        ss = {s for s in ss if self.is_maybe_str(s)}
        if len(ss) == 0:
            return self
        ret = Value.from_value(self)
        if self.string is not None:
            if self.string in ss:
                ret.string = None
        elif ret.included_strs is not None:
            ret.included_strs = ret.included_strs.difference(ss)
            if len(ret.included_strs) == 0:
                ret.string = ret.included_strs = None
                ret.flags &= ~STR
            else:
                ret.fix_singleton_included()
        else:
            ret.excluded_strs = (ss if self.excluded_strs is None
                                 else ss.union(self.excluded_strs))
        return self.canonicalize(ret)

    def fix_singleton_included(self):
        if self.included_strs is not None and len(self.included_strs) == 1:
            self.string = next(iter(self.included_strs))
            self.included_strs = None
            self.flags &= ~STR

    def forget_excluded_included_strs(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.excluded_strs is None and self.included_strs is None:
            return self
        ret = Value.from_value(self)
        ret.excluded_strs = ret.included_strs = None
        return self.canonicalize(ret)

    def restrict_to_str(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        ret = Value()
        ret.flags = self.flags & STR
        ret.string = self.string
        ret.excluded_strs = self.excluded_strs
        ret.included_strs = self.included_strs
        return self.canonicalize(ret)

    def restrict_to_not_str(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        ret = Value.from_value(self)
        ret.flags &= ~STR
        ret.string = None
        ret.excluded_strs = ret.included_strs = None
        return self.canonicalize(ret)

    def is_maybe_all_known_str(self) -> bool:
        return self.is_maybe_single_str() or self.included_strs is not None

    def get_all_known_str(self) -> Set[str]:
        if self.is_maybe_single_str():
            return {self.string}
        elif self.included_strs is not None:
            return self.included_strs
        else:
            raise ValueError("Getting known strings form a value with all known strings")

    def join_obj(self, l: ObjLabel) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.obj_labels is not None and l in self.obj_labels:
            return self
        ret = Value.from_value(self)
        if ret.obj_labels is None:
            ret.obj_labels = {*()}
        else:
            ret.obj_labels = {obj_l for obj_l in ret.obj_labels}
        ret.obj_labels.add(l)
        return self.canonicalize(ret)

    def restrict_to_function(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        ret = Value.from_value(self)
        ret.flags &= ~PRIMITIVE
        ret.int_num = ret.float_num = ret.string = None
        ret.excluded_strs = ret.included_strs = None
        ret.getters = ret.setters = None
        ret.obj_labels = {*()}
        if self.obj_labels is not None:
            for l in self.obj_labels:
                if l.get_kind() == LabelKind.Function:
                    ret.obj_labels.add(l)
        if len(ret.obj_labels) == 0:
            ret.obj_labels = None
        return self.canonicalize(ret)

    def restrict_to_not_function(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        ret = Value.from_value(self)
        ret.obj_labels = {*()}
        if self.obj_labels is not None:
            for l in self.obj_labels:
                if l.get_kind() != LabelKind.Function:
                    ret.obj_labels.add(l)
        if len(ret.obj_labels) == 0:
            ret.obj_labels = None
        return self.canonicalize(ret)

    def remove_objs(self, objs: Set[ObjLabel]) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        self.check_no_getters_setters()
        if self.obj_labels is None:
            return self
        ret = Value.from_value(self)
        ret.obj_labels = ret.obj_labels.difference(objs)
        if len(ret.obj_labels) == 0:
            ret.obj_labels = None
        return self.canonicalize(ret)

    def restrict_to_obj(self, objs: Set[ObjLabel]) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if self.obj_labels is None:
            return self
        ret = Value.from_value(self)
        ret.flags &= ~PRIMITIVE
        ret.int_num = ret.float_num = ret.string = None
        ret.excluded_strs = ret.included_strs = None
        ret.obj_labels = self.obj_labels.intersection(objs)
        if len(ret.obj_labels) == 0:
            ret.obj_labels = None
        return self.canonicalize(ret)

    def make_getter(self) -> 'Value':
        ret = Value.from_value(self)
        ret.getters = self.obj_labels
        ret.obj_labels = None
        return self.canonicalize(ret)

    def make_setter(self) -> 'Value':
        ret = Value.from_value(self)
        ret.setters = self.obj_labels
        ret.obj_labels = None
        return self.canonicalize(ret)

    def rename(self, s: Renamings) -> 'Value':
        if self.is_unknown() or self.is_polymorphic():
            return self
        ss = s.rename_obj_labels(self.obj_labels)
        ss_getters = s.rename_obj_labels(self.getters)
        ss_setters = s.rename_obj_labels(self.setters)
        if ((ss is None or ss == self.obj_labels) and
                (ss_getters is None or ss_getters == self.getters) and
                (ss_setters is None or ss_setters == self.setters)):
            return self
        ret = Value.from_value(self)
        if ss is not None and len(ss) == 0:
            ss = None
        ret.obj_labels = ss
        if ss_getters is not None and len(ss_getters) == 0:
            ss_getters = None
        ret.getters = ss_getters
        if ss_setters is not None and len(ss_setters) == 0:
            ss_setters = None
        ret.setters = ss_setters
        ret.flags |= MODIFIED
        return self.canonicalize(ret)

    def is_maybe_present(self) -> bool:
        self.check_not_unknown()
        if self.is_polymorphic():
            return (self.flags & (PRESENT_DATA | PRESENT_ACCESOR)) != 0
        else:
            return (self.flags & PRIMITIVE) != 0 or \
                   self.int_num is not None or self.float_num is not None or self.string is not None or \
                   self.obj_labels is not None or self.getters is not None or self.setters is not None

    def is_maybe_present_data(self) -> bool:
        self.check_not_unknown()
        if self.is_polymorphic():
            return (self.flags & PRESENT_DATA) != 0
        else:
            return (self.flags & PRIMITIVE) != 0 or self.obj_labels is not None or \
                   self.int_num is not None or self.float_num is not None or self.string is not None

    def is_maybe_present_accessor(self) -> bool:
        self.check_not_unknown()
        if self.is_polymorphic():
            return (self.flags & PRESENT_DATA) != 0
        else:
            return self.getters is not None or self.setters is not None

    def is_maybe_polymorphic_present(self) -> bool:
        return (self.flags & (PRESENT_DATA | PRESENT_ACCESOR)) != 0

    def is_maybe_present_or_unknown(self) -> bool:
        return self.is_unknown() or self.is_maybe_present()

    def is_not_present(self) -> bool:
        self.check_not_unknown()
        return not self.is_maybe_present()

    def is_not_present_not_absent(self) -> bool:
        self.check_not_unknown()
        return not self.is_maybe_present() and not self.is_maybe_present()

    def is_maybe_obj(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return self.obj_labels is not None and len(self.obj_labels) > 0

    def is_maybe_getter(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return self.getters is not None

    def is_maybe_setter(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return self.setters is not None

    def is_maybe_getter_or_setter(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return self.getters is not None and self.setters is not None

    def is_maybe_primitive(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & PRIMITIVE) != 0 or \
               self.int_num is not None or self.float_num is not None or self.string is not None

    def get_obj_labels(self) -> Set[ObjLabel]:
        return set_deoptional(self.obj_labels)

    def get_all_obj_labels(self) -> Set[ObjLabel]:
        if self.obj_labels is None and self.getters is None and self.setters is None:
            return {*()}
        if self.getters is None and self.setters is None:
            return self.get_obj_labels()
        return (lambda u, v, w: u.union(v).union(w))(
            set_deoptional(self.obj_labels),
            set_deoptional(self.setters), set_deoptional(self.getters))

    def get_getters(self) -> Set[ObjLabel]:
        return set_deoptional(self.getters)

    def get_setters(self) -> Set[ObjLabel]:
        return set_deoptional(self.setters)

    def replace_obj_label(self, src_l: ObjLabel, tgt_l: ObjLabel) -> 'Value':
        assert src_l == tgt_l, "Equal object labels not expected"
        if ((self.obj_labels is None or src_l not in self.obj_labels) and
                (self.getters is None or src_l not in self.getters) and
                (self.setters is None or src_l not in self.setters)):
            return self
        ret = Value.from_value(self)
        if self.obj_labels is not None:
            ret.obj_labels = {(l if l != src_l else tgt_l) for l in self.obj_labels}
        if self.setters is not None:
            ret.setters = {(l if l != src_l else tgt_l) for l in self.setters}
        if self.getters is not None:
            ret.getters = {(l if l != src_l else tgt_l) for l in self.getters}
        return self.canonicalize(ret)

    def assert_non_empty(self):
        self.check_not_unknown()
        assert (self.is_polymorphic() and (self.flags & PRIMITIVE) == 0 and
                self.int_num is None and self.float_num is None and
                self.string is None and self.obj_labels is None and
                self.getters is None and self.getters is None
                ), "Empty Value"

    def __sizeof__(self) -> int:
        if self.is_unknown() or self.is_polymorphic():
            return 0
        cnt = 0
        if not self.is_not_bool():
            cnt += 1
        if not self.is_not_str():
            cnt += 1
        if not self.is_not_int():
            cnt += 1
        if not self.is_not_float():
            cnt += 1
        if self.obj_labels is not None:
            cnt += 1
        if self.getters is not None:
            cnt += 1
        if self.setters is not None:
            cnt += 1
        if cnt == 0 and self.is_maybe_none():
            cnt = 1
        return cnt

    def restrict_to_attributes(self) -> 'Value':
        ret = Value.from_value(self)
        ret.int_num = ret.float_num = ret.string = ret.var = None
        ret.excluded_strs = ret.included_strs = None
        ret.flags &= ATTR | ABSENT | UNKNOWN
        if not self.is_unknown() and self.is_maybe_present():
            ret.flags |= UNDEF
        return self.canonicalize(ret)

    def restrict_to_not_attributes(self) -> 'Value':
        ret = Value.from_value(self)
        ret.flags &= ~(PROPERTYDATA | ABSENT | PRESENT_DATA | PRESENT_ACCESOR)
        return self.canonicalize(ret)

    def replace_value(self, v: 'Value') -> 'Value':
        ret = Value.from_value(v)
        ret.flags &= ~(PROPERTYDATA | ABSENT | PRESENT_DATA | PRESENT_ACCESOR)
        ret.flags |= self.flags & (PROPERTYDATA | ABSENT)
        if ret.var is not None:
            ret.flags |= self.flags & (PRESENT_DATA | PRESENT_ACCESOR)
        return self.canonicalize(ret)

    def is_maybe_single_obj_label(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return self.obj_labels is not None and len(self.obj_labels) == 1

    def is_maybe_single_allocation_site(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return self.obj_labels is not None and len(self.get_obj_source_locations()) == 1

    def is_not_a_summarized_obj(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return not any(not l.is_singleton() for l in set_deoptional(self.obj_labels))

    def is_not_a_singleton_obj(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return not any(l.is_singleton() for l in set_deoptional(self.obj_labels))

    def contains_obj_label(self, l: ObjLabel) -> bool:
        return (l in set_deoptional(self.obj_labels) or
                l in set_deoptional(self.getters) or
                l in set_deoptional(self.setters))

    def restrict_to_not_int_zero(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if not self.is_maybe_zero():
            return self
        ret = Value.from_value(self)
        if ret.int_num is not None and self.is_zero(ret.int_num):
            ret.int_num = None
        ret.flags &= ~INT_ZERO
        return self.canonicalize(ret)

    def restrict_to_not_float_zero(self) -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if not self.is_maybe_zero():
            return self
        ret = Value.from_value(self)
        if ret.float_num is not None and self.is_zero(ret.float_num):
            ret.float_num = None
        ret.flags &= ~FLOAT_ZERO
        return self.canonicalize(ret)

    def is_maybe_zero(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return self.is_maybe_int_zero() or self.is_maybe_float_zero()

    def is_maybe_int_zero(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        if self.int_num is not None and self.is_zero(self.int_num):
            return True
        return (self.flags & INT_ZERO) != 0

    def is_maybe_int_pos(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & INT_POS) != 0

    def is_maybe_float_pos(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        return (self.flags & FLOAT_POS) != 0

    def is_maybe_float_zero(self) -> bool:
        self.check_not_polymorphic_or_unknown()
        if self.float_num is not None and self.is_zero(self.float_num):
            return True
        return (self.flags & FLOAT_ZERO) != 0

    def is_maybe_same_int(self, v: 'Value') -> bool:
        self.check_not_polymorphic_or_unknown()
        if self.int_num is not None:
            return v.is_maybe_int(self.int_num)
        if v.int_num is not None:
            return self.is_maybe_int(v.int_num)
        return (self.flags & v.flags & INT) != 0

    def is_maybe_same_float(self, v: 'Value') -> bool:
        self.check_not_polymorphic_or_unknown()
        if self.float_num is not None:
            return v.is_maybe_float(self.float_num)
        if v.float_num is not None:
            return self.is_maybe_float(v.float_num)
        return (self.flags & v.flags & FLOAT) != 0

    def is_maybe_same_int_when_negated(self, v: 'Value') -> bool:
        self.check_not_polymorphic_or_unknown()
        if self.int_num is not None:
            return v.is_maybe_int(-self.int_num)
        if v.int_num is not None:
            return self.is_maybe_int(-v.int_num)
        maybe_pos = (self.flags & INT_POS) != 0
        maybe_neg = (self.flags & INT_OTHER) != 0
        maybe_zero = (self.flags & INT_ZERO) != 0
        v_maybe_pos = (v.flags & INT_POS) != 0
        v_maybe_neg = (v.flags & INT_OTHER) != 0
        v_maybe_zero = (v.flags & INT_ZERO) != 0
        maybe_pos_neg = maybe_pos and v_maybe_neg
        maybe_neg_pos = maybe_neg and v_maybe_pos
        maybe_zero_zero = maybe_zero and v_maybe_zero
        return maybe_pos_neg or maybe_pos_neg or maybe_zero_zero

    def is_maybe_same_float_when_negated(self, v: 'Value') -> bool:
        self.check_not_polymorphic_or_unknown()
        if self.float_num is not None:
            return v.is_maybe_float(-self.float_num)
        if v.float_num is not None:
            return self.is_maybe_float(-v.float_num)
        maybe_pos = (self.flags & FLOAT_POS) != 0
        maybe_neg = (self.flags & FLOAT_OTHER) != 0
        maybe_zero = (self.flags & FLOAT_ZERO) != 0
        v_maybe_pos = (v.flags & FLOAT_POS) != 0
        v_maybe_neg = (v.flags & FLOAT_OTHER) != 0
        v_maybe_zero = (v.flags & FLOAT_ZERO) != 0
        maybe_pos_neg = maybe_pos and v_maybe_neg
        maybe_neg_pos = maybe_neg and v_maybe_pos
        maybe_zero_zero = maybe_zero and v_maybe_zero
        return maybe_pos_neg or maybe_pos_neg or maybe_zero_zero

    def restrict_to_strict_equals(self, v: 'Value') -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if v.getters is not None:
            return self
        ret = Value.from_value(self)
        ret.flags &= v.flags | ~(BOOL | NONE)
        if (v.flags & (UNDEF | ABSENT)) == 0:
            ret.flags &= ~(UNDEF | ABSENT)
        if self.is_maybe_single_int():
            if not v.is_maybe_int(self.int_num):
                ret.int_num = None
        else:
            if v.is_maybe_single_int():
                if self.is_maybe_int(v.int_num):
                    ret.int_num = v.int_num
                ret.flags &= ~INT
        if self.is_maybe_single_float():
            if not v.is_maybe_float(self.float_num):
                ret.float_num = None
        else:
            if v.is_maybe_single_float():
                if self.is_maybe_float(v.float_num):
                    ret.float_num = v.float_num
                ret.flags &= ~FLOAT
        if self.is_maybe_single_str():
            if not v.is_maybe_str(self.string):
                ret.string = None
        elif v.is_maybe_single_str():
            if self.is_maybe_str(v.string):
                ret.string = v.string
            else:
                ret.string = None
            ret.flags &= ~STR
            ret.included_strs = ret.excluded_strs = None
        else:
            if self.included_strs is not None or v.included_strs is not None:
                if self.included_strs is not None:
                    ret.included_strs = {s for s in ret.included_strs}
                    if v.included_strs is not None:
                        ret.included_strs.intersection_update(v.included_strs)
                    else:
                        for s in ret.included_strs:
                            if not v.is_maybe_str(s):
                                ret.included_strs.remove(s)
                else:
                    ret.included_strs = {s for s in v.included_strs if self.is_maybe_str(s)}
                ret.excluded_strs = None
                ret.string = None
                ret.flags &= ~STR
                for s in ret.included_strs:
                    ret.join_single_float_as_fuzzy(s)
                ret.fix_singleton_included()
                if ret.included_strs is not None and len(ret.included_strs) == 0:
                    ret.included_strs = None
            else:
                ret.flags &= v.flags | ~STR
                if v.excluded_strs is not None:
                    ret.excluded_strs = set_deoptional(ret.excluded_strs).union(v.excluded_strs)
                if ret.excluded_strs is not None:
                    ret.excluded_strs = {s for s in ret.excluded_strs if ret.is_maybe_str_ignore_included_excluded(s)}
                    if len(ret.excluded_strs) == 0:
                        ret.excluded_strs = None
        if v.obj_labels is None:
            ret.obj_labels = None
        elif ret.obj_labels is not None:
            ret.obj_labels = ret.obj_labels.intersection(v.obj_labels)
            if len(ret.obj_labels) == 0:
                ret.obj_labels = None
        return self.canonicalize(ret)

    def summarize(self, s: Summarized) -> 'Value':
        if self.is_unknown() or self.is_polymorphic():
            return self
        ss = s.summarize(self.get_obj_labels())
        if ss == self.get_obj_labels():
            return self
        r = Value.from_value(self)
        r.obj_labels = ss if len(ss) > 0 else None
        return self.canonicalize(r)

    def restrict_to_strict_not_equals(self, v: 'Value') -> 'Value':
        self.check_not_polymorphic_or_unknown()
        if (v.is_maybe_fuzzy_str() or v.is_maybe_fuzzy_int() or v.is_maybe_fuzzy_float() or
                (v.obj_labels is not None and (len(v.obj_labels) > 1 or not next(iter(v.obj_labels)).is_singleton()))):
            return self
        v_is_undef_or_absent = v.is_maybe_undef() or v.is_maybe_absent()
        v_is_none = v.is_maybe_none()
        v_is_true = v.is_maybe_true()
        v_is_false = v.is_maybe_false()
        v_is_str = not v.is_not_str()
        v_is_int = not v.is_not_int()
        v_is_float = not v.is_not_float()
        v_is_obj = v.obj_labels is not None
        if int(v_is_undef_or_absent) + int(v_is_none) + int(v_is_true) + int(v_is_false) + int(v_is_str) + \
                int(v_is_int) + int(v_is_float) + int(v_is_obj) != 1:
            return self
        if v_is_str:
            return self.restrict_to_not_strs({v.get_str()})
        else:
            ret = Value.from_value(self)
            if v_is_undef_or_absent:
                ret.flags &= ~(UNDEF | ABSENT)
            elif v_is_none:
                ret.flags &= ~NONE
            elif v_is_true:
                ret.flags &= ~BOOL_TRUE
            elif v_is_false:
                ret.flags &= ~BOOL_FALSE
            elif v_is_int:
                v_i = v.get_int()
                if ret.int_num is not None and ret.int_num == v_i:
                    ret.int_num = None
            elif v_is_float:
                v_f = v.get_float()
                if ret.float_num is not None and ret.float_num == v_f:
                    ret.float_num = None
            elif ret.obj_labels is not None:
                ret.obj_labels = {s for s in ret.obj_labels}
                if v.obj_labels is not None:
                    ret.obj_labels.remove(next(iter(v.obj_labels)))
                if len(ret.obj_labels) == 0:
                    ret.obj_labels = None
            return self.canonicalize(ret)

    def restrict_to_loose_equals(self, v: 'Value') -> 'Value':
        if v.obj_labels is not None:
            return self
        ret = Value.from_value(self)
        if self.included_strs is not None or v.included_strs is not None:
            if self.included_strs is not None:
                if v.included_strs is not None:
                    ret.included_strs = ret.included_strs.intersection(v.included_strs)
                else:
                    ret.included_strs = {s for s in ret.included_strs if v.is_maybe_str(s)}
            else:
                ret.included_strs = {s for s in v.included_strs if self.is_maybe_str(s)}
            ret.excluded_strs = None
        else:
            v_is_not_undef_absent_or_none = not (v.is_maybe_undef() or v.is_maybe_absent() or v.is_maybe_none())
            v_is_not_true = not v.is_maybe_true()
            v_is_not_false = not v.is_maybe_false()
            v_is_not_str = v.is_not_str()
            v_is_not_int = v.is_not_int()
            v_is_not_float = v.is_not_float()
            v_is_not_zero = not v.is_maybe_zero()
            v_is_not_empty_str = not v.is_maybe_str("")
            if v_is_not_undef_absent_or_none:
                ret.flags &= ~(UNDEF | ABSENT | NONE)
            if v_is_not_true:
                ret.flags &= ~BOOL_TRUE
            if v_is_not_str and v_is_not_int and v_is_not_float and v_is_not_false:
                ret.flags &= ~STR
                ret.string = None
                ret.included_strs = ret.excluded_strs = None
            if v_is_not_int and v_is_not_false and v_is_not_empty_str:
                ret.flags &= ~INT
                ret.int_num = None
            if v_is_not_float and v_is_not_false and v_is_not_empty_str:
                ret.flags &= ~FLOAT
                ret.float_num = None
            if v_is_not_zero and v_is_not_false and v_is_not_empty_str:
                ret.float_num &= ~(BOOL_FALSE | INT_ZERO | FLOAT_ZERO)
                if ret.int_num is not None and ret.int_num == 0:
                    ret.int_num = None
                if ret.float_num is not None and ret.float_num == 0:
                    ret.float_num = None
                ret.remove_included_add_excluded_str("")
        ret.cleanup_included_excluded()
        return self.canonicalize(ret)

    def restrict_to_loose_not_equals(self, v: 'Value') -> 'Value':
        if v.is_maybe_fuzzy_str() or v.is_maybe_fuzzy_int() or v.is_maybe_fuzzy_float() or v.obj_labels is not None:
            return self
        v_is_undef_or_absent_or_none = v.is_maybe_undef() or v.is_maybe_absent() or v.is_maybe_none()
        v_is_true = v.is_maybe_true()
        v_is_false = v.is_maybe_false()
        v_is_str = not v.is_not_str()
        v_is_int = not v.is_not_int()
        v_is_float = not v.is_not_float()
        if (int(v_is_undef_or_absent_or_none) + int(v_is_true) + int(v_is_false) +
            int(v_is_int) + int(v_is_float) + int(v_is_str)) != 1:
            return self
        v_is_int_zero = v_is_int and self.is_zero(v.int_num)
        v_is_float_zero = v_is_float and self.is_zero(v.float_num)
        v_is_str_empty = v_is_str and len("" if v.string is None else v.string) == 0
        ret = Value.from_value(self)
        if v_is_undef_or_absent_or_none:
            ret.flags &= ~(UNDEF | ABSENT | NONE)
        elif v_is_true:
            ret.flags &= ~BOOL_TRUE
        elif v_is_int_zero or v_is_float_zero or v_is_false:
            if ret.int_num is not None and self.is_zero(ret.int_num):
                ret.int_num = None
            if ret.float_num is not None and self.is_zero(ret.float_num):
                ret.float_num = None
            ret.flags &= ~(INT_ZERO | FLOAT_ZERO | BOOL_FALSE)
            ret.remove_included_add_excluded_str("")
        elif v_is_int:
            if ret.int_num is not None and ret.int_num == v.int_num:
                ret.int_num = None
        elif v_is_float:
            if ret.float_num is not None and ret.float_num == v.float_num:
                ret.float_num = None
        elif v_is_str_empty:
            ret.flags &= ~(INT_ZERO | FLOAT_ZERO | BOOL_FALSE)
            if ret.is_maybe_single_str() and ret.string == v.string:
                ret.string = None
            ret.remove_included_add_excluded_str(v.string)
        else:
            if ret.is_maybe_single_str() and ret.string == v.string:
                ret.str = None
            ret.remove_included_add_excluded_str(v.string)
        ret.cleanup_included_excluded()
        return self.canonicalize(ret)

    def cleanup_included_excluded(self):
        if self.included_strs is not None:
            self.flags &= ~STR
            self.excluded_strs = None
            for s in self.included_strs:
                self.join_single_str_as_fuzzy(s)
            self.fix_singleton_included()
            if self.included_strs is not None and len(self.included_strs) == 0:
                self.included_strs = None
        if self.excluded_strs is not None:
            self.excluded_strs = {s for s in self.excluded_strs if self.is_maybe_str_ignore_included_excluded(s)}
            if self.is_maybe_single_str() and self.string in self.excluded_strs:
                self.excluded_strs.remove(self.string)
                self.string = None
            if len(self.excluded_strs) == 0:
                self.excluded_strs = None

    def remove_included_add_excluded_str(self, string: str):
        if self.included_strs is not None:
            self.included_strs = {s for s in self.included_strs if s != string}
            self.fix_singleton_included()
            if self.included_strs is not None and len(self.included_strs) == 0:
                self.included_strs = None
        else:
            self.excluded_strs = set_deoptional(self.excluded_strs).union({string})


class ValueDefault:
    the_none = Value.really_make_none()
    the_none_modified = Value.really_make_none_modified()
    the_undef = Value.really_make_undef()
    the_bool_any = Value.really_make_bool(None)
    the_bool_true = Value.really_make_bool(True)
    the_bool_false = Value.really_make_bool(False)
    the_str_any = Value.really_make_any_str()
    the_int_any = Value.really_make_any_int()
    the_float_any = Value.really_make_any_float()
    the_absent = Value.really_make_absent()
    the_absent_modified = Value.really_make_absent_modified()
    the_unknown = Value.really_make_unknown()
