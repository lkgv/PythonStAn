"""Legacy representation exports. Python operations live in shared Source Transfer."""
from .base import Domain,DomainRegistry
from .strings import StringDomain,StrLattice
from .ints import IntDomain,IntLattice
from .bool import BoolDomain,BoolLattice
from .none import NoneDomain,NoneLattice
from .callable import CallableDomain,CallableVal,FunctionRef,BoundMethodRef
from .type_domain import TypeDomain,TypeVal,ClassRef
from .descriptor import DescriptorDomain,DescriptorVal,DescriptorRef,DescriptorType,PropertyRef,ClassMethodRef,StaticMethodRef
from .mapslots import SlotMap,Slot,Presence,KeySig,LoadResult,SlotMapSemantics,DictSemantics,KeyDecomposer,make_str_keysig,make_int_keysig,keysig_kind,keysig_value
from .binding import to_callable_targets,bind_descriptor
from .kwargs import KwShape,KwShapeDisj,ArgKwShape,materialize_kwargs_shape,expand_kwargs
from .tuples import TupleExact,TupleSummary,TupleDisj,materialize_tuple_disj,expand_starred_arg
