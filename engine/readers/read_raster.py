"""GIS rasters -> terrain mesh. Runs in Blender's Python.
GeoTIFF elevation grids (.tif, .tiff; also a TIFF with a world file) and ESRI ASCII grids (.asc). Every cell centre
becomes a vertex and every cell with four valid corners becomes a quad; nodata cells stay open. Projected rasters
keep their coordinates in metres as stored; a longitude/latitude raster becomes UTM metres.
Writes geometry.npz/json (see geometry_io.py). Usage: read_raster.py <in file> <outdir>"""
import math
import os
import re
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from geometry_io import MeshCollector, origin_for  # noqa: E402
from gis_common import UNIT_CODES, UtmFrame, geographic_from_epsg, unit_from_prj  # noqa: E402
from tiff_reader import TAG, Tiff, TiffError  # noqa: E402

MAX_CELLS = 25_000_000  # a larger grid is reduced to every n-th cell in both directions
COLOUR = (0.55, 0.5, 0.4)
SENTINELS = (-9999.0, -32768.0)


class Grid:
    """Heights plus the affine map from (column, row) of a cell centre to x, y."""

    def __init__(self, z, affine, nodata=None, geographic=None, factor=1.0, vfactor=1.0, crs=""):
        self.z, self.affine, self.nodata = z, affine, nodata
        self.geographic, self.factor, self.vfactor, self.crs = geographic, factor, vfactor, crs


def read_world_file(path):
    base, ext = os.path.splitext(path)
    ext = ext.lower().lstrip(".")
    for suffix in (ext[0] + ext[-1] + "w", ext + "w", "wld", "tfw"):
        for candidate in (base + "." + suffix, base + "." + suffix.upper()):
            if os.path.isfile(candidate):
                with open(candidate, encoding="utf-8", errors="replace") as f:
                    values = [float(v) for v in f.read().split()[:6]]
                if len(values) == 6:
                    a, d, b, e, c, f_ = values  # x = a*col + b*row + c, y = d*col + e*row + f (cell centre of 0, 0)
                    return (a, b, c, d, e, f_)
    return None


def read_prj(path):
    prj = os.path.splitext(path)[0] + ".prj"
    if os.path.isfile(prj):
        with open(prj, encoding="utf-8", errors="replace") as f:
            return unit_from_prj(f.read())
    return None, 1.0


def read_geotiff(path):
    tiff = Tiff(path)
    try:
        keys = tiff.geokeys()
        z = tiff.read()
        scale, tie, transform = tiff.get(TAG["pixel_scale"]), tiff.get(TAG["tiepoint"]), tiff.get(TAG["transformation"])
        half = 0.5 if keys.get(1025, 1) != 2 else 0.0  # PixelIsArea: the point of a cell is its centre
        if transform is not None and len(transform) >= 16:
            m = transform
            affine = (m[0], m[1], m[0] * half + m[1] * half + m[3], m[4], m[5], m[4] * half + m[5] * half + m[7])
        elif scale is not None and tie is not None and len(tie) >= 6:
            i, j, x0, y0 = tie[0], tie[1], tie[3], tie[4]
            sx, sy = scale[0], scale[1]
            affine = (sx, 0.0, x0 + (half - i) * sx, 0.0, -sy, y0 - (half - j) * sy)
        else:
            affine = read_world_file(path)
            if affine is None:
                raise TiffError("This TIFF has no georeference (no GeoTIFF tags and no world file), so it cannot be "
                                "placed as terrain.")
        a, b, c, d, e, f = (float(v) for v in affine)
        geographic, factor = None, 1.0
        model = keys.get(1024)  # 1 projected, 2 geographic
        epsg = keys.get(3072) if keys.get(3072, 32767) not in (0, 32767) else keys.get(2048)
        if model == 2:
            geographic = True
        elif model == 1:
            geographic, factor = False, UNIT_CODES.get(keys.get(3076, 9001), 1.0)
        elif epsg:
            geographic = geographic_from_epsg(int(epsg))
        if geographic is None:
            geographic, factor = read_prj(path)
        vfactor = UNIT_CODES.get(keys.get(4099), factor if geographic is False else 1.0)
        text = tiff.get(TAG["nodata"])
        nodata = None
        try:
            nodata = float(text) if text else None
        except ValueError:
            pass
        name = keys.get(3073) or keys.get(2049) or (("EPSG:%d" % epsg) if epsg else "")
        return Grid(z, (a, b, c, d, e, f), nodata, geographic, factor, vfactor, str(name))
    finally:
        tiff.close()


