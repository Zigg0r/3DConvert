NATIVE_FORMATS = {  # Blender imports these itself
    ".blend": "Blender (cleanup only)",
    ".obj": "Wavefront OBJ",
    ".fbx": "Autodesk FBX",
    ".stl": "STL",
    ".ply": "Stanford PLY",
    ".gltf": "glTF",
    ".glb": "glTF Binary",
    ".usd": "USD",
    ".usda": "USD ASCII",
    ".usdc": "USD Crate",
    ".usdz": "USDZ package",
    ".abc": "Alembic",
}
OTHER_FORMATS = {  # converted by this program
    ".3ds": "3D Studio (3ds Max)",
    ".3mf": "3MF",
    ".amf": "AMF",
    ".dae": "COLLADA",
    ".ifc": "IFC (BIM)",
    ".ifczip": "IFC (zipped)",
    ".ifcxml": "IFC (XML)",
    ".3dm": "Rhino 3DM",
    ".stp": "STEP",
    ".step": "STEP",
    ".igs": "IGES",
    ".iges": "IGES",
    ".dxf": "AutoCAD DXF",
    ".dwg": "AutoCAD DWG",
    ".skp": "SketchUp",
    ".wrl": "VRML",
    ".x3d": "X3D",
    ".x3dv": "X3D (classic)",
    ".max": "3ds Max",
    ".geojson": "GeoJSON",
    ".json": "GeoJSON",
    ".shp": "Shapefile",
    ".gpkg": "GeoPackage",
    ".kml": "Google Earth KML",
    ".kmz": "Google Earth KMZ",
    ".gpx": "GPX track",
    ".osm": "OpenStreetMap XML",
    ".tif": "GeoTIFF elevation grid",
    ".tiff": "GeoTIFF elevation grid",
    ".asc": "ESRI ASCII grid / XYZ text",
    ".las": "LAS point cloud",
    ".laz": "LAZ point cloud",
    ".e57": "E57 point cloud",
    ".pts": "PTS point cloud",
    ".xyz": "XYZ point cloud",
    ".pcd": "PCD point cloud",
    ".vrm": "VRM avatar",
    ".x3dz": "X3D (compressed)",
    ".wrz": "VRML (compressed)",
    ".unitypackage": "Unity package",
    ".x": "DirectX",
    ".off": "Geomview OFF",
    ".lwo": "LightWave object",
    ".lws": "LightWave scene",
    ".md2": "Quake 2 model",
    ".md3": "Quake 3 model",
    ".md5mesh": "Doom 3 model",
    ".ms3d": "MilkShape 3D",
    ".smd": "Source engine model",
    ".ac": "AC3D",
    ".ase": "3ds ASCII scene",
    ".cob": "trueSpace",
}
SUPPORTED = {**NATIVE_FORMATS, **OTHER_FORMATS}

# Closed formats that cannot be read directly: what to export instead
EXPORT_HINTS = {
    ".nwd": "Navisworks is a closed format. Export FBX from Navisworks, or convert the source models "
            "(Revit IFC, DWG, ...) instead.",
    ".nwc": "Navisworks is a closed format. Export FBX from Navisworks, or convert the source models "
            "(Revit IFC, DWG, ...) instead.",
    ".nwf": "Navisworks is a closed format. Export FBX from Navisworks, or convert the source models "
            "(Revit IFC, DWG, ...) instead.",
    ".rvt": "Revit is a closed format that only Revit can read. In Revit use File > Export > IFC (supported "
            "here), or export FBX / DWG.",
    ".rfa": "Revit families are a closed format. In Revit use File > Export > IFC, or export FBX / DWG.",
    ".f3d": "Fusion 360 .f3d is a closed format. In Fusion use File > Export as STEP (supported here) or FBX.",
    ".dgn": "MicroStation DGN is a closed format. In MicroStation use File > Export as DWG/DXF or IFC "
            "(all supported here), or FBX.",
    ".sldprt": "SOLIDWORKS parts are a closed format. In SOLIDWORKS use File > Save As > STEP (supported here), "
               "IGES or STL.",
    ".sldasm": "SOLIDWORKS assemblies are a closed format. In SOLIDWORKS use File > Save As > STEP (supported "
               "here).",
    ".ipt": "Inventor parts are a closed format. In Inventor use Save Copy As > STEP (supported here).",
    ".iam": "Inventor assemblies are a closed format. In Inventor use Save Copy As > STEP (supported here).",
    ".catpart": "CATIA parts are a closed format. In CATIA use Save As > STEP (supported here) or IGES.",
    ".catproduct": "CATIA assemblies are a closed format. In CATIA use Save As > STEP (supported here).",
    ".cgr": "CATIA .cgr is a closed lightweight format. In CATIA export STEP or FBX instead.",
    ".3dxml": "3DXML is a closed Dassault format. Export STEP (supported here) from the 3DEXPERIENCE / CATIA "
              "application.",
    ".x_t": "Parasolid is a closed kernel format. Export STEP (supported here) from the CAD program.",
    ".x_b": "Parasolid is a closed kernel format. Export STEP (supported here) from the CAD program.",
    ".sat": "ACIS .sat is a closed kernel format. Export STEP (supported here) from the CAD program.",
    ".sab": "ACIS .sab is a closed kernel format. Export STEP (supported here) from the CAD program.",
    ".jt": "JT is a closed visualization format. Export STEP (supported here) or FBX from the CAD program.",
}

