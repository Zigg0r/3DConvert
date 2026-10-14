"""Quick OBJ scanner that collects statistics without building a mesh."""

import os
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class ObjInfo:
    path: str
    format_name: str = "Wavefront OBJ"
    file_size: int = 0
    vertices: int = 0
    normals: int = 0
    uvs: int = 0
    faces: int = 0
    triangles: int = 0
    objects: int = 0
    groups: int = 0
    materials: list = field(default_factory=list)
    mtl_files: list = field(default_factory=list)
    bbox_min: Optional[tuple] = None
    bbox_max: Optional[tuple] = None


def read_obj_info(path: str, progress: Callable[[int], None] = None) -> ObjInfo:
    """Scan an OBJ file; `progress` receives 0-100."""
    info = ObjInfo(path=path, file_size=os.path.getsize(path))
    materials = set()
    mins = [float("inf")] * 3
    maxs = [float("-inf")] * 3
    read_bytes = 0
    last_pct = -1

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            read_bytes += len(line) + 1
            if progress and info.file_size:
                pct = min(99, read_bytes * 100 // info.file_size)
                if pct != last_pct:
                    last_pct = pct
                    progress(pct)

            line = line.strip()
            if not line or line[0] == "#":
                continue
            parts = line.split()
            key = parts[0]

            if key == "v":
                info.vertices += 1
                try:
                    for i in range(3):
                        val = float(parts[i + 1])
                        if val < mins[i]:
                            mins[i] = val
                        if val > maxs[i]:
                            maxs[i] = val
                except (ValueError, IndexError):
                    pass
            elif key == "vn":
                info.normals += 1
            elif key == "vt":
                info.uvs += 1
            elif key == "f":
                info.faces += 1
                info.triangles += max(0, len(parts) - 3)
            elif key == "o":
                info.objects += 1
            elif key == "g":
                info.groups += 1
            elif key == "usemtl" and len(parts) > 1:
                materials.add(" ".join(parts[1:]))
            elif key == "mtllib" and len(parts) > 1:
                info.mtl_files.append(" ".join(parts[1:]))

    info.materials = sorted(materials)
    if info.vertices and mins[0] != float("inf"):
        info.bbox_min = tuple(mins)
        info.bbox_max = tuple(maxs)
    if progress:
        progress(100)
    return info
