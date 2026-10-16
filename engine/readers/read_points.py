"""Point clouds -> a Blender point cloud object with colours. Runs in Blender's Python.
LAS/LAZ (laspy + lazrs), E57 (pye57), PTS, XYZ/ASC text files and PCD (ASCII, binary, binary compressed).
Clouds of more than MAX_POINTS points are thinned evenly. Writes geometry.npz/json (see geometry_io.py).
Usage: read_points.py <in file> <outdir>"""
import os
import re
import struct
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from geometry_io import MeshCollector, origin_for  # noqa: E402

MAX_POINTS = 25_000_000
CHUNK = 2_000_000


class Cloud:
    """Points (float64) with optional colours (uint8) or grey values, collected in pieces and thinned evenly."""

    def __init__(self, total=None):
        self.stride = max(1, -(-total // MAX_POINTS)) if total else 1
        self.seen = 0
        self.xyz, self.rgb, self.grey = [], [], []

    def add(self, x, y, z, rgb=None, grey=None):
        count = len(x)
        first = (-self.seen) % self.stride
        keep = slice(first, None, self.stride)
        self.seen += count
        valid = np.isfinite(x[keep]) & np.isfinite(y[keep]) & np.isfinite(z[keep])
        self.xyz.append(np.column_stack([x[keep], y[keep], z[keep]])[valid])
        if rgb is not None:
            self.rgb.append(np.asarray(rgb)[keep][valid])
        if grey is not None:
            self.grey.append(np.asarray(grey, np.float64)[keep][valid])

    def finish(self):
        """(points float64, uint8 colours or None)"""
        xyz = np.concatenate(self.xyz) if self.xyz else np.zeros((0, 3))
        if len(xyz) > MAX_POINTS:  # a text file without a header count
            keep = np.linspace(0, len(xyz) - 1, MAX_POINTS).astype(np.int64)
        else:
            keep = None
        colours = None
        if self.rgb and sum(len(c) for c in self.rgb) == len(xyz):
            colours = np.concatenate(self.rgb)
            if colours.dtype != np.uint8:
                top = colours.max() if len(colours) else 0
                colours = colours / (65535.0 if top > 255 else 255.0 if top > 1.0 else 1.0) * 255.0
                colours = np.clip(np.round(colours), 0, 255).astype(np.uint8)
        elif self.grey and sum(len(g) for g in self.grey) == len(xyz):
            grey = np.concatenate(self.grey)
            low, high = float(grey.min()), float(grey.max())
            level = np.clip(np.round((grey - low) / (high - low or 1.0) * 255.0), 0, 255).astype(np.uint8)
            colours = np.column_stack([level, level, level])
        if keep is not None:
            xyz, colours = xyz[keep], colours[keep] if colours is not None else None
        return xyz, colours


# ---------------------------------------------------------------- LAS / LAZ

def read_las(path):
    import laspy
    with laspy.open(path) as reader:
        names = set(reader.header.point_format.dimension_names)
        cloud = Cloud(reader.header.point_count)
        has_rgb = {"red", "green", "blue"} <= names
        for chunk in reader.chunk_iterator(CHUNK):
            rgb = np.column_stack([chunk.red, chunk.green, chunk.blue]) if has_rgb else None
            grey = np.asarray(chunk.intensity) if "intensity" in names and not has_rgb else None
            cloud.add(np.asarray(chunk.x), np.asarray(chunk.y), np.asarray(chunk.z), rgb, grey)
            print("PROGRESS %d Reading points" % (5 + 70 * cloud.seen // max(reader.header.point_count, 1)), flush=True)
    note = "%d points%s" % (cloud.seen, ", thinned to every %d. point" % cloud.stride if cloud.stride > 1 else "")
    return cloud.finish() + (note,)


# ---------------------------------------------------------------- E57

def read_e57(path):
    import pye57
    scans = pye57.E57(path)
    cloud = Cloud()
    for k in range(scans.scan_count):
        data = scans.read_scan(k, intensity=True, colors=True, ignore_missing_fields=True)
        x, y, z = (np.asarray(data[key], np.float64) for key in ("cartesianX", "cartesianY", "cartesianZ"))
        rgb = None
        if all(key in data for key in ("colorRed", "colorGreen", "colorBlue")):
            rgb = np.column_stack([data["colorRed"], data["colorGreen"], data["colorBlue"]])
        grey = data.get("intensity") if rgb is None else None
        stride = max(1, -(-len(x) // max(1, MAX_POINTS // scans.scan_count)))
        sel = slice(None, None, stride)
        cloud.add(x[sel], y[sel], z[sel], rgb[sel] if rgb is not None else None,
                  np.asarray(grey)[sel] if grey is not None else None)
        print("PROGRESS %d Reading scans" % (5 + 70 * (k + 1) // scans.scan_count), flush=True)
    return cloud.finish() + ("%d scan(s)" % scans.scan_count,)


# ---------------------------------------------------------------- text: PTS, XYZ, ASC

def read_text(path):
    with open(path, "rb") as f:
        text = f.read().decode("latin-1")
    lines = text.split("\n", 3)
    if len(lines[0].split()) == 1 and lines[0].strip().isdigit():  # PTS: the first line is the point count
        text = text[len(lines[0]) + 1:]
    text = re.sub(r"[,;]", " ", text)
    first = next((ln for ln in text.split("\n", 50) if ln.strip() and re.match(r"^\s*[-+0-9.]", ln)), "")
    columns = len(first.split())
    if columns < 3:
        raise ValueError("This text file has no x y z columns.")
    values = np.fromstring(text, dtype=np.float64, sep=" ")
    values = values[:len(values) // columns * columns].reshape(-1, columns)
    x, y, z = values[:, 0], values[:, 1], values[:, 2]
    rgb = grey = None

    def looks_rgb(block):
        return len(block) and block.min() >= 0 and block.max() <= 255

    if columns == 4:
        grey = values[:, 3]
    elif columns == 7 and looks_rgb(values[:, 4:7]):
        rgb = values[:, 4:7]
    elif columns in (6, 9, 10) and looks_rgb(values[:, 3:6]) and np.allclose(values[:, 3:6], np.round(values[:, 3:6])):
        rgb = values[:, 3:6]
    cloud = Cloud()
    cloud.add(x, y, z, rgb, grey)
    return cloud.finish() + ("%d points, %d columns" % (len(x), columns),)


# ---------------------------------------------------------------- PCD

PCD_TYPES = {("F", 4): "<f4", ("F", 8): "<f8", ("U", 1): "u1", ("U", 2): "<u2", ("U", 4): "<u4", ("U", 8): "<u8",
             ("I", 1): "i1", ("I", 2): "<i2", ("I", 4): "<i4", ("I", 8): "<i8"}


def lzf_decompress(data, size):
    out = bytearray()
    i, n = 0, len(data)
    while i < n and len(out) < size:
        control = data[i]
        i += 1
        if control < 32:
            out += data[i:i + control + 1]
            i += control + 1
        else:
            length = control >> 5
            if length == 7:
                length += data[i]
                i += 1
            start = len(out) - ((control & 0x1F) << 8) - data[i] - 1
            i += 1
            length += 2
            if start + length <= len(out):
                out += out[start:start + length]
            else:
                for k in range(length):  # the copy overlaps what it is writing
                    out.append(out[start + k])
    return bytes(out)


def read_pcd(path):
    with open(path, "rb") as f:
        raw = f.read()
    end = raw.index(b"\nDATA") + 1
    line_end = raw.index(b"\n", end)
    header = {}
    for line in raw[:line_end].decode("latin-1").splitlines():
        parts = line.split()
        if parts and not line.startswith("#"):
            header[parts[0].upper()] = parts[1:]
    kind = header["DATA"][0].lower()
    fields, sizes, types = header["FIELDS"], [int(s) for s in header["SIZE"]], header["TYPE"]
    counts = [int(c) for c in header.get("COUNT", ["1"] * len(fields))]
    points = int(header["POINTS"][0])
    dtype = np.dtype([(name, PCD_TYPES[(types[k], sizes[k])]) if counts[k] == 1
                      else (name, PCD_TYPES[(types[k], sizes[k])], (counts[k],)) for k, name in enumerate(fields)])
    body = raw[line_end + 1:]
    if kind == "ascii":
        values = np.fromstring(body.decode("latin-1"), dtype=np.float64, sep=" ")
        values = values[:points * sum(counts)].reshape(points, sum(counts))
        table, column = {}, 0
        for k, name in enumerate(fields):
            table[name] = values[:, column]
            column += counts[k]
    else:
        if kind == "binary_compressed":
            compressed, size = struct.unpack_from("<II", body)
            body = lzf_decompress(body[8:8 + compressed], size)
            table, offset = {}, 0
            for k, name in enumerate(fields):  # stored one field after the other
                width = sizes[k] * counts[k] * points
                table[name] = np.frombuffer(body, PCD_TYPES[(types[k], sizes[k])], points * counts[k], offset)
                offset += width
        elif kind == "binary":
            array = np.frombuffer(body, dtype, points)
            table = {name: array[name] for name in fields}
        else:
            raise ValueError("Unknown PCD data type '%s'." % kind)
    x, y, z = (np.asarray(table[k], np.float64) for k in ("x", "y", "z"))
    rgb = None
    if "rgb" in table or "rgba" in table:
        packed = np.asarray(table.get("rgb", table.get("rgba")))
        packed = packed.view(np.uint32) if packed.dtype == np.float32 else packed.astype(np.uint32)
        rgb = np.column_stack([(packed >> 16) & 255, (packed >> 8) & 255, packed & 255]).astype(np.uint8)
    elif all(k in table for k in ("r", "g", "b")):
        rgb = np.column_stack([table["r"], table["g"], table["b"]])
    cloud = Cloud(points)
    cloud.add(x, y, z, rgb)
    return cloud.finish() + ("%d points" % points,)


READERS = {".las": read_las, ".laz": read_las, ".e57": read_e57, ".pts": read_text, ".xyz": read_text,
           ".asc": read_text, ".pcd": read_pcd}


def run(src, outdir):
    t0 = time.time()
    ext = os.path.splitext(src)[1].lower()
    stem = os.path.splitext(os.path.basename(src))[0]
    print("PROGRESS 5 Reading points", flush=True)
    try:
        xyz, colours, note = READERS[ext](src)
    except (ValueError, KeyError, OSError, ImportError) as ex:
        print("Could not read this point cloud: %s" % ex, file=sys.stderr, flush=True)
        return 1
    if not len(xyz):
        print("No points found in this file.", file=sys.stderr, flush=True)
        return 1
    print("PROGRESS 80 Building geometry", flush=True)
    low, high = xyz.min(axis=0), xyz.max(axis=0)
    collector = MeshCollector()
    collector.origin = origin_for(low, high)
    area = max(float((high[0] - low[0]) * (high[1] - low[1])), 1e-9)
    radius = float(np.clip(0.5 * np.sqrt(area / len(xyz)), 0.002, 2.0))
    collector.add_pointcloud(stem, xyz - np.array(collector.origin), colours, radius)
    collector.save(outdir)
    print("point cloud: %d points (%s)%s, point size %.3f m [%.0fs]" % (
        len(xyz), note, ", with colours" if colours is not None else "", radius, time.time() - t0))
    return 0


if __name__ == "__main__":
    code = run(sys.argv[1], sys.argv[2])
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
