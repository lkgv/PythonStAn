"""Stdlib-only ownership of a disposable POSIX project worker.

This does not import the native analyzer or a repository scripts package.
"""
import os
import selectors
import signal
import subprocess
import time

WORKER_CLEANUP_GRACE_SECONDS = 1.0
MAX_STDOUT_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
PIPE_CHUNK_BYTES = 65536
PROCESS_TRANSPORT_PROTOCOL = "bounded_binary_selectors_v2"


class WorkerTimeout(RuntimeError):
    def __init__(self, termination, transport_health=None):
        super().__init__("worker communication deadline expired")
        self.termination = termination
        self.transport_health = transport_health or {}


class UnsupportedWorkerPlatform(RuntimeError):
    pass


def worker_limits():
    supported = _bounded_worker_supported()
    return {"cleanup_grace_seconds": WORKER_CLEANUP_GRACE_SECONDS,
            "process_group_supported": supported,
            "bounded_pipe_cleanup_supported": supported,
            "platform_support": "supported_posix" if supported else "unsupported_platform",
            "worker_exit_policy": "disposable_after_owned_output_flush",
            "process_transport_protocol": PROCESS_TRANSPORT_PROTOCOL,
            "stdout_retained_bytes_limit": MAX_STDOUT_BYTES,
            "stderr_retained_bytes_limit": MAX_STDERR_BYTES,
            "timeout_scope": "worker communication deadline; bounded cleanup is additional"}


def run_worker_process(command, *, input, cwd, timeout):
    if not _bounded_worker_supported():
        raise UnsupportedWorkerPlatform(
            "unsupported_platform: bounded worker lifecycle requires POSIX process groups")
    # Popen text mode normalizes bare CR to LF in communicate(), losing the
    # delimiter evidence needed by the receipt protocol. Keep the wire bytes.
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, cwd=cwd, start_new_session=True)
    output = bytearray()
    errors = bytearray()
    totals = {"stdout": 0, "stderr": 0}
    try:
        _pump(process, input.encode("utf-8"), time.monotonic() + timeout,
              output, errors, totals)
    except subprocess.TimeoutExpired as exc:
        termination = _terminate_worker(process, process_group=True)
        raise WorkerTimeout(termination, _transport_health(output, errors, totals)) from exc
    except BaseException:
        _terminate_worker(process, process_group=True)
        raise
    finally:
        _close_worker_pipes(process)
    # Preserve the existing str interface losslessly. The receipt reader
    # rejects surrogateescaped bytes in a candidate frame; unrelated noisy
    # diagnostics remain printable JSON strings and cannot fabricate a frame.
    completed = subprocess.CompletedProcess(process.args, process.returncode,
        bytes(output).decode("utf-8", errors="surrogateescape"),
        bytes(errors).decode("utf-8", errors="surrogateescape"))
    completed.transport_health = _transport_health(output, errors, totals)
    return completed


def _transport_health(output, errors, totals):
    return {"protocol": PROCESS_TRANSPORT_PROTOCOL,
            "stdout_total_bytes": totals["stdout"], "stderr_total_bytes": totals["stderr"],
            "stdout_retained_bytes": len(output), "stderr_retained_bytes": len(errors),
            "stdout_overflow": totals["stdout"] > MAX_STDOUT_BYTES,
            "stderr_tail_truncated": totals["stderr"] > MAX_STDERR_BYTES,
            "stdout_acceptance": "refused_overflow" if totals["stdout"] > MAX_STDOUT_BYTES else "within_limit"}


def _pump(process, input_bytes, deadline, output, errors, totals):
    """Drain binary pipes while retaining a capped stdout and stderr tail.

    EOF of both output pipes and child exit are required. A descendant keeping
    a pipe open cannot turn a deadline into successful worker completion.
    """
    with selectors.DefaultSelector() as selector:
        for name in ("stdout", "stderr"):
            stream = getattr(process, name)
            if stream is not None and not stream.closed:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
        remaining = memoryview(input_bytes)
        if process.stdin is not None and not process.stdin.closed:
            if remaining:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                process.stdin.close()
        while selector.get_map() or process.poll() is None:
            available = deadline - time.monotonic()
            if available <= 0:
                raise subprocess.TimeoutExpired(process.args, 0)
            ready = selector.select(min(available, 0.05))
            for key, _ in ready:
                name = key.data
                if name == "stdin":
                    try:
                        count = os.write(key.fd, remaining[:PIPE_CHUNK_BYTES])
                        remaining = remaining[count:]
                    except BrokenPipeError:
                        remaining = remaining[len(remaining):]
                    except BlockingIOError:
                        continue
                    if not remaining:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                try:
                    chunk = os.read(key.fd, PIPE_CHUNK_BYTES)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                totals[name] += len(chunk)
                if name == "stdout":
                    output.extend(chunk[:max(0, MAX_STDOUT_BYTES - len(output))])
                else:
                    errors.extend(chunk)
                    if len(errors) > MAX_STDERR_BYTES:
                        del errors[:-MAX_STDERR_BYTES]


def _bounded_worker_supported():
    # Windows communicate uses reader threads; closing a pipe from another
    # thread is not a verified bounded operation. Refuse before creating it.
    return os.name == "posix"


def _close_worker_pipes(process):
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(process, name, None)
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


def _terminate_worker(process, *, process_group):
    """Kill our worker group, then spend at most one grace period draining it.

    This is an owned POSIX process group, not a general process-forest sandbox:
    a descendant that creates a new session can escape it. No unbounded Popen
    context-manager wait is used, including when a descendant retains a pipe.
    """
    started = time.monotonic()
    deadline = started + WORKER_CLEANUP_GRACE_SECONDS
    errors = []
    try:
        if process_group:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass  # The owned group/child may have exited between observation and kill.
    except OSError as exc:
        errors.append(type(exc).__name__ + ": " + str(exc))
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except OSError as fallback:
            errors.append(type(fallback).__name__ + ": " + str(fallback))
    pipes_drained = False
    try:
        # Discard all cleanup bytes in bounded chunks. communicate() would
        # retain an unbounded second output copy even after the worker is killed.
        _pump(process, b"", deadline, bytearray(), bytearray(), {"stdout": 0, "stderr": 0})
        pipes_drained = True
    except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
        errors.append(type(exc).__name__ + ": " + str(exc))
    finally:
        _close_worker_pipes(process)
    return {"scope": "owned_posix_process_group" if process_group else "direct_child_only",
            "process_group_supported": process_group,
            "escaping_descendants_covered": False,
            "cleanup_grace_seconds": WORKER_CLEANUP_GRACE_SECONDS,
            "cleanup_wall_seconds": time.monotonic() - started,
            "child_reaped": process.poll() is not None,
            "child_returncode": process.returncode,
            "pipes_drained": pipes_drained, "pipes_closed": True,
            "errors": errors}
