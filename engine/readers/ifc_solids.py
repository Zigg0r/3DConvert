"""Reduced IFC -> triangles with IfcOpenShell, on all CPU cores. Runs in Blender's Python.
An element that has parts (a wall with layers, a railing with balusters) counts only through its parts, otherwise
everything would be there twice. Material = the element's surface style, else its IfcMaterial name; the colour comes
from the style. Coordinates are metres, as stored (IfcOpenShell applies the file's units); nothing is moved.
Writes geometry.npz/json (see geometry_io.py). Usage: ifc_solids.py <reduced.ifc> <outdir>"""
import multiprocessing
import os
import sys
import time

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as util_element
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from geometry_io import DEFAULT_MATERIAL, MeshCollector  # noqa: E402


def material_name(element):
    """Name of the first IfcMaterial of an element ('' when there is none)."""
    try:
        materials = util_element.get_materials(element)
        return (materials[0].Name or "") if materials else ""
    except Exception:
        return ""


def run(path, outdir):
    t0 = time.time()
    model = ifcopenshell.open(path)
    print("opened [%.0fs]" % (time.time() - t0), flush=True)
    wholes = set()  # elements whose parts carry the geometry
    for rel in model.by_type("IfcRelAggregates"):
        whole = rel.RelatingObject
        if not whole.is_a("IfcSpatialStructureElement") and not whole.is_a("IfcProject"):
            if any(part.Representation for part in rel.RelatedObjects):
                wholes.add(whole.id())
    elements = [e for e in model.by_type("IfcElement")
                if e.Representation and e.id() not in wholes and not e.is_a("IfcOpeningElement")]
    print("%d elements with geometry, %d wholes skipped [%.0fs]" % (len(elements), len(wholes), time.time() - t0), flush=True)

    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    iterator = ifcopenshell.geom.iterator(settings, model, multiprocessing.cpu_count(), include=elements)
    collector = MeshCollector()
    done = 0
    step = max(len(elements) // 100, 1)
    if iterator.initialize():
        while True:
            shape = iterator.get()
            geometry = shape.geometry
            V = np.asarray(geometry.verts, dtype=np.float64).reshape(-1, 3)
            F = np.asarray(geometry.faces, dtype=np.int64).reshape(-1, 3)
            if len(F):
                material_ids = np.asarray(geometry.material_ids, dtype=np.int64)
                styles = geometry.materials
                fallback = None  # IfcMaterial name, looked up once per element and only when a style is missing
                for index in np.unique(material_ids):
                    style = styles[index] if 0 <= index < len(styles) else None
                    name = (style.name if style is not None else None) or DEFAULT_MATERIAL
                    if style is None or name.startswith("Ifc"):  # no surface style: the IfcMaterial name instead
                        if fallback is None:
                            fallback = material_name(model.by_id(shape.id))
                        name = fallback or name
                    colour = None
                    if style is not None:
                        diffuse = style.diffuse
                        colour = (float(diffuse.r()), float(diffuse.g()), float(diffuse.b()))
                    used, inverse = np.unique(F[material_ids == index], return_inverse=True)
                    collector.add(name, V[used], inverse.reshape(-1, 3), False, colour)
            done += 1
            if done % step == 0:
                print("PROGRESS %d Reading element %d/%d" % (100 * done // len(elements), done, len(elements)), flush=True)
            if done % 5000 == 0:
                print("  %d elements, %d triangles [%.0fs]" % (done, collector.triangles, time.time() - t0), flush=True)
            if not iterator.next():
                break
    if not len(collector):
        print("No geometry found in this IFC file.", file=sys.stderr, flush=True)
        return 1
    collector.save(outdir)
    print("done: %d elements, %d triangles in %d meshes [%.0fs]"
          % (done, collector.triangles, len(collector), time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1], sys.argv[2]))
