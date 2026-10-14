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

# Programs shown on the supported-formats page: (name, extensions, short description).
# An extension counts as tested when it is in TESTED (real-world file, or Blender's own importer).
TESTED = {".blend", ".obj", ".fbx", ".stl", ".ply", ".gltf", ".glb", ".usd", ".usdc", ".abc", ".ifc", ".3dm", ".skp",
          ".step", ".stp"}
PROGRAMS = [
    ("3MF", [".3mf"], "3D printing format with meshes, colours, units and components."),
    ("Alembic", [".abc"], "Baked geometry caches from Maya, Houdini and others; imported through Blender."),
    ("AMF", [".amf"], "Additive manufacturing format with meshes, colours and units (plain or zipped)."),
    ("Autodesk 3ds Max", [".3ds", ".max"], "Legacy 3DS meshes (own reader) and 3ds Max scenes (bundled importer)."),
    ("Autodesk FBX", [".fbx"], "Meshes and materials exchanged between many programs; imported through Blender."),
    ("AutoCAD", [".dwg", ".dxf"], "3D faces, meshes and solids; DWG needs the free ODA File Converter."),
    ("Blender", [".blend"], "Blender files; opened and cleaned, nothing else."),
    ("COLLADA", [".dae"], "Scenes from SketchUp, Blender and others; triangle geometry only."),
    ("glTF", [".glb", ".gltf"], "Web and game meshes with materials; imported through Blender."),
    ("IFC (BIM)", [".ifc", ".ifczip", ".ifcxml"], "Building models from Revit, ArchiCAD and others; IfcOpenShell."),
    ("IGES", [".iges", ".igs"], "CAD exchange format; surfaces are tessellated with OpenCASCADE."),
    ("Rhino", [".3dm"], "Rhino models; render meshes are read with rhino3dm."),
    ("SketchUp", [".skp"], "SketchUp models with groups, components and materials (OpenSKP as fallback)."),
    ("Stanford PLY", [".ply"], "Polygon meshes with vertex colours; imported through Blender."),
    ("STEP", [".step", ".stp"], "CAD solids and assemblies; tessellated with OpenCASCADE."),
    ("STL", [".stl"], "Triangle meshes for 3D printing; imported through Blender."),
    ("USD", [".usd", ".usda", ".usdc", ".usdz"], "Pixar Universal Scene Description; imported through Blender."),
    ("VRML / X3D", [".wrl", ".x3d", ".x3dv"], "Web3D scenes: VRML97 and X3D, XML and classic encoding."),
    ("Wavefront OBJ", [".obj"], "Simple meshes with materials; imported through Blender."),
]


def program_status(extensions):
    """'Supported' when every extension is tested, 'Not tested' when none is, else 'Partially tested'."""
    done = sum(ext in TESTED for ext in extensions)
    return "Supported" if done == len(extensions) else "Not tested" if done == 0 else "Partially tested"
