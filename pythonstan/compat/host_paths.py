"""The host filesystem protocol for an erased legacy filename capability."""

from collections.abc import Callable
from os import fspath as _builtin_fspath
from typing import Final, cast

# The fixed stdlib primitive either invokes the type-level filesystem slot and
# returns text/bytes, or raises its real TypeError. Erasing the input here does
# not promise that an arbitrary host object implements that protocol.
_FSPATH_OBJECT: Final = cast(Callable[[object], str | bytes], _builtin_fspath)


def filesystem_path(value: object) -> str | bytes:
    """Invoke the actual special-method protocol, including subclass behavior."""
    return _FSPATH_OBJECT(value)
