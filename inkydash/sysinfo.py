"""Best-effort system facts for the status screen and `doctor`. Every function returns None
off the Pi instead of failing."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path


def ip_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET, nothing is sent for UDP connect
            return s.getsockname()[0]
    except OSError:
        return None


def hostname() -> str:
    return socket.gethostname()


def wifi() -> tuple[str | None, int | None]:
    """(SSID, signal quality 0–100)."""
    ssid = None
    try:
        ssid = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=2).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    quality = None
    try:
        for line in Path("/proc/net/wireless").read_text().splitlines()[2:]:
            parts = line.split()
            if len(parts) >= 3:
                q = float(parts[2].rstrip("."))
                quality = int(min(100, q / 70 * 100))
                break
    except (OSError, ValueError):
        pass
    return ssid, quality


def uptime_s() -> float | None:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def disk_free(path: str | Path = ".") -> tuple[int, int] | None:
    try:
        u = shutil.disk_usage(path)
        return u.free, u.total
    except OSError:
        return None


def clock_synced() -> bool | None:
    if os.name != "posix":
        return None
    p = Path("/run/systemd/timesync/synchronized")
    if p.exists():
        return True
    return False if Path("/run/systemd/timesync").exists() else None


def cpu_temp_c() -> float | None:
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
    except (OSError, ValueError):
        return None


def mem_available_mb() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return None


def rss_mb() -> int | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return None


def is_pi() -> bool:
    try:
        return "Raspberry Pi" in Path("/proc/device-tree/model").read_text(errors="ignore")
    except OSError:
        return False


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def human_duration(s: float) -> str:
    s = int(s)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m"
