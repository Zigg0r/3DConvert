"""Shared by the GIS readers (GeoJSON, Shapefile, KML, GPX, OSM, GeoPackage, GeoTIFF, ESRI ASCII grid).

Projected coordinates (RD New, UTM, Lambert, ...) are kept exactly as stored, in metres, so that files of the same
CRS lie on top of each other. Longitude/latitude data cannot be used as it is (degrees), so it becomes UTM metres of
the zone that holds the centre of the data."""
import math
import re

import numpy as np

from geometry_io import MeshCollector, origin_for  # noqa: F401  (readers import MeshCollector from here)
from polygons import triangulate_rings

GEOGRAPHIC_EPSG = {4326, 4258, 4269, 4283, 4617, 4979, 7844, 4167, 6318}
WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563
UTM_K0 = 0.9996
FOOT = 0.3048
US_FOOT = 0.3048006096012192
UNIT_CODES = {9001: 1.0, 9002: FOOT, 9003: US_FOOT, 9005: 0.3047972654}  # EPSG linear unit -> metres


def epsg_of(text):
    """EPSG code in a CRS name such as 'EPSG:28992' or 'urn:ogc:def:crs:EPSG::28992'; CRS84 means WGS 84."""
    if not text:
        return None
    text = str(text)
    found = re.search(r"EPSG[^0-9]{0,12}?(\d{4,6})", text, re.IGNORECASE)
    if found:
        return int(found.group(1))
    return 4326 if "CRS84" in text.upper() else None


def geographic_from_epsg(code):
    """True/False for an EPSG code (geographic CRSs sit at 4000-4999 and a few newer codes), None without a code."""
    if code is None:
        return None
    return code in GEOGRAPHIC_EPSG or 4000 <= code < 5000


def unit_from_prj(wkt):
    """(geographic, metres per unit) of an ESRI/OGC .prj text. A PROJCS is projected; a bare GEOGCS is geographic."""
    upper = wkt.upper()
    if upper.lstrip().startswith(("GEOGCS", "GEOGCRS", "GEODCRS")):
        return True, 1.0
    if "PROJCS" in upper or "PROJCRS" in upper or "PROJECTEDCRS" in upper:
        units = re.findall(r'UNIT\s*\[\s*"[^"]*"\s*,\s*([0-9.eE+-]+)', wkt, re.IGNORECASE)
        try:
            return False, float(units[-1]) if units else 1.0
        except ValueError:
            return False, 1.0
    return None, 1.0


def utm_zone(lon):
    return int(min(60, max(1, math.floor((lon + 180) / 6) + 1)))


def utm_forward(lon, lat, zone, south):
    """WGS 84 longitude/latitude (degrees, arrays) -> UTM easting, northing in metres (Karney's series)."""
    n = WGS84_F / (2 - WGS84_F)
    big_a = WGS84_A / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64)
    alpha = (n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16, 13 * n ** 2 / 48 - 3 * n ** 3 / 5, 61 * n ** 3 / 240)
    e = math.sqrt(WGS84_F * (2 - WGS84_F))
    phi = np.radians(np.asarray(lat, np.float64))
    lam = np.radians(np.asarray(lon, np.float64) - (zone * 6 - 183))
    tau = np.tan(phi)
    sigma = np.sinh(e * np.arctanh(e * tau / np.hypot(1, tau)))
    tau_p = tau * np.hypot(1, sigma) - sigma * np.hypot(1, tau)
    xi_p = np.arctan2(tau_p, np.cos(lam))
    eta_p = np.arcsinh(np.sin(lam) / np.hypot(tau_p, np.cos(lam)))
    xi, eta = xi_p.copy(), eta_p.copy()
    for j, a in enumerate(alpha, 1):
        xi += a * np.sin(2 * j * xi_p) * np.cosh(2 * j * eta_p)
        eta += a * np.cos(2 * j * xi_p) * np.sinh(2 * j * eta_p)
    return 500000.0 + UTM_K0 * big_a * eta, UTM_K0 * big_a * xi + (10000000.0 if south else 0.0)


class UtmFrame:
    """The UTM zone chosen for a set of longitude/latitude data."""

    def __init__(self, lon_centre, lat_centre):
        self.zone = utm_zone(lon_centre)
        self.south = lat_centre < 0

    def __str__(self):
        return "UTM zone %d%s (WGS 84)" % (self.zone, "S" if self.south else "N")

    def apply(self, points):
        """(n, 3) array, x = longitude, y = latitude -> same array in metres."""
        out = points.astype(np.float64)
        out[:, 0], out[:, 1] = utm_forward(points[:, 0], points[:, 1], self.zone, self.south)
        return out