# Test status per extension, shown as the orb on the supported-formats page. Only extensions that have been tested are
# listed; any other extension is "Not tested".
#   FULL     known to work every time (many files, all converted correctly)
#   LIMITED  every real-world file tried so far converted correctly, but only a few were tried
#   PARTIAL  real-world files were met that converted and files that failed or lost geometry
# When more tests of an extension change the picture, move it between the sets below.
FULL, LIMITED, PARTIAL, UNTESTED = "Fully supported", "Supported (limited testing)", "Partially supported", "Not tested"
EXTENSION_STATUS = {
    **dict.fromkeys([".blend", ".obj", ".fbx"], FULL),
    **dict.fromkeys([".stl", ".ply", ".gltf", ".glb", ".usd", ".usdc", ".abc", ".ifc", ".3dm", ".skp", ".step",
                     ".stp", ".geojson", ".json", ".tif", ".tiff", ".3ds", ".dae", ".wrl", ".unitypackage"], LIMITED),
    **dict.fromkeys([".dxf"], PARTIAL),  # 2D entities are ignored; newer binary ACIS solids can fail in ezdxf
    **dict.fromkeys([".dwg"], PARTIAL),  # complete with the ODA File Converter; the built-in LibreDWG loses 2013+ solids
    **dict.fromkeys([".max"], PARTIAL),  # converts, but the bundled importer drops or misplaces some geometry
    **dict.fromkeys([".lwo"], PARTIAL),  # ASSIMP loses the points of LightWave layers with several point chunks
}
_RANK = {PARTIAL: 0, LIMITED: 1, FULL: 2}
# Programs shown on the supported-formats page: (name, extensions, short description).
PROGRAMS = [
    ("3MF", [".3mf"], "3D printing format with meshes, colours, units and components."),
    ("AC3D / ASE / COB", [".ac", ".ase", ".cob"], "AC3D, 3ds ASCII scene and trueSpace models, read with ASSIMP."),
    ("Alembic", [".abc"], "Baked geometry caches from Maya, Houdini and others; imported through Blender."),
    ("AMF", [".amf"], "Additive manufacturing format with meshes, colours and units (plain or zipped)."),
    ("Autodesk 3ds Max", [".3ds", ".max"], "Legacy 3DS meshes (own reader) and 3ds Max scenes (bundled importer)."),
    ("Autodesk FBX", [".fbx"], "Meshes and materials exchanged between many programs; imported through Blender."),
    ("AutoCAD", [".dwg", ".dxf"], "3D faces, meshes, solids; DWG via built-in LibreDWG or ODA File Converter."),
    ("Blender", [".blend"], "Blender files; opened and cleaned, nothing else."),
    ("COLLADA", [".dae"], "Scenes from SketchUp, Blender and others; triangles, polylists and polygons."),
    ("DirectX", [".x"], "DirectX models (text and binary), read with ASSIMP in their rest pose."),
    ("E57", [".e57"], "Laser scanner point clouds with colours, as a Blender point cloud; all scans are merged."),
    ("ESRI ASCII grid", [".asc"], "Elevation grids as terrain; a plain x y z text file is read as a point cloud."),
    ("GeoJSON", [".geojson", ".json"], "GIS and PDOK data: polygons become faces, lines edges, points vertices."),
    ("GeoPackage", [".gpkg"], "GIS layers from QGIS and PDOK; every layer becomes its own object."),
    ("Geomview OFF", [".off"], "Simple polygon meshes from research and scanning tools, read with ASSIMP."),
    ("GeoTIFF", [".tif", ".tiff"], "Elevation grids (AHN DTM/DSM) as a terrain mesh; nodata cells stay open."),
    ("glTF", [".glb", ".gltf"], "Web and game meshes with materials; imported through Blender."),
    ("Google Earth", [".kml", ".kmz"], "Placemarks, paths and polygons in longitude/latitude, converted to UTM metres."),
    ("GPX", [".gpx"], "GPS tracks and routes as lines, waypoints as points; converted to UTM metres."),
    ("IFC (BIM)", [".ifc", ".ifczip", ".ifcxml"], "Building models from Revit, ArchiCAD and others; IfcOpenShell."),
    ("IGES", [".iges", ".igs"], "CAD exchange format; surfaces are tessellated with OpenCASCADE."),
    ("LAS / LAZ", [".las", ".laz"], "Lidar point clouds with colours, as a Blender point cloud (very large ones thinned)."),
    ("LightWave", [".lwo", ".lws"], "LightWave objects and scenes, read with ASSIMP."),
    ("MilkShape / Source", [".ms3d", ".smd"], "MilkShape 3D and Source engine models, read with ASSIMP in their rest pose."),
    ("OpenStreetMap", [".osm"], "OSM XML: buildings and areas as faces, roads as lines, nodes as points; no heights."),
    ("PCD", [".pcd"], "Point Cloud Library files (ASCII, binary and compressed), as a Blender point cloud."),
    ("PTS / XYZ", [".pts", ".xyz"], "Text point clouds with optional colours, as a Blender point cloud."),
    ("Quake models", [".md2", ".md3", ".md5mesh"], "Quake 2, Quake 3 and Doom 3 models, read with ASSIMP in their rest pose."),
    ("Rhino", [".3dm"], "Rhino models; render meshes are read with rhino3dm."),
    ("Shapefile", [".shp"], "Esri polygons (with holes), lines and points; the .prj file gives the units."),
    ("SketchUp", [".skp"], "SketchUp models with groups, components and materials (OpenSKP as fallback)."),
    ("Stanford PLY", [".ply"], "Polygon meshes with vertex colours; imported through Blender."),
    ("STEP", [".step", ".stp"], "CAD solids and assemblies; tessellated with OpenCASCADE."),
    ("STL", [".stl"], "Triangle meshes for 3D printing; imported through Blender."),
    ("Unity package", [".unitypackage"], "Models (FBX, OBJ, ...) taken from the package; several become collections."),
    ("USD", [".usd", ".usda", ".usdc", ".usdz"], "Pixar Universal Scene Description; imported through Blender."),
    ("VRM", [".vrm"], "VRM avatars are glTF files; read like .glb, without the MToon shaders."),
    ("VRML / X3D", [".wrl", ".x3d", ".x3dv"], "Web3D scenes: VRML97 and X3D, XML and classic encoding."),
    ("VRML / X3D (gzip)", [".wrz", ".x3dz"], "Compressed VRML and X3D files; unpacked, then read as usual."),
    ("Wavefront OBJ", [".obj"], "Simple meshes with materials; imported through Blender."),
]


def program_status(extensions):
    """The lowest status of the tested extensions; at most LIMITED while some extension is untested, UNTESTED when none
    has been tested."""
    known = [EXTENSION_STATUS[ext] for ext in extensions if ext in EXTENSION_STATUS]
    if not known:
        return UNTESTED
    worst = min(known, key=_RANK.get)
    return LIMITED if worst == FULL and len(known) < len(extensions) else worst
