# 3DConvert

Drop 3D files, get clean `.blend` files.

3DConvert is a portable Windows program that converts 3D files from many CAD, BIM and DCC formats into Blender files. It runs Blender in the background (no Blender window opens), keeps the geometry at its original coordinates and tidies the result with 30 optional cleanup steps.

- **Local only.** Files are never uploaded. The program and the Blender it starts make no network connections (Blender runs with factory settings, so its online access stays off).
- **Portable.** One `.exe`, no installer. Blender itself is not included and must be installed.
- **Many formats.** IFC, Rhino, SketchUp, DXF/DWG, COLLADA, STEP, IGES, VRML/X3D, 3MF, AMF, 3ds Max and everything Blender imports natively. The file icon in the title bar lists every supported program, its extensions and whether it has been tested.
- **Cleanup included.** Duplicate vertices, normals, materials, hidden objects, orphan data and more are handled automatically. Every step can be switched off.

> Version 1.10. Large parts of this program were written with AI assistance. Expect bugs and check the result before relying on it. There is no warranty (see [License](#license)).

## Contents

- [Supported formats](#supported-formats)
- [Requirements](#requirements)
- [Using the program](#using-the-program)
- [What ends up in the .blend](#what-ends-up-in-the-blend)
- [Cleanup steps](#cleanup-steps)
- [How it works](#how-it-works)
- [Repository layout](#repository-layout)
- [Building from source](#building-from-source)
- [Bundled libraries](#bundled-libraries)
- [Limitations](#limitations)
- [License](#license)

## Supported formats

| Format | Extensions | Converted by | Needs |
|---|---|---|---|
| Blender | `.blend` | opened and cleaned | Blender |
| Wavefront OBJ | `.obj` | Blender importer | Blender |
| FBX | `.fbx` | Blender importer | Blender |
| STL | `.stl` | Blender importer | Blender |
| Stanford PLY | `.ply` | Blender importer | Blender |
| glTF | `.gltf` `.glb` | Blender importer | Blender |
| USD | `.usd` `.usda` `.usdc` `.usdz` | Blender importer | Blender |
| Alembic | `.abc` | Blender importer | Blender |
| 3D Studio | `.3ds` | built-in reader (meshes, per-face materials, diffuse colours) | Blender |
| 3ds Max | `.max` | bundled *Import Autodesk MAX* add-on | Blender |
| 3MF | `.3mf` | built-in reader (meshes, components, base materials and colour groups, units) | Blender 5.x |
| AMF | `.amf` | built-in reader (plain or zipped XML; volumes, colours, constellations, units) | Blender 5.x |
| IFC (BIM) | `.ifc` | IfcOpenShell, or a direct reader for files that already contain meshes | Blender 5.x |
| IFC zipped / XML | `.ifczip` `.ifcxml` | unzipped, or converted to IFC text with IfcOpenShell, then as `.ifc` | Blender 5.x |
| Rhino | `.3dm` | rhino3dm | Blender 5.x |
| SketchUp | `.skp` | SketchUp C API binding (optional), else OpenSKP (MIT) | Blender 5.x |
| COLLADA | `.dae` | built-in reader | Blender 5.x |
| AutoCAD DXF | `.dxf` | ezdxf | Blender 5.x |
| AutoCAD DWG | `.dwg` | ODA File Converter (to DXF), then ezdxf | Blender 5.x and the ODA File Converter |
| VRML / X3D | `.wrl` `.x3d` `.x3dv` | built-in readers (VRML97, X3D XML and classic encoding) | Blender 5.x |
| STEP | `.stp` `.step` | OpenCASCADE (via cascadio) to glTF, then Blender | Blender 5.x |
| IGES | `.igs` `.iges` | OpenCASCADE (via cascadio) to glTF, then Blender | Blender 5.x |

Closed formats that cannot be read directly (Navisworks, Revit, Fusion 360, MicroStation, SOLIDWORKS, Inventor, CATIA, Parasolid, ACIS, JT) are recognised: the program explains what to export instead (IFC, FBX, DWG, STEP, ...).

## Requirements

- Windows 10 or 11, 64-bit.
- [Blender](https://www.blender.org/download/). Blender 5.2 or newer is required: at start the program looks for it and says so in the line at the bottom left (grey when 5.2 or newer is found, red otherwise). With an older or missing Blender the drop zone is greyed out and **Create .blend** stays disabled. (The bundled libraries are built for the Python inside Blender 5.) The program uses the newest Blender it finds in `C:\Program Files\Blender Foundation\Blender *`; set the environment variable `BLENDER` to the full path of `blender.exe` to use another one.
- DWG only: the free ODA File Converter from [opendesign.com](https://www.opendesign.com/). It is found in `Program Files\ODA\`, or through the environment variable `ODA_FILE_CONVERTER` (full path of `ODAFileConverter.exe`).

## Using the program

1. Start `3DConvert.exe`.
2. Drop one or more files on the drop zone, or click it and pick files. Files are added to the list, never replaced, and a file that is already in the list is skipped. The files are scanned one after another; the *Imported file information* panel shows what the selected file contains (format, size, counts, dimensions, materials, ...). Use the small arrows next to **File 3 / 8** at the top of the panel to switch between the files; the drop zone follows. The trash icon next to it removes only the selected file.
3. Optional: *Change location* sets the output folder (default: the folder of the source file). The cog button in the title bar opens the cleanup steps; untick what you do not want.
4. Choose **Separate** (default: one `.blend` per file) or **Combined** (all files in one `.blend`, each in its own collection named after the file; a file's `!HIDDEN` collection sits inside its collection and stays switched off).
5. Click **Create .blend**. The step list shows the running step of the current file and the bar shows the overall progress. A file that fails is skipped and reported; the others are still converted.
6. The result is saved as `<name>.blend` (numbered, `<name> (1).blend`, when the name exists); a combined file is called `<first file> + <n> more.blend` and goes to the folder of the first file. The folder button opens the output folder.

Other controls: *Clear data (n)* removes all files from the list, the **i** button shows what the program's size is made of, and the title bar drags the window. Closing the window cancels a running conversion.

**Command line.** `3DConvert.exe --convert <source> <target.blend>` converts without a window (exit code 0 on success, 1 on failure).

**Files the program writes.** The bundled libraries are unpacked once to `%LOCALAPPDATA%\3DConvert\libs`, and the log of the last conversion is `%LOCALAPPDATA%\3DConvert\last_convert.log`. Working files go to a temporary folder that is removed after each conversion.

## What ends up in the .blend

- **IFC, Rhino, SketchUp, DXF/DWG, COLLADA, VRML:** one mesh object per material (named after the material) at the original coordinates, in metres where the file states its unit. Materials are plain colours. Objects that are hidden in the source (hidden layers, tags or entities) stay hidden and, with the *Hidden things sorted* step on, are collected in the `!HIDDEN` collection.
- **OBJ, FBX, glTF, STL, PLY, USD, Alembic, 3DS, MAX, Blender:** the objects and materials of the file, then cleaned.
- **STEP, IGES:** the shapes are tessellated with relative tolerances, the axes are corrected to Z-up and assemblies are flattened.

## Cleanup steps

All steps are on by default and can be switched off one by one (cog button). They run on every object of the converted scene.

| Step | What it does |
|---|---|
| Animation removed | Clears keyframes, drivers and actions; objects keep their current pose. |
| Bones and armatures removed | Deletes armatures, bone weight groups and armature modifiers. |
| Cameras, lights and world removed | Deletes cameras, lights and the world from the file. |
| Clean face layout | Triangulates, then rejoins nearly flat faces into quads. |
| Curves and text turned into meshes | Converts curves, surfaces, text and metaballs to mesh objects. |
| Degenerate geometry dissolved | Removes zero-area faces and zero-length edges. |
| Duplicate faces and objects removed | Deletes faces on the same vertices and identical objects in place. |
| Duplicate vertices merged | Welds vertices closer than 0.1 mm. |
| Empty meshes removed | Deletes mesh objects without any faces or edges. |
| Empty objects removed | Deletes empties (axes, group holders); collection instances stay, children keep their place. |
| Extra mesh data removed | Clears shape keys, weight groups, bevel/crease weights and custom properties. |
| Flat shading, no marks | Clears smooth shading, sharp edges and UV seams. |
| Hidden geometry unhidden | Unhides faces, edges and vertices hidden inside meshes. |
| Hidden things sorted | Moves hidden objects to the `!HIDDEN` collection. |
| Importer leftovers removed | Drops `ios_*` data, paint masks, skin data and custom normals. |
| Loose edges and points removed | Deletes stray edges and vertices without faces. |
| Materials merged by colour | Combines materials with the same colour into one. |
| Modifiers removed | Deletes all modifiers without applying them; the name gets a note. |
| Names tidied | Strips `.001` suffixes when free and deletes empty collections. |
| Normals recalculated | Points every face outward, consistently. |
| Orphan data purged | Clears unused meshes, materials and images, repeated until none are left. |
| Parents cleared, position kept | Clear Parent with Keep Transform on every object. |
| Physics removed | Clears cloth, soft body, rigid body, particles, fluid and force fields. |
| Scale and rotation applied | Bakes scale and rotation into the mesh; positions stay the same. |
| Scene tidied | One scene, metric units at scale 1, timeline reset. |
| Simple materials | Plain base colour only: no textures or transparency. |
| Stray objects removed | Deletes objects that sit in no scene or collection. |
| Stuck-invisible objects fixed | Turns *Show in Viewport* back on; hiding stays on the eye icon. |
| Unused material slots dropped | Removes material slots that no face uses. |
| UV maps and colours removed | Deletes UV maps and vertex colour attributes. |

## How it works

```mermaid
flowchart LR
    UI["GUI<br/>app/main.py"] --> PL["Pipeline<br/>app/pipeline.py"]
    PL -->|"IFC, 3DM, SKP, DXF, DWG, DAE, WRL"| RD["Reader script<br/>engine/readers/"]
    RD -->|"geometry.npz + geometry.json"| BB["Blender, background<br/>build_blend.py"]
    PL -->|"STEP, IGES"| ST["step_to_glb.py<br/>OpenCASCADE"]
    ST -->|"temporary glTF"| CN
    PL -->|"OBJ, FBX, STL, PLY, glTF, USD, ABC, 3DS, MAX, BLEND"| CN["Blender, background<br/>convert_native.py"]
    BB --> CL["cleanup.py"]
    CN --> CL
    CL --> OUT[".blend file"]
```

- **GUI** (`app/main.py`): a PySide6 window with the drop zone, the information panel, the step list and the cleanup and size pages. It runs the conversion on a worker thread and shows the progress that the scripts print (`PROGRESS` and `STEP` lines).
- **Pipeline** (`app/pipeline.py`): finds Blender, unpacks the bundled libraries, starts every step as a separate process, reads their progress, supports cancelling and writes the log.
- **Information scanners** (`app/scanners.py`, `app/obj_reader.py`): pure-Python scanners per format for the information panel. Only 3DM, SketchUp, USD, Alembic, MAX and Blender files need a short Blender run for that.
- **Readers** (`engine/readers/`): scripts that run with the Python that ships with Blender, with the bundled libraries on `PYTHONPATH`. Each one turns a foreign file into triangles and writes `geometry.npz` and `geometry.json` (one mesh per material, a hidden flag and the colours; see `engine/geometry_io.py`). The IFC route first measures how much of the file is tessellated geometry: mostly meshes go to `ifc_meshes.py` (direct text reader), everything else is stripped to physical elements by `ifc_reduce.py` and tessellated by `ifc_solids.py` with IfcOpenShell on all cores.
- **Blender side** (`engine/in_blender/`): `build_blend.py` builds the objects from the reader output, `convert_native.py` imports with Blender's own importers, `cleanup.py` is the cleaning pass, and `combine_blends.py` merges the finished files of a *Combined* run. The cleanup steps the user switched off arrive in the environment variable `CLEANUP_OFF`.

The bundled libraries are zip files; compiled modules cannot be imported from a zip, so they are unpacked once to `%LOCALAPPDATA%\3DConvert\libs` and unpacked again when the wheels change.

## Repository layout

```
app/                       the program (runs inside the exe)
    main.py                window, drop zone, information/cleanup/size pages, headless --convert
    pipeline.py            Blender discovery, library unpacking, process control, progress, log
    scanners.py            information scanners per format
    obj_reader.py          OBJ statistics for the information panel
    formats.py             supported formats and export hints
    icon.py                the cube icon, drawn in code
engine/
    readers/               one script per foreign format (run with Blender's Python)
    in_blender/            scripts run by Blender itself (build, native import, cleanup, combine, scan)
    addons/io_scene_max/   bundled Blender add-on for .max files
    wheels/                bundled Python libraries (Windows x64, Python 3.13)
    geometry_io.py         hand-over format between a reader and build_blend.py
    ifc_text.py            streaming access to IFC entities
    polygons.py            polygon triangulation shared by the readers
assets/                    icon.ico and splash.png (generated by build/make_icon.py and make_splash.py)
build/                     build.bat, 3DConvert.spec, requirements.txt, make_icon.py, make_splash.py
LICENSE                    GNU General Public License, version 3
THIRD_PARTY_NOTICES.md     licenses of everything that is bundled or used
```

## Building from source

Needs Windows, Python 3.12 (64-bit, installed with the `py` launcher) and internet access the first time.

```
build\build.bat
```

The script creates a virtual environment in `.venv`, installs PySide6 and PyInstaller into it, regenerates the icon and splash image, and builds `dist\3DConvert.exe` (about 94 MB, one file). Pass `nopause` to skip the final pause. Work files go to `%LOCALAPPDATA%\3DConvert\build`.

To run from source instead, use the environment created by the build:

```
.venv\Scripts\python.exe app\main.py
.venv\Scripts\python.exe app\main.py --convert input.obj output.blend
```

Everything the exe contains (`engine/` with the scripts, the wheels and the add-on) is taken from this repository, so a change to a script only needs a rebuild. Bump `APP_VERSION` in `app/main.py` for a new release.

## Bundled libraries

`engine/wheels/` holds the Python libraries the readers need, built for Windows x64 and the Python 3.13 of Blender 5: IfcOpenShell, cascadio, rhino3dm, ezdxf, fontTools, Shapely, mapbox_earcut, Lark, isodate, pyparsing, python-dateutil, six, typing_extensions, OpenSKP with trimesh and defusedxml, and (optionally) a SketchUp binding. Most are the upstream wheels re-compressed with LZMA (same files, smaller exe); the app unpacks every `*.whl` in that folder itself, so pip is not involved. The optional SketchUp wheel was assembled from the `sketchup` module and the SketchUp C API libraries of the SketchUp Importer add-on, version 0.27.

To update a library, replace its wheel with a build for the same platform. When Blender moves to another Python version, the wheels must be replaced with matching ones. Licenses and versions are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Limitations

- Windows only. Blender is not included.
- Materials become plain colours; textures are not carried over.
- Geometry keeps its original coordinates. Blender stores vertex positions as 32-bit floats, so models that sit very far from the origin (for example in national grid coordinates) lose some precision.
- COLLADA: only `<triangles>` geometry is read and the file is assumed to be Z-up.
- Rhino: curved objects without a stored render mesh cannot be tessellated without Rhino and are skipped (they are counted in the log); curves, points and lights are ignored.
- SketchUp: with the optional Trimble binding, files saved by a SketchUp version newer than the bundled API may not open. Without it (or when it fails) OpenSKP reads the file. OpenSKP needs no SDK, but is slower on very large models (about 15 times on a 65 MB file), cannot tell that a single group or face is hidden (hidden layers/tags still work) and produces extra back-face triangles. Files from SketchUp 2019 are reported not to open with it; 2022 to 2024 are unverified.
- VRML / X3D: only VRML 2.0 / VRML97 and X3D, and only face sets, triangle sets and boxes (no spheres, cylinders, cones, extrusions or elevation grids).
- DXF / DWG: only 3D faces, meshes and solids become geometry; 2D lines, polylines and hatches are ignored.
- 3MF / AMF: units are converted to metres; slicer metadata, textures and support objects are ignored. AMF constellations are read, nested ones included.
- IFC: spaces, grids, annotations, property sets and similar non-geometric data are dropped; openings are applied to their walls. `.ifcxml` needs IfcOpenShell to read the XML.
- The supported-formats page (file icon in the title bar) shows which formats are tested. Not tested yet: `.max`, `.dwg`, `.dxf`, `.dae`, `.3ds`, `.wrl`, IGES and all formats added in 1.09 have only been tried with generated samples, or not at all.

## License

3DConvert is free software: you can redistribute it and modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See [LICENSE](LICENSE) for the full text.

The libraries and the Blender add-on that are bundled keep their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The optional SketchUp C API libraries inside the SketchUp wheel are proprietary software of Trimble Inc. and are not covered by the GPL; that wheel is listed in `.gitignore` and should be left out of any build you publish. OpenSKP (MIT), which is bundled instead, has no such restriction.