def signed_area(ring):
    x, y = ring[:, 0], ring[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def as_array(coords):
    """(n, 3) float64 from a list of (x, y) or (x, y, z) tuples, which may be mixed; None when nothing usable."""
    try:
        array = np.asarray(coords, np.float64)
    except (ValueError, TypeError):
        try:
            array = np.array([[p[0], p[1], p[2] if len(p) > 2 else 0.0] for p in coords], np.float64)
        except (IndexError, TypeError, ValueError):
            return None
    if array.ndim == 1 and array.size >= 2:
        array = array[None, :]
    if array.ndim != 2 or array.shape[1] < 2 or not len(array):
        return None
    if array.shape[1] == 2:
        array = np.column_stack([array, np.zeros(len(array))])
    return array[:, :3]


def clean_ring(ring):
    """(n, 3) float64 ring without the repeated closing point; None when fewer than three points remain."""
    ring = as_array(ring)
    if ring is None:
        return None
    if len(ring) > 1 and np.array_equal(ring[0], ring[-1]):
        ring = ring[:-1]
    return ring if len(ring) >= 3 else None


class Layer:
    """Polygons, lines and points of one source layer, in the source CRS until emit() converts them to metres."""

    COLOURS = {"polygons": (0.62, 0.64, 0.66), "lines": (0.2, 0.2, 0.2), "points": (0.25, 0.6, 0.3)}

    def __init__(self, name, geographic=None, factor=1.0):
        self.name = name
        self.geographic = geographic
        self.factor = factor
        self._vertices = {"polygons": [], "lines": [], "points": []}
        self._indices = {"polygons": [], "lines": []}
        self._count = {"polygons": 0, "lines": 0, "points": 0}
        self.bounds = [math.inf, math.inf, -math.inf, -math.inf]
        self.skipped = 0

    def _note(self, array):
        self.bounds[0] = min(self.bounds[0], float(array[:, 0].min()))
        self.bounds[1] = min(self.bounds[1], float(array[:, 1].min()))
        self.bounds[2] = max(self.bounds[2], float(array[:, 0].max()))
        self.bounds[3] = max(self.bounds[3], float(array[:, 1].max()))

    def polygon(self, rings):
        """rings[0] is the outline, the others are holes; each ring is a list or array of (x, y[, z])."""
        cleaned = [clean_ring(r) for r in rings]
        if cleaned[0] is None:
            self.skipped += 1
            return
        cleaned = [r for r in cleaned if r is not None]
        try:
            triangles = triangulate_rings(cleaned)
        except Exception:
            triangles = np.zeros((0, 3), np.int64)
        if not len(triangles):
            self.skipped += 1
            return
        points = np.concatenate(cleaned)
        a, b, c = points[triangles[:, 0]], points[triangles[:, 1]], points[triangles[:, 2]]
        down = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]) < 0
        triangles[down] = triangles[down][:, ::-1]  # every flat face looks up
        self._indices["polygons"].append(triangles + self._count["polygons"])
        self._vertices["polygons"].append(points)
        self._count["polygons"] += len(points)
        self._note(points)

    def line(self, coords):
        coords = as_array(coords)
        if coords is None or len(coords) < 2:
            self.skipped += 1
            return
        n = len(coords)
        edges = np.column_stack([np.arange(n - 1), np.arange(1, n)]) + self._count["lines"]
        self._indices["lines"].append(edges)
        self._vertices["lines"].append(coords)
        self._count["lines"] += n
        self._note(coords)

    def points(self, coords):
        coords = as_array(coords)
        if coords is None:
            self.skipped += 1
            return
        self._vertices["points"].append(coords)
        self._count["points"] += len(coords)
        self._note(coords)

    def __len__(self):
        return sum(self._count.values())

    def kinds(self):
        return [k for k in ("polygons", "lines", "points") if self._count[k]]

    def parts(self, frame):
        """[(kind, mesh name, points in metres (n, 3) float64, indices or None, colour)]; `frame` converts
        longitude/latitude (None for projected data)."""
        kinds = self.kinds()
        out = []
        for kind in kinds:
            points = np.concatenate(self._vertices[kind])
            if frame is not None:
                points = frame.apply(points)
            else:
                points = points * np.array([self.factor, self.factor, 1.0])
            name = self.name if len(kinds) == 1 else "%s %s" % (self.name, kind)
            indices = np.concatenate(self._indices[kind]) if kind != "points" else None
            out.append((kind, name, points, indices, self.COLOURS[kind]))
        return out


def is_geographic(layer):
    """Declared CRS first; without one, coordinates inside the longitude/latitude range are taken as degrees."""
    if layer.geographic is not None:
        return layer.geographic
    x0, y0, x1, y1 = layer.bounds
    return -180 <= x0 and x1 <= 180 and -90 <= y0 and y1 <= 90


def emit_layers(layers, collector):
    """Converts every layer to metres and adds it to the collector; returns a short description of the coordinates.
    The vertices are stored relative to a round origin near the data (see geometry_io), so far-away coordinates such
    as RD or UTM keep their precision."""
    layers = [layer for layer in layers if len(layer)]
    geographic = [layer for layer in layers if is_geographic(layer)]
    frame = None
    if geographic:
        x0 = min(layer.bounds[0] for layer in geographic)
        y0 = min(layer.bounds[1] for layer in geographic)
        x1 = max(layer.bounds[2] for layer in geographic)
        y1 = max(layer.bounds[3] for layer in geographic)
        frame = UtmFrame((x0 + x1) / 2, (y0 + y1) / 2)
    parts = []
    for layer in layers:
        parts += layer.parts(frame if layer in geographic else None)
    if parts:
        collector.origin = origin_for(np.min([p[2].min(axis=0) for p in parts], axis=0),
                                      np.max([p[2].max(axis=0) for p in parts], axis=0))
    shift = np.array(collector.origin)
    for kind, name, points, indices, colour in parts:
        points = points - shift
        if kind == "polygons":
            collector.add(name, points, indices, colour=colour)
        elif kind == "lines":
            collector.add_edges(name, points, indices, colour=colour)
        else:
            collector.add_points(name, points, colour=colour)
    if frame is not None:
        return "longitude/latitude converted to " + str(frame)
    return "projected coordinates kept in metres, as stored"
