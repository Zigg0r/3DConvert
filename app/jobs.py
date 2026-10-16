"""How many conversions run at the same time: a RAM budget, a CPU limit and a rule for very large files."""
import ctypes
import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

BUDGET_SHARE = 0.8  # of the RAM that is free when the conversion starts
BIG_FILE_MB = 200  # only one file of this size or larger is converted at a time
MAX_WORKERS = 4
BASE_MB = 400  # a Blender process before it holds any geometry
# Peak RAM per MB of source file. Measured with Blender 5.2: .blend 12x, .3dm 7.5x, .skp 41x (a dense file), the rest
# is estimated; the factors are on the safe side.
PER_FILE_MB = {".skp": 40, ".ifc": 20, ".ifcxml": 20, ".ifczip": 40,
               ".tif": 450, ".tiff": 450, ".asc": 180,  # measured: a 19 MB GeoTIFF (6.9 million cells) peaks at 7.5 GB
               ".las": 8, ".laz": 50, ".e57": 15, ".pts": 12, ".xyz": 12, ".pcd": 8,
               ".geojson": 25, ".json": 25, ".shp": 15, ".gpkg": 20, ".osm": 15, ".kml": 20, ".kmz": 20}
DEFAULT_PER_FILE_MB = 14
IFC_EXTENSIONS = {".ifc", ".ifczip", ".ifcxml"}


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong), ("total_page", ctypes.c_ulonglong),
                ("avail_page", ctypes.c_ulonglong), ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong), ("avail_extended", ctypes.c_ulonglong)]


def memory_mb():
    """(total, free) physical RAM in MB; (0, 0) when it cannot be read."""
    try:
        status = _MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return 0, 0
        return status.total_phys // 2 ** 20, status.avail_phys // 2 ** 20
    except (AttributeError, OSError):
        return 0, 0


def ram_budget_mb():
    """What the running conversions together may use: 80% of the RAM that is free right now (0 = unknown)."""
    return int(memory_mb()[1] * BUDGET_SHARE)


def estimate_mb(path):
    """Rough peak RAM of converting this file."""
    try:
        size = os.path.getsize(path) / 2 ** 20
    except OSError:
        size = 0
    return BASE_MB + PER_FILE_MB.get(os.path.splitext(path)[1].lower(), DEFAULT_PER_FILE_MB) * size


def job_for(index, path):
    try:
        big = os.path.getsize(path) >= BIG_FILE_MB * 2 ** 20
    except OSError:
        big = False
    return {"index": index, "path": path, "est": estimate_mb(path), "big": big,
            "ifc": os.path.splitext(path)[1].lower() in IFC_EXTENSIONS}


def worker_count(count, budget):
    """Conversions at once: at most MAX_WORKERS, half the CPU threads, and one when the RAM is unknown."""
    if budget <= 0:
        return 1
    return max(1, min(MAX_WORKERS, (os.cpu_count() or 2) // 2, count))


def _fits(job, running, budget):
    if not running:
        return True  # something always runs, even when the file alone is above the budget
    if job["big"] and any(r["big"] for r in running):
        return False
    if job["ifc"] and any(r["ifc"] for r in running):
        return False  # IFC already uses every CPU core by itself
    return sum(r["est"] for r in running) + job["est"] <= budget


def run_jobs(jobs, run_one, budget, workers, cancelled):
    """Calls run_one(job) for every job, several at a time while they fit the RAM budget. A job that does not fit
    waits and a smaller one behind it may go first. Stops starting new jobs once cancelled() is true."""
    pending, running = list(jobs), {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while pending or running:
            started = True
            while started and pending and len(running) < workers and not cancelled():
                started = False
                for job in pending:
                    if _fits(job, list(running.values()), budget):
                        pending.remove(job)
                        running[pool.submit(run_one, job)] = job
                        started = True
                        break
            if not running:
                break
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                running.pop(future)
                future.result()  # run_one handles its own errors; anything else should surface
