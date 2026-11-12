"""Frozen Python 3.12 builtin exception identity and inheritance declarations.

The parent graph was cross-checked against Python 3.12's official builtin
exceptions reference and the trusted CPython 3.12 runtime, never target code.
Aliases are equal class identities, not subclasses. User classes must be
resolved through source/store identities before consulting this builtin graph.
Unknown names return an empty ancestor set; they do not prove nonmatching.

References:
https://docs.python.org/3.12/library/exceptions.html
"""

from types import MappingProxyType

EXCEPTION_MODEL_VERSION: str = "python-3.12-builtin-exceptions-v1"

EXCEPTION_PARENTS: MappingProxyType[str, tuple[str, ...]] = MappingProxyType(
    {
        "ArithmeticError": ("Exception",),
        "AssertionError": ("Exception",),
        "AttributeError": ("Exception",),
        "BaseException": (),
        "BaseExceptionGroup": ("BaseException",),
        "BlockingIOError": ("OSError",),
        "BrokenPipeError": ("ConnectionError",),
        "BufferError": ("Exception",),
        "BytesWarning": ("Warning",),
        "ChildProcessError": ("OSError",),
        "ConnectionAbortedError": ("ConnectionError",),
        "ConnectionError": ("OSError",),
        "ConnectionRefusedError": ("ConnectionError",),
        "ConnectionResetError": ("ConnectionError",),
        "DeprecationWarning": ("Warning",),
        "EOFError": ("Exception",),
        "EncodingWarning": ("Warning",),
        "Exception": ("BaseException",),
        "ExceptionGroup": ("BaseExceptionGroup", "Exception"),
        "FileExistsError": ("OSError",),
        "FileNotFoundError": ("OSError",),
        "FloatingPointError": ("ArithmeticError",),
        "FutureWarning": ("Warning",),
        "GeneratorExit": ("BaseException",),
        "ImportError": ("Exception",),
        "ImportWarning": ("Warning",),
        "IndentationError": ("SyntaxError",),
        "IndexError": ("LookupError",),
        "InterruptedError": ("OSError",),
        "IsADirectoryError": ("OSError",),
        "KeyError": ("LookupError",),
        "KeyboardInterrupt": ("BaseException",),
        "LookupError": ("Exception",),
        "MemoryError": ("Exception",),
        "ModuleNotFoundError": ("ImportError",),
        "NameError": ("Exception",),
        "NotADirectoryError": ("OSError",),
        "NotImplementedError": ("RuntimeError",),
        "OSError": ("Exception",),
        "OverflowError": ("ArithmeticError",),
        "PendingDeprecationWarning": ("Warning",),
        "PermissionError": ("OSError",),
        "ProcessLookupError": ("OSError",),
        "RecursionError": ("RuntimeError",),
        "ReferenceError": ("Exception",),
        "ResourceWarning": ("Warning",),
        "RuntimeError": ("Exception",),
        "RuntimeWarning": ("Warning",),
        "StopAsyncIteration": ("Exception",),
        "StopIteration": ("Exception",),
        "SyntaxError": ("Exception",),
        "SyntaxWarning": ("Warning",),
        "SystemError": ("Exception",),
        "SystemExit": ("BaseException",),
        "TabError": ("IndentationError",),
        "TimeoutError": ("OSError",),
        "TypeError": ("Exception",),
        "UnboundLocalError": ("NameError",),
        "UnicodeDecodeError": ("UnicodeError",),
        "UnicodeEncodeError": ("UnicodeError",),
        "UnicodeError": ("ValueError",),
        "UnicodeTranslateError": ("UnicodeError",),
        "UnicodeWarning": ("Warning",),
        "UserWarning": ("Warning",),
        "ValueError": ("Exception",),
        "Warning": ("Exception",),
        "ZeroDivisionError": ("ArithmeticError",),
    }
)

EXCEPTION_ALIASES: MappingProxyType[str, str] = MappingProxyType(
    {"EnvironmentError": "OSError", "IOError": "OSError"}
)

EXCEPTION_NAMES: frozenset[str] = frozenset(EXCEPTION_PARENTS) | frozenset(EXCEPTION_ALIASES)


def canonical_builtin_exception_name(name: str | None) -> str | None:
    """Return the builtin class identity name, or None for an unknown name."""
    if name is None or name not in EXCEPTION_NAMES:
        return None
    return EXCEPTION_ALIASES.get(name, name)


def require_builtin_exception_name(name: str) -> str:
    """Resolve a required declared builtin exception identity."""
    canonical = canonical_builtin_exception_name(name)
    if canonical is None:
        raise ValueError(f"Unknown builtin exception: {name}")
    return canonical


def builtin_exception_ancestors(name: str | None) -> frozenset[str]:
    """Known builtin ancestors including self and equivalent alias names.

    An empty result means unavailable builtin evidence, not proof that an
    arbitrary user class is unrelated to a handler.
    """
    canonical = canonical_builtin_exception_name(name)
    if canonical is None:
        return frozenset()
    result: set[str] = set()
    pending = [canonical]
    while pending:
        current = pending.pop()
        if current in result:
            continue
        result.add(current)
        pending.extend(EXCEPTION_PARENTS[current])
    result.update(alias for alias, target in EXCEPTION_ALIASES.items() if target in result)
    return frozenset(result)
