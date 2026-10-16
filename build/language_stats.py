"""Counts what the program is made of by language (like GitHub's language bar) into assets/languages.json, which the
information screen of the exe shows. Run by build.bat before PyInstaller (with the build venv's Python).

Unit: bytes of code files that end up in the exe, not lines. Python/C/C++/Batchfile source counts as it is; compiled
libraries (.dll/.pyd) count by the language they were written in, which is fixed per wheel below. Data files (IFC
schemas, json, images) are left out."""
import glob
import json
import os
import sys
import zipfile

FILES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = {".py": "Python", ".pyx": "Python", ".spec": "Python", ".bat": "Batchfile",
          ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".hpp": "C++", ".hh": "C++"}
BINARY = (".dll", ".pyd", ".so")
# language of the compiled files in each wheel: (.dll/.so, .pyd)
COMPILED = {
    "ifcopenshell": ("C++", "C++"),  # IfcOpenShell and Open CASCADE
    "cascadio": ("C++", "C++"),  # Open CASCADE
    "rhino3dm": ("C++", "C++"),  # openNURBS
    "mapbox_earcut": ("C++", "C++"),
    "pye57": ("C++", "C++"),  # libE57Format and Xerces-C
    "assimp_py": ("C++", "C++"),  # ASSIMP
    "lazrs": ("Rust", "Rust"),  # laz-rs
    "shapely": ("C++", "C"),  # GEOS, Cython glue
    "sketchup_api": ("C", "C"),  # SketchUp C API
    "ezdxf": ("C", "C"),  # Cython
    "fonttools": ("C", "C"),  # Cython
}
SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "dist", "wheels"}

totals = {}


def add(language, size):
    totals[language] = totals.get(language, 0) + size


# the program itself and the conversion scripts
for root, dirs, files in os.walk(FILES):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for name in files:
        language = SOURCE.get(os.path.splitext(name)[1].lower())
        if language:
            add(language, os.path.getsize(os.path.join(root, name)))

# bundled libraries
for wheel in glob.glob(os.path.join(FILES, "engine", "wheels", "*.whl")):
    package = os.path.basename(wheel).split("-")[0].lower()
    native, pyd = COMPILED.get(package, ("C++", "C++"))
    with zipfile.ZipFile(wheel) as z:
        for info in z.infolist():
            ext = os.path.splitext(info.filename)[1].lower()
            if ext in SOURCE:
                add(SOURCE[ext], info.file_size)
            elif ext in BINARY:
                add(pyd if ext == ".pyd" else native, info.file_size)

# bundled tools (LibreDWG and libiconv are written in C)
for root, dirs, files in os.walk(os.path.join(FILES, "engine", "tools")):
    for name in files:
        if name.lower().endswith((".dll", ".exe")):
            add("C", os.path.getsize(os.path.join(root, name)))

# what PyInstaller adds from the build environment: the Qt libraries (C++) and the Python runtime (C)
qt_dir = os.path.join(sys.prefix, "Lib", "site-packages", "PySide6")
for name in ("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Svg.dll", "pyside6.abi3.dll",
             "QtCore.pyd", "QtGui.pyd", "QtWidgets.pyd"):
    path = os.path.join(qt_dir, name)
    if os.path.isfile(path):
        add("C++", os.path.getsize(path))
for name in ("python312.dll", "python313.dll"):
    path = os.path.join(sys.base_prefix, name)
    if os.path.isfile(path):
        add("C", os.path.getsize(path))

with open(os.path.join(FILES, "assets", "languages.json"), "w", encoding="utf-8") as f:
    json.dump(dict(sorted(totals.items(), key=lambda kv: -kv[1])), f, indent=1)
print("languages.json written")
