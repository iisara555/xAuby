import os
import sys
import ctypes
from datetime import datetime, timezone
from typing import Optional

# Global tracker for CPU usage calculation
PREV_CPU_TIMES = None

class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]

class FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", ctypes.c_ulong),
        ("dwHighDateTime", ctypes.c_ulong)
    ]

def get_ram_usage():
    if sys.platform == 'win32':
        try:
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            total_gb = stat.ullTotalPhys / (1024**3)
            avail_gb = stat.ullAvailPhys / (1024**3)
            used_gb = total_gb - avail_gb
            return stat.dwMemoryLoad, used_gb, total_gb
        except Exception:
            return None, 0, 0
    else:
        try:
            with open("/proc/meminfo", "r") as f:
                lines = f.readlines()
            mem_info = {}
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    mem_info[parts[0].rstrip(":")] = int(parts[1])
            if "MemTotal" in mem_info:
                total_kb = mem_info["MemTotal"]
                avail_kb = mem_info.get("MemAvailable")
                if avail_kb is None:
                    free_kb = mem_info.get("MemFree", 0)
                    buffers_kb = mem_info.get("Buffers", 0)
                    cached_kb = mem_info.get("Cached", 0)
                    avail_kb = free_kb + buffers_kb + cached_kb
                used_kb = total_kb - avail_kb
                load_pct = int((used_kb / total_kb) * 100)
                total_gb = total_kb / (1024 * 1024)
                used_gb = used_kb / (1024 * 1024)
                return load_pct, used_gb, total_gb
        except Exception:
            pass
        return None, 0, 0

def get_cpu_times():
    if sys.platform == 'win32':
        try:
            idleTime = FILETIME()
            kernelTime = FILETIME()
            userTime = FILETIME()
            if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idleTime), ctypes.byref(kernelTime), ctypes.byref(userTime)):
                idle = (idleTime.dwHighDateTime << 32) + idleTime.dwLowDateTime
                kernel = (kernelTime.dwHighDateTime << 32) + kernelTime.dwLowDateTime
                user = (userTime.dwHighDateTime << 32) + userTime.dwLowDateTime
                return idle, kernel, user
        except Exception:
            pass
    else:
        try:
            with open("/proc/stat", "r") as f:
                line = f.readline()
            if line.startswith("cpu"):
                parts = line.split()[1:]
                vals = [float(x) for x in parts]
                idle = vals[3] + vals[4]
                total = sum(vals)
                return idle, total
        except Exception:
            pass
    return None

def calculate_cpu_usage():
    global PREV_CPU_TIMES
    curr = get_cpu_times()
    if not curr:
        return None
    if PREV_CPU_TIMES is None or len(PREV_CPU_TIMES) != len(curr):
        PREV_CPU_TIMES = curr
        return 0.0
    
    if len(curr) == 3:
        idle_diff = curr[0] - PREV_CPU_TIMES[0]
        kernel_diff = curr[1] - PREV_CPU_TIMES[1]
        user_diff = curr[2] - PREV_CPU_TIMES[2]
        PREV_CPU_TIMES = curr
        total = kernel_diff + user_diff
        if total > 0:
            return (1.0 - idle_diff / total) * 100.0
    elif len(curr) == 2:
        idle_diff = curr[0] - PREV_CPU_TIMES[0]
        total_diff = curr[1] - PREV_CPU_TIMES[1]
        PREV_CPU_TIMES = curr
        if total_diff > 0:
            return (1.0 - idle_diff / total_diff) * 100.0
    return 0.0

def get_db_size(db_path: Optional[str] = None) -> float:
    try:
        if db_path is None:
            from xauby.database.db import resolve_db_path
            db_path = resolve_db_path()
        if os.path.exists(db_path):
            return os.path.getsize(db_path) / (1024 * 1024)
    except Exception:
        pass
    return 0.0

def format_uptime(started_at_str: Optional[str]) -> str:
    if not started_at_str:
        return "N/A"
    try:
        started_at = datetime.fromisoformat(started_at_str)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        delta = now - started_at
        
        days = delta.days
        hours, remainder = divmod(delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        else:
            return f"{hours:02d}h {minutes:02d}m {seconds:02d}s"
    except Exception:
        return "N/A"