def read_ascii_grid(path):
    header, rows = {}, 0
    with open(path, "rb") as f:
        while rows < 12:
            at = f.tell()
            line = f.readline().decode("latin-1").strip()
            parts = line.split()
            if len(parts) == 2 and re.match(r"^[A-Za-z_]+$", parts[0]):
                header[parts[0].lower()] = float(parts[1])
                rows += 1
            else:
                f.seek(at)
                break
        body = f.read().decode("latin-1")
    if not {"ncols", "nrows"} <= header.keys() or not ("cellsize" in header or {"dx", "dy"} <= header.keys()):
        raise ValueError("This .asc file has no ESRI grid header (ncols, nrows, cellsize).")
    ncols, nrows = int(header["ncols"]), int(header["nrows"])
    dx, dy = header.get("dx", header.get("cellsize")), header.get("dy", header.get("cellsize"))
    z = np.fromstring(body, dtype=np.float32, sep=" ")
    if z.size < ncols * nrows:
        raise ValueError("The grid holds %d values, %d expected." % (z.size, ncols * nrows))
    z = z[:ncols * nrows].reshape(nrows, ncols)
    if "xllcenter" in header:
        x0 = header["xllcenter"]
    else:
        x0 = header.get("xllcorner", 0.0) + dx / 2
    if "yllcenter" in header:
        y0 = header["yllcenter"] + (nrows - 1) * dy
    else:
        y0 = header.get("yllcorner", 0.0) + (nrows - 0.5) * dy
    geographic, factor = read_prj(path)
    return Grid(z, (dx, 0.0, x0, 0.0, -dy, y0), header.get("nodata_value"), geographic, factor)


def build(grid, stem, collector):
    """Quads of the valid cells; returns a description of the coordinates."""
    z = grid.z
    height, width = z.shape
    a, b, c, d, e, f = grid.affine
    step = 1
    if height * width > MAX_CELLS:
        step = math.ceil(math.sqrt(height * width / MAX_CELLS))
        z = z[::step, ::step]
        a, b, d, e = a * step, b * step, d * step, e * step
        height, width = z.shape
        print("grid reduced to every %d. cell to keep the model manageable" % step)
    valid = np.isfinite(z) & (np.abs(z) < 1e30)
    for sentinel in SENTINELS:
        valid &= z != sentinel
    if grid.nodata is not None and np.isfinite(grid.nodata):
        valid &= z != grid.nodata
    ok = valid[:-1, :-1] & valid[1:, :-1] & valid[1:, 1:] & valid[:-1, 1:]
    rows, cols = np.nonzero(ok)
    if not len(rows):
        raise ValueError("The raster holds no valid elevation cells.")
    first = rows * width + cols
    if a * e - b * d < 0:  # counter-clockwise seen from above
        quads = np.column_stack([first, first + width, first + width + 1, first + 1])
    else:
        quads = np.column_stack([first, first + 1, first + width + 1, first + width])
    used = np.zeros(height * width, np.bool_)
    used[quads.ravel()] = True
    index = np.nonzero(used)[0]
    remap = np.cumsum(used) - 1
    quads = remap[quads]
    r, k = index // width, index % width
    x = a * k + b * r + c
    y = d * k + e * r + f
    heights = z.ravel()[index].astype(np.float64) * grid.vfactor
    geographic = grid.geographic
    if geographic is None:
        geographic = -180 <= x.min() and x.max() <= 180 and -90 <= y.min() and y.max() <= 90
    points = np.column_stack([x, y, heights])
    if geographic:
        frame = UtmFrame((x.min() + x.max()) / 2, (y.min() + y.max()) / 2)
        points = frame.apply(points)
        note = "longitude/latitude converted to %s" % frame
    else:
        points[:, :2] *= grid.factor
        note = "projected coordinates kept in metres, as stored%s" % (" (%s)" % grid.crs if grid.crs else "")
    collector.origin = origin_for(points.min(axis=0), points.max(axis=0))  # see geometry_io: keeps float32 precision
    collector.add_quads(stem, points - np.array(collector.origin), quads, colour=COLOUR)
    return note, int(valid.sum()), valid.size


def run(src, outdir):
    t0 = time.time()
    ext = os.path.splitext(src)[1].lower()
    stem = os.path.splitext(os.path.basename(src))[0]
    if ext == ".asc":
        with open(src, "rb") as f:
            is_grid = f.read(200).decode("latin-1").lstrip().lower().startswith("ncols")
        if not is_grid:  # an .asc file can also be a text point cloud
            import read_points
            return read_points.run(src, outdir)
    print("PROGRESS 5 Reading raster", flush=True)
    try:
        grid = read_ascii_grid(src) if ext in (".asc", ".grd") else read_geotiff(src)
        print("PROGRESS 60 Building terrain", flush=True)
        collector = MeshCollector()
        note, valid, total = build(grid, stem, collector)
    except (TiffError, ValueError, OSError) as ex:
        print("Could not read this raster: %s" % ex, file=sys.stderr, flush=True)
        return 1
    collector.save(outdir)
    print("terrain: %d quads from a %d x %d grid (%d%% valid); %s [%.0fs]" % (
        collector.quads, grid.z.shape[1], grid.z.shape[0], round(100 * valid / total), note, time.time() - t0))
    return 0


if __name__ == "__main__":
    code = run(sys.argv[1], sys.argv[2])
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
