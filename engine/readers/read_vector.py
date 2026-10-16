"""GIS vector files -> faces, lines and points. Runs in Blender's Python.
GeoJSON (.geojson, .json), Shapefile (.shp), KML/KMZ, GPX, OpenStreetMap XML (.osm) and GeoPackage (.gpkg).
Polygons become flat faces (holes are kept), lines become edges and points become loose vertices, at the height
stored in the file (0 for 2D data). Projected coordinates stay as stored; longitude/latitude becomes UTM metres.
Writes geometry.npz/json (see geometry_io.py). Usage: read_vector.py <in file> <outdir>"""
import os
import re
import struct
import sys
import time
import zipfile

import numpy as np
from defusedxml import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gis_common import (Layer, MeshCollector, emit_layers, epsg_of, geographic_from_epsg, signed_area,  # noqa: E402
                        unit_from_prj)


def point_batch(layer):
    """Collects points in a Python list and hands them over in one piece (a file can hold millions)."""
    batch = []

    def add(xyz):
        batch.append(xyz)

    def flush():
        if batch:
            layer.points(batch)
            batch.clear()
    return add, flush


# ---------------------------------------------------------------- GeoJSON

def read_geojson(path, stem):
    import json
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    crs = data.get("crs") if isinstance(data, dict) else None
    name = ((crs or {}).get("properties") or {}).get("name") if isinstance(crs, dict) else None
    layer = Layer(stem, geographic_from_epsg(epsg_of(name)))
    add_point, flush = point_batch(layer)

    def geometry(g):
        if not isinstance(g, dict):
            return
        kind, c = g.get("type"), g.get("coordinates")
        if kind == "Point" and c:
            add_point(c)
        elif kind == "MultiPoint" and c:
            for p in c:
                add_point(p)
        elif kind == "LineString" and c:
            layer.line(c)
        elif kind == "MultiLineString" and c:
            for line in c:
                layer.line(line)
        elif kind == "Polygon" and c:
            layer.polygon(c)
        elif kind == "MultiPolygon" and c:
            for polygon in c:
                if polygon:
                    layer.polygon(polygon)
        elif kind == "GeometryCollection":
            for sub in g.get("geometries") or []:
                geometry(sub)

    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        features = data.get("features") or []
        for k, feature in enumerate(features):
            geometry((feature or {}).get("geometry"))
            if k % 2000 == 0:
                print("PROGRESS %d Reading GeoJSON" % (10 + 80 * k // max(len(features), 1)), flush=True)
    elif isinstance(data, dict) and data.get("type") == "Feature":
        geometry(data.get("geometry"))
    else:
        geometry(data)
    flush()
    return [layer]


# ---------------------------------------------------------------- Shapefile

POINT_TYPES, LINE_TYPES, POLYGON_TYPES, MULTIPOINT_TYPES = (1, 11, 21), (3, 13, 23), (5, 15, 25), (8, 18, 28)
HAS_Z = (11, 13, 15, 18)


def inside(point, ring):
    """Even-odd test of one (x, y) against a ring."""
    x, y = point[0], point[1]
    x0, y0, x1, y1 = ring[:, 0], ring[:, 1], np.roll(ring[:, 0], -1), np.roll(ring[:, 1], -1)
    crossing = (y0 > y) != (y1 > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        at = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
    return bool(np.count_nonzero(crossing & (x < at)) % 2)


def group_rings(rings):
    """Shapefile polygons: clockwise rings are outlines, counter-clockwise rings are holes of the outline around them."""
    outlines, holes = [], []
    for ring in rings:
        if len(ring) < 3:
            continue
        (holes if signed_area(ring) > 0 else outlines).append(ring)
    if not outlines:
        outlines, holes = holes, []
    groups = [[o] for o in outlines]
    for hole in holes:
        owners = [g for g in groups if inside(hole[0], g[0])]
        if owners:
            min(owners, key=lambda g: abs(signed_area(g[0]))).append(hole)
        else:
            groups.append([hole])
    return groups


def read_shp(path, stem):
    geographic, factor = None, 1.0
    prj = os.path.splitext(path)[0] + ".prj"
    if os.path.isfile(prj):
        with open(prj, encoding="utf-8", errors="replace") as f:
            geographic, factor = unit_from_prj(f.read())
    layer = Layer(stem, geographic, factor)
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 100 or struct.unpack_from(">i", data, 0)[0] != 9994:
        raise ValueError("This is not a valid .shp file.")
    add_point, flush = point_batch(layer)
    pos, unsupported = 100, set()
    while pos + 12 <= len(data):
        length = struct.unpack_from(">i", data, pos + 4)[0] * 2
        body = pos + 12
        pos += 8 + length
        shape = struct.unpack_from("<i", data, body - 4)[0]
        if shape == 0:
            continue
        if shape in POINT_TYPES:
            x, y = struct.unpack_from("<2d", data, body)
            z = struct.unpack_from("<d", data, body + 16)[0] if shape in HAS_Z else 0.0
            if np.isfinite(x) and np.isfinite(y):
                add_point((x, y, z if np.isfinite(z) else 0.0))
        elif shape in MULTIPOINT_TYPES:
            count = struct.unpack_from("<i", data, body + 32)[0]
            xy = np.frombuffer(data, "<f8", count * 2, body + 36).reshape(-1, 2)
            z = np.frombuffer(data, "<f8", count, body + 36 + 16 * count + 16) if shape in HAS_Z else np.zeros(count)
            layer.points(np.column_stack([xy, z]))
        elif shape in LINE_TYPES + POLYGON_TYPES:
            n_parts, n_points = struct.unpack_from("<2i", data, body + 32)
            parts = np.frombuffer(data, "<i4", n_parts, body + 40)
            at = body + 40 + 4 * n_parts
            xy = np.frombuffer(data, "<f8", n_points * 2, at).reshape(-1, 2)
            z = np.frombuffer(data, "<f8", n_points, at + 16 * n_points + 16) if shape in HAS_Z else np.zeros(n_points)
            xyz = np.column_stack([xy, np.where(np.isfinite(z) & (z > -1e38), z, 0.0)])
            bounds = list(parts) + [n_points]
            pieces = [xyz[bounds[k]:bounds[k + 1]] for k in range(n_parts)]
            if shape in LINE_TYPES:
                for piece in pieces:
                    layer.line(piece)
            else:
                for group in group_rings(pieces):
                    layer.polygon(group)
        else:
            unsupported.add(shape)
    flush()
    if unsupported:
        print("skipped shape types: %s" % sorted(unsupported))
    return [layer]


# ---------------------------------------------------------------- KML / KMZ / GPX

def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def kml_coordinates(text):
    """'lon,lat,alt lon,lat,alt ...' -> list of (lon, lat, alt)."""
    out = []
    for item in re.sub(r",\s+", ",", text or "").split():
        parts = item.split(",")
        if len(parts) >= 2:
            try:
                out.append((float(parts[0]), float(parts[1]), float(parts[2]) if len(parts) > 2 else 0.0))
            except ValueError:
                pass
    return out


def read_kml_root(root, stem):
    layer = Layer(stem, True)
    add_point, flush = point_batch(layer)

    def coords_of(element):
        for child in element.iter():
            if local(child.tag) == "coordinates":
                return kml_coordinates(child.text)
        return []

    for element in root.iter():
        kind = local(element.tag)
        if kind == "Point":
            for p in coords_of(element)[:1]:
                add_point(p)
        elif kind == "LineString":
            layer.line(coords_of(element))
        elif kind == "Polygon":
            outer, inner = [], []
            for boundary in element:
                side = local(boundary.tag)
                if side in ("outerBoundaryIs", "innerBoundaryIs"):
                    ring = coords_of(boundary)
                    if ring:
                        (outer if side == "outerBoundaryIs" else inner).append(ring)
            if outer:
                layer.polygon(outer[:1] + inner)
    flush()
    return [layer]


def read_kml(path, stem):
    return read_kml_root(ET.parse(path).getroot(), stem)


def read_kmz(path, stem):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".kml")]
        if not names:
            raise ValueError("No .kml file found inside this .kmz.")
        names.sort(key=lambda n: (n.lower() != "doc.kml", n.count("/")))
        root = ET.fromstring(z.read(names[0]))
    return read_kml_root(root, stem)


def read_gpx(path, stem):
    layer = Layer(stem, True)
    add_point, flush = point_batch(layer)

    def position(element):
        try:
            ele = next((c.text for c in element if local(c.tag) == "ele"), None)
            return float(element.get("lon")), float(element.get("lat")), float(ele) if ele else 0.0
        except (TypeError, ValueError):
            return None

    for element in ET.parse(path).getroot().iter():
        kind = local(element.tag)
        if kind == "wpt":
            p = position(element)
            if p:
                add_point(p)
        elif kind in ("trkseg", "rte"):
            pts = [position(c) for c in element if local(c.tag) in ("trkpt", "rtept")]
            layer.line([p for p in pts if p])
    flush()
    return [layer]


# ---------------------------------------------------------------- OpenStreetMap XML

AREA_KEYS = {"building", "building:part", "landuse", "leisure", "amenity", "shop", "tourism", "office", "water",
             "area:highway", "aeroway", "man_made", "historic", "place", "boundary"}
NOT_AREA_NATURAL = {"coastline", "cliff", "ridge", "tree_row", "valley", "arete"}


def osm_is_area(tags):
    if tags.get("area") == "no":
        return False
    if tags.get("area") == "yes" or any(k in tags for k in AREA_KEYS):
        return True
    return "natural" in tags and tags["natural"] not in NOT_AREA_NATURAL


def read_osm(path, stem):
    nodes, tagged, used, ways, relations = {}, set(), set(), [], 0
    parent = None  # the node, way or relation whose <tag> children are being read
    node_id = None
    for event, element in ET.iterparse(path, events=("start", "end")):
        kind = local(element.tag)
        if event == "start":
            if kind == "node":
                parent, node_id = kind, element.get("id")
                try:
                    nodes[node_id] = (float(element.get("lon")), float(element.get("lat")))
                except (TypeError, ValueError):
                    pass
            elif kind == "way":
                parent = kind
                ways.append(([], {}))
            elif kind == "relation":
                parent = kind
            elif kind == "nd" and parent == "way":
                ways[-1][0].append(element.get("ref"))
            elif kind == "tag":
                if parent == "node":
                    tagged.add(node_id)
                elif parent == "way":
                    ways[-1][1][element.get("k")] = element.get("v")
        elif kind in ("node", "way", "relation"):
            relations += kind == "relation"
            parent = None
            element.clear()
    areas, lines, loose = Layer(stem + " areas", True), Layer(stem + " lines", True), Layer(stem + " points", True)
    for refs, tags in ways:
        pts = [(*nodes[r], 0.0) for r in refs if r in nodes]
        used.update(refs)
        if len(pts) < 2:
            continue
        if len(pts) >= 4 and refs[0] == refs[-1] and osm_is_area(tags):
            areas.polygon([pts])
        else:
            lines.line(pts)
    add_point, flush = point_batch(loose)
    for ident in tagged - used:
        if ident in nodes:
            add_point((*nodes[ident], 0.0))
    flush()
    if relations:
        print("skipped %d relations (multipolygons are not read)" % relations)
    return [areas, lines, loose]


# ---------------------------------------------------------------- GeoPackage

def read_wkb(buf, offset, layer, add_point):
    """One WKB geometry at `offset` (ISO or EWKB); returns the offset after it."""
    e = "<" if buf[offset] == 1 else ">"
    (code,) = struct.unpack_from(e + "I", buf, offset + 1)
    offset += 5
    if code & 0x20000000:
        offset += 4  # EWKB SRID
    base, iso = code & 0x0FFFFFFF, 0
    has_z, has_m = bool(code & 0x80000000), bool(code & 0x40000000)
    iso, base = divmod(base, 1000)
    has_z, has_m = has_z or iso in (1, 3), has_m or iso in (2, 3)
    dims = 2 + has_z + has_m

    def coords(count):
        nonlocal offset
        array = np.frombuffer(buf, e + "f8", count * dims, offset).reshape(count, dims)
        offset += 8 * count * dims
        return array[:, :3] if has_z else array[:, :2]

    def count():
        nonlocal offset
        (n,) = struct.unpack_from(e + "I", buf, offset)
        offset += 4
        return n

    if base == 1:
        p = coords(1)[0]
        if np.all(np.isfinite(p)):
            add_point(tuple(p) + ((0.0,) if len(p) == 2 else ()))
    elif base == 2:
        layer.line(coords(count()))
    elif base == 3:
        rings = [coords(count()) for _ in range(count())]
        if rings:
            layer.polygon(rings)
    elif base in (4, 5, 6, 7):
        for _ in range(count()):
            offset = read_wkb(buf, offset, layer, add_point)
    else:
        raise ValueError("WKB type %d" % code)
    return offset


def read_gpkg(path, stem):
    import pathlib
    import sqlite3
    db = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro", uri=True)
    layers = []
    try:
        tables = db.execute("SELECT table_name, column_name, srs_id FROM gpkg_geometry_columns").fetchall()
        for table, column, srs in tables:
            row = db.execute("SELECT organization, organization_coordsys_id, definition FROM gpkg_spatial_ref_sys "
                             "WHERE srs_id = ?", (srs,)).fetchone()
            geographic, factor = None, 1.0
            if row:
                if str(row[0]).upper() == "EPSG":
                    geographic = geographic_from_epsg(int(row[1]))
                from_wkt, factor = unit_from_prj(row[2] or "")
                if geographic is None:
                    geographic = from_wkt
                if geographic:
                    factor = 1.0
            layer = Layer(table if len(tables) > 1 else stem, geographic, factor)
            add_point, flush = point_batch(layer)
            quoted = lambda s: '"' + s.replace('"', '""') + '"'  # noqa: E731
            for (blob,) in db.execute("SELECT %s FROM %s" % (quoted(column), quoted(table))):
                if not blob or len(blob) < 8 or blob[:2] != b"GP" or blob[3] & 0x10:
                    continue  # no geometry, or an empty one
                envelope = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get((blob[3] >> 1) & 7, 0)
                try:
                    read_wkb(bytes(blob), 8 + envelope, layer, add_point)
                except (ValueError, struct.error):
                    layer.skipped += 1
            flush()
            layers.append(layer)
    finally:
        db.close()
    return layers


READERS = {".geojson": read_geojson, ".json": read_geojson, ".shp": read_shp, ".kml": read_kml, ".kmz": read_kmz,
           ".gpx": read_gpx, ".osm": read_osm, ".gpkg": read_gpkg}


def run(src, outdir):
    t0 = time.time()
    ext = os.path.splitext(src)[1].lower()
    stem = os.path.splitext(os.path.basename(src))[0]
    print("PROGRESS 5 Reading " + ext, flush=True)
    try:
        layers = READERS[ext](src, stem)
    except (ValueError, KeyError, OSError) as ex:
        print("Could not read this file as %s: %s" % (ext, ex), file=sys.stderr, flush=True)
        return 1
    collector = MeshCollector()
    print("PROGRESS 90 Building geometry", flush=True)
    note = emit_layers(layers, collector)
    if not len(collector):
        print("No polygons, lines or points found in this file.", file=sys.stderr, flush=True)
        return 1
    collector.save(outdir)
    skipped = sum(layer.skipped for layer in layers)
    print("gis: %d triangles, %d edges, %d points in %d meshes; %s%s [%.0fs]" % (
        collector.triangles, collector.edges, collector.points, len(collector), note,
        "; %d invalid shapes skipped" % skipped if skipped else "", time.time() - t0))
    return 0


if __name__ == "__main__":
    code = run(sys.argv[1], sys.argv[2])
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
