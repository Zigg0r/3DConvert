"""Streaming access to the entities of an IFC file (STEP physical file) as text; shared by the IFC readers."""
import re

HEAD = re.compile(rb"^#(\d+)\s*=\s*([A-Z0-9_]+)\s*\(")
REF = re.compile(rb"#(\d+)")

# Entity types that hold tessellated geometry; a file that is mostly these is read as a mesh, not as solids.
MESH_TYPES = {b"IFCINDEXEDPOLYGONALFACE", b"IFCINDEXEDPOLYGONALFACEWITHVOIDS", b"IFCCARTESIANPOINTLIST3D",
              b"IFCPOLYGONALFACESET", b"IFCTRIANGULATEDFACESET", b"IFCINDEXEDCOLOURMAP", b"IFCCOLOURRGBLIST"}


def entities(path):
    """Yields (id, type, text) for every entity of the DATA section, as bytes. An entity may span several lines."""
    buf = b""
    with open(path, "rb") as f:
        in_data = False
        for line in f:
            if not in_data:
                if line.strip() == b"DATA;":
                    in_data = True
                continue
            if line.endswith(b";\n") or line.endswith(b";\r\n"):  # fast path: the entity ends on this line
                if buf:
                    line, buf = buf + line, b""
                m = HEAD.match(line)
                if m:
                    yield int(m.group(1)), m.group(2), line
                continue
            buf += line
            if not buf.rstrip().endswith(b";"):
                continue
            m = HEAD.match(buf)
            if m:
                yield int(m.group(1)), m.group(2), buf
            buf = b""
