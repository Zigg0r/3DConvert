# Third-party notices

3DConvert is licensed under the GNU General Public License, version 3 or later (see [LICENSE](LICENSE)). It bundles and uses the third-party software listed here, each under its own license. Versions are the ones bundled with release 1.10. The license texts of most libraries are also inside their wheel files (`*.dist-info/licenses`).

## Libraries in `engine/wheels/`

These run inside the Python of Blender 5 and are unpacked to `%LOCALAPPDATA%\3DConvert\libs`.

| Library | Version | License | Notes |
|---|---|---|---|
| [IfcOpenShell](https://ifcopenshell.org) | 0.9.0 | LGPL-3.0-or-later | IFC geometry. Uses Open CASCADE Technology. |
| [cascadio](https://github.com/trimesh/cascadio) | 0.1.1 | MIT | STEP tessellation. The wheel contains the Open CASCADE Technology libraries (LGPL-2.1 with the Open CASCADE exception) and the Microsoft Visual C++ runtime. |
| [rhino3dm](https://github.com/mcneel/rhino3dm) | 8.35.0 | MIT | Reads `.3dm`. Based on openNURBS by Robert McNeel & Associates. |
| [ezdxf](https://github.com/mozman/ezdxf) | 1.4.4 | MIT | Reads DXF. |
| [fontTools](https://github.com/fonttools/fonttools) | 4.66.1 | MIT | Required by ezdxf. |
| [Shapely](https://github.com/shapely/shapely) | 2.2.0 | BSD-3-Clause | Required by IfcOpenShell. Contains GEOS (LGPL-2.1); its license text is in the wheel. |
| [mapbox_earcut](https://github.com/skogler/mapbox_earcut_python) | 2.1.0 | ISC | Polygon triangulation. |
| [Lark](https://github.com/lark-parser/lark) | 1.3.1 | MIT | Required by IfcOpenShell. |
| [isodate](https://github.com/gweis/isodate) | 0.7.2 | BSD-3-Clause | Required by IfcOpenShell. |
| [pyparsing](https://github.com/pyparsing/pyparsing) | 3.3.3 | MIT | Required by IfcOpenShell and ezdxf. |
| [python-dateutil](https://github.com/dateutil/dateutil) | 2.9.0.post0 | BSD-3-Clause or Apache-2.0 | Required by IfcOpenShell. |
| [six](https://github.com/benjaminp/six) | 1.17.0 | MIT | Required by python-dateutil. |
| [typing_extensions](https://github.com/python/typing_extensions) | 4.16.0 | PSF-2.0 | Required by IfcOpenShell and ezdxf. |
| [OpenSKP](https://github.com/iamahsanmehmood/openskp) | 1.3.0 | MIT | Reads `.skp` without any SketchUp software or SDK (reverse-engineered file format). Built from the `packages/python` folder of the project. |
| [trimesh](https://github.com/mikedh/trimesh) | 5.1.1 | MIT | Required by OpenSKP. |
| [defusedxml](https://github.com/tiran/defusedxml) | 0.7.1 | PSF-2.0 | Required by OpenSKP. |
| `sketchup` module (optional) | 0.27 | GPL-3.0-or-later | Taken unmodified from the SketchUp Importer add-on 0.27 (Martijn Berger and contributors). |
| SketchUp C API (`SketchUpAPI.dll`, `SketchUpCommonPreferences.dll`) (optional) | as shipped with that add-on | Proprietary, Trimble Inc. | Not covered by the GPL. See the SketchUp note below. |

The `sketchup` module and the two SketchUp libraries are packed in `sketchup_api-0.27-cp313-none-win_amd64.whl`, which was assembled from the files of the add-on. This wheel is optional: when it is missing, or cannot open a file, OpenSKP reads the SketchUp file instead.

## Blender add-on in `engine/addons/`

| Add-on | Version | License | Notes |
|---|---|---|---|
| [Import Autodesk MAX (`io_scene_max`)](https://github.com/nrgsille76/io_scene_max) | 1.9.2 | GPL-3.0-or-later (manifest); GPL-2.0-or-later (source file headers) | Copyright 2023-2026 Sebastian Schrand, 2017-2022 Jens M. Plonka, 2005-2018 Philippe Lagadec. Based on `olefile` and on the ImportMAX importer of FreeCAD. |

## Inside the exe

| Component | Version | License | Notes |
|---|---|---|---|
| [Python](https://www.python.org) runtime and the libraries that ship with it (OpenSSL 3, zlib, expat, Tcl/Tk, ...) | 3.12 | PSF-2.0 and the licenses of those libraries | See https://docs.python.org/3/license.html. |
| [Qt for Python (PySide6)](https://doc.qt.io/qtforpython-6/) with Qt 6 and Shiboken6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | Used under the LGPL-3.0 (or the GPL-3.0). |
| [PyInstaller](https://pyinstaller.org) bootloader | 6.22.3 | GPL-2.0-or-later with a special exception that allows distributing programs built with it under any license | Used to build the exe. |
| Microsoft Visual C++ and Universal C runtime files | | Microsoft redistributable | `VCRUNTIME140*.dll`, `ucrtbase.dll` and the `api-ms-win-*` forwarders. |

## Used but not bundled

| Software | License | Notes |
|---|---|---|
| [Blender](https://www.blender.org) | GNU GPL, version 3 or later | Must be installed separately. 3DConvert starts it in the background. |
| NumPy | BSD-3-Clause | Provided by the Python of Blender; the bundled libraries rely on it. |
| ODA File Converter | Proprietary freeware, Open Design Alliance | Optional, only for `.dwg`. Installed by the user, not distributed here. |

## Program icons (`assets/icons/`)

The logo tiles on the supported-formats page are made from the [Simple Icons](https://simpleicons.org) glyphs (CC0-1.0) of Blender, AutoCAD, Autodesk, SketchUp, Rhinoceros, glTF and the Khronos Group. The logos are trademarks of their owners and are shown only to identify the programs whose files can be opened; no endorsement is implied. A PNG with the same name in `assets/icons/` replaces an icon.

## Notes

- **LGPL libraries** (Qt/PySide6, IfcOpenShell, Open CASCADE, GEOS). The complete source of 3DConvert and its build script are in this repository, so the exe can be rebuilt with a modified PySide6 (change `build/requirements.txt`). The libraries in `engine/wheels/` are separate files and can be replaced by other builds of the same library.
- **SketchUp.** OpenSKP (MIT) is the license-clean reader and is always bundled. The optional `sketchup` module loads the SketchUp C API libraries of Trimble Inc. They are proprietary, come from Trimble's SketchUp C SDK (which is available on request only, under its own license agreement) and are distributed by the SketchUp Importer add-on. The SDK license text is not public; check it with Trimble before redistributing the files yourself, or leave `sketchup_api-0.27-cp313-none-win_amd64.whl` out of the repository and the exe (it is listed in `.gitignore`). Without it `.skp` files are read by OpenSKP, which is slower on very large models and does not mark single hidden groups or faces.
- **Scripts that run inside Blender** (`engine/in_blender/`) use Blender's Python API, which is why 3DConvert as a whole is distributed under the GPL.
