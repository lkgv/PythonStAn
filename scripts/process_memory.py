"""Worker memory observations without a fork-inherited ru_maxrss floor."""
from pathlib import Path
import sys


def worker_memory():
    """Linux VmHWM belongs to the current exec; ru_maxrss may include its parent."""
    peak = None
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmHWM:"):
                    fields = line.split()
                    if len(fields) == 3 and fields[2] == "kB":
                        peak = int(fields[1]) * 1024
                    break
        except (OSError, ValueError):
            pass
    return {"peak_rss_bytes": peak,
            "peak_rss_method": "linux_proc_self_status_VmHWM" if peak is not None else "unavailable",
            "peak_rss_scope": "current exec process including analyzer startup; unavailable on hard timeout"}
