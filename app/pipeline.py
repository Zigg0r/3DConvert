"""Runs a conversion: source file -> .blend, using Blender in the background (no window).

Two routes:
  - Blender's own importers (OBJ, FBX, glTF, STL, PLY, USD, Alembic, .blend, 3DS, MAX): the file's own objects and
    materials are kept. STEP and IGES are first tessellated by OpenCASCADE (cascadio) into a temporary glTF.
  - Readers (IFC, Rhino 3DM, COLLADA, DXF, DWG, SketchUp, VRML): a script from engine/readers, run in Blender's
    bundled Python, turns the file into triangles; Blender then builds one mesh per material at the original
    coordinates, with simple materials and no modifiers (engine/in_blender/build_blend.py).
Both routes finish with the mesh cleaning pass (engine/in_blender/cleanup.py)."""
import glob
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import zipfile

from blender_find import blender_version, find_all

NATIVE = {".blend", ".obj", ".stl", ".ply", ".fbx", ".gltf", ".glb", ".usd", ".usda", ".usdc", ".usdz", ".abc", ".3ds",
          ".max"}
IGES = {".igs", ".iges"}
VIA_GLB = {".stp", ".step"} | IGES
# format -> (reader script in engine/readers, step name). IFC has several scripts and DWG needs a DXF first.
READERS = {".dae": ("read_dae.py", "Reading COLLADA"), ".3dm": ("read_3dm.py", "Reading Rhino file"),
           ".skp": ("read_skp.py", "Reading SketchUp"), ".wrl": ("read_wrl.py", "Reading VRML"),
           ".dxf": ("read_dxf.py", "Reading DXF"), ".dwg": ("read_dxf.py", "Reading DXF"),
           ".3mf": ("read_3mf.py", "Reading 3MF"), ".amf": ("read_amf.py", "Reading AMF"),
           ".x3d": ("read_x3d.py", "Reading X3D"), ".x3dv": ("read_wrl.py", "Reading X3D"),
           ".tif": ("read_raster.py", "Reading terrain"), ".tiff": ("read_raster.py", "Reading terrain"),
           ".asc": ("read_raster.py", "Reading ASC file"),
           ".las": ("read_points.py", "Reading point cloud"), ".laz": ("read_points.py", "Reading point cloud"),
           ".e57": ("read_points.py", "Reading point cloud"), ".pts": ("read_points.py", "Reading point cloud"),
           ".xyz": ("read_points.py", "Reading point cloud"), ".pcd": ("read_points.py", "Reading point cloud"),
           ".geojson": ("read_vector.py", "Reading GIS data"), ".json": ("read_vector.py", "Reading GIS data"),
           ".shp": ("read_vector.py", "Reading GIS data"), ".kml": ("read_vector.py", "Reading GIS data"),
           ".kmz": ("read_vector.py", "Reading GIS data"), ".gpx": ("read_vector.py", "Reading GIS data"),
           ".osm": ("read_vector.py", "Reading GIS data"), ".gpkg": ("read_vector.py", "Reading GIS data"),
           ".ifc": (None, "Reading IFC"), ".ifczip": (None, "Reading IFC"), ".ifcxml": (None, "Reading IFC")}
ASSIMP_FORMATS = (".x", ".off", ".lwo", ".lws", ".md2", ".md3", ".md5mesh", ".ms3d", ".smd", ".ac", ".ase", ".cob")
READERS.update({ext: ("read_assimp.py", "Reading with ASSIMP") for ext in ASSIMP_FORMATS})
IFC_KINDS = {".ifc", ".ifczip", ".ifcxml"}
ALIASES = {".vrm": ".glb"}  # a VRM avatar is a glTF binary with another name
GZIPPED = {".x3dz": ".x3d", ".wrz": ".wrl"}  # gzip-compressed X3D and VRML
UNITY_MODELS = {".fbx", ".obj", ".dae", ".glb", ".gltf", ".stl", ".ply", ".3ds"}  # model types read from a .unitypackage
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MESH_SHARE_LIMIT = 0.5  # an IFC that is more than half tessellated geometry is read as a mesh, else as solids
REQUIRED_BLENDER = (5, 2)  # the bundled libraries and scripts are tested with this Blender or newer


class ConvertError(Exception):
    pass


_log_lock = threading.Lock()
_keep_failed_log = False  # the log of a failed file stays when other files finish after it


def reset_log():
    global _keep_failed_log
    _keep_failed_log = False


def engine_dir():
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "engine")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine")


def app_data_dir():
    path = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "3DConvert")
    os.makedirs(path, exist_ok=True)
    return path


def _settings_file():
    return os.path.join(app_data_dir(), "settings.json")


def _saved_blender():
    try:
        with open(_settings_file(), encoding="utf-8") as f:
            return json.load(f).get("blender") or ""
    except (OSError, ValueError, AttributeError):
        return ""


def set_blender_path(path):
    """Remembers a blender.exe picked by the user (blender-launcher.exe is mapped to its blender.exe).
    Returns False when the file is not a Blender executable."""
    if os.path.basename(path).lower() == "blender-launcher.exe":
        path = os.path.join(os.path.dirname(path), "blender.exe")
    if os.path.basename(path).lower() != "blender.exe" or not os.path.isfile(path):
        return False
    with open(_settings_file(), "w", encoding="utf-8") as f:
        json.dump({"blender": os.path.normpath(path)}, f)
    return True


_installs = None  # blender.exe files found on this PC; the search touches the registry and several drives


def blender_installs(refresh=False):
    global _installs
    if _installs is None or refresh:
        _installs = find_all()
    return _installs


def find_blender():
    """Blender to use: the BLENDER environment variable or the path the user picked, when new enough; else the newest
    Blender found anywhere on this PC. Returns (exe, major version)."""
    pinned = [p for p in (os.environ.get("BLENDER"), _saved_blender()) if p and os.path.isfile(p)]
    for exe in pinned:
        if blender_version(exe) >= REQUIRED_BLENDER:
            return exe, blender_version(exe)[0]
    pool = pinned + blender_installs()
    if not pool:
        raise ConvertError("Blender was not found. Install Blender (5.x recommended) or select its blender.exe.")
    exe = max(pool, key=blender_version)
    return exe, blender_version(exe)[0]


def blender_status():
    """(message, ok) for the window: the Blender that would be used and whether it is new enough."""
    need = "%d.%d" % REQUIRED_BLENDER
    try:
        exe, _ = find_blender()
    except ConvertError:
        return f"Blender {need} not found. Click here to select blender.exe", False
    ver = blender_version(exe)
    if ver == (0, 0):
        return f"Blender version unknown. Click here to select blender.exe ({need}+)", False
    if ver < REQUIRED_BLENDER:
        return "Blender %d.%d found, %s needed. Click here to select blender.exe" % (*ver, need), False
    return "Blender %d.%d found, insert file(s) to start" % ver, True


def blender_python(exe):
    found = glob.glob(os.path.join(os.path.dirname(exe), "*", "python", "bin", "python.exe"))
    if not found:
        raise ConvertError("Could not find the Python that ships with Blender.")
    return sorted(found)[-1]


def find_oda_converter():
    """ODA File Converter (free download from opendesign.com), needed to read DWG; None when not installed.
    Looked for in: the ODA_FILE_CONVERTER variable, Program Files\\ODA, a portable copy in %LOCALAPPDATA%\\3DConvert\\ODA
    (the unpacked MSI) and %LOCALAPPDATA%\\Programs\\ODA."""
    env = os.environ.get("ODA_FILE_CONVERTER")
    if env and os.path.isfile(env):
        return env
    found = []
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", ""),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if root:
            found += glob.glob(os.path.join(root, "ODA", "ODAFileConverter*", "ODAFileConverter.exe"))
    portable = os.path.join(app_data_dir(), "ODA")
    found += glob.glob(os.path.join(portable, "ODAFileConverter.exe")) + \
        glob.glob(os.path.join(portable, "*", "ODAFileConverter.exe"))
    return sorted(found)[-1] if found else None


def find_libredwg():
    """The bundled LibreDWG converter (engine/tools/libredwg/dwg2dxf.exe), or None."""
    path = os.path.join(engine_dir(), "tools", "libredwg", "dwg2dxf.exe")
    return path if os.path.isfile(path) else None


# DXF version written by ODA: R2000 keeps ACIS solids as text, which ezdxf reads completely; R2013 and newer store them
# as binary SAB, of which ezdxf only reads part (a 3-solid test file gave 1 solid instead of 3).
ODA_SOLIDS_VERSION = "ACAD2000"
ODA_FULL_VERSION = "ACAD2018"  # every entity type is kept (MESH needs R2010+)
_SOLID_ENTITY = re.compile(rb"\n(?:3DSOLID|BODY|REGION|\w*SURFACE)\r?\n")
_MESH_ENTITY = re.compile(rb"\nMESH\r?\n")


def dxf_entity_kinds(path):
    """(has ACIS solids, has MESH entities) of an ASCII DXF; both False for anything else (binary DXF, no such entity)."""
    import mmap
    try:
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
            return bool(_SOLID_ENTITY.search(m)), bool(_MESH_ENTITY.search(m))
    except (OSError, ValueError):  # unreadable or empty
        return False, False


def dxf_version(path):
    """The $ACADVER code of an ASCII DXF (AC1027 = 2013, AC1032 = 2018), or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(1 << 16)
    except OSError:
        return None
    m = re.search(rb"\$ACADVER\s+1\s+(AC\d+)", head)
    return m.group(1).decode() if m else None


def ensure_libs():
    """Unpacks the bundled wheels (IfcOpenShell, rhino3dm, ...) once; compiled modules cannot load from a zip."""
    wheels = sorted(glob.glob(os.path.join(engine_dir(), "wheels", "*.whl")))
    libs = os.path.join(app_data_dir(), "libs")
    stamp = os.path.join(libs, ".stamp")
    signature = "|".join(f"{os.path.basename(w)}:{os.path.getsize(w)}" for w in wheels)
    if os.path.isfile(stamp):
        with open(stamp, encoding="utf-8") as f:
            if f.read() == signature:
                return libs
    shutil.rmtree(libs, ignore_errors=True)
    os.makedirs(libs)
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as z:
            z.extractall(libs)
    with open(stamp, "w", encoding="utf-8") as f:
        f.write(signature)
    return libs


def clean_env(extra=None):
    env = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "_MEIPASS2", "PYTHONSTARTUP"):
        env.pop(key, None)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                       if os.path.normcase(p).rstrip("\\") != os.path.normcase(meipass).rstrip("\\"))
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    # An empty extensions dir keeps --factory-startup from cleaning up other extensions' packages.
    env["BLENDER_USER_EXTENSIONS"] = os.path.join(tempfile.gettempdir(), "3dconvert_blender_empty")
    env.update(extra or {})
    return env


def cad_name(ext):
    return "IGES" if ext in IGES else "STEP"


def steps_for(ext, combined=False):
    """Step names shown in the UI, in the order Converter runs them; the last one is always 'Done'. In combined mode
    the per-file steps are followed by 'Combining files'."""
    if ext in ALIASES:
        return steps_for(ALIASES[ext], combined)
    if ext in GZIPPED:
        return ["Unpacking file"] + steps_for(GZIPPED[ext], combined)
    if ext == ".unitypackage":
        return ["Unpacking package", "Converting", "Cleaning meshes"] + (["Combining files"] if combined else []) + ["Done"]
    if ext in NATIVE:
        steps = ["Converting"]
    elif ext in VIA_GLB:
        steps = ["Tessellating " + cad_name(ext), "Converting"]
    else:
        steps = {".dwg": ["Converting DWG"], ".ifc": ["Measuring IFC"], ".ifczip": ["Unpacking IFC", "Measuring IFC"],
                 ".ifcxml": ["Converting IFC XML", "Measuring IFC"]}.get(ext, [])
        steps += [READERS[ext][1], "Building .blend"] if ext in READERS else ["Building .blend"]
    steps.append("Cleaning meshes")
    if combined:
        steps.append("Combining files")
    return steps + ["Done"]


class Converter:
    def __init__(self, progress=None, step=None, cleanup_off=()):
        self._progress = progress or (lambda pct, text: None)
        self._on_step = step or (lambda name: None)
        self._env_extra = {"CLEANUP_OFF": ",".join(cleanup_off)}
        self._proc = None
        self._cancelled = False
        self._log = []
        self._base, self._span = 0.0, 1.0  # this file's share of the overall progress bar (percent, fraction)

    def set_slot(self, index, count, reserve=0.0):
        """Progress of the next conversion fills file `index` of `count`; `reserve` (0-1) is kept free at the end."""
        self._base = 100.0 * (1 - reserve) * index / count
        self._span = (1 - reserve) / count

    @property
    def cancelled(self):
        return self._cancelled

    def cancel(self):
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self._proc.pid)], capture_output=True,
                           creationflags=NO_WINDOW)

    def _report(self, pct, text):
        self._progress(int(self._base + pct * self._span), text)

    def _run(self, cmd, env, lo, hi, text):
        """Runs one step; 'PROGRESS <0-100> <text>' lines move the bar between lo and hi, 'STEP <name>' lines
        switch the active step in the UI."""
        if self._cancelled:
            raise ConvertError("Cancelled")
        self._report(lo, text)
        self._on_step(text)
        env = {**env, **self._env_extra}
        self._log.append("== " + text)
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                      encoding="utf-8", errors="replace", env=env, creationflags=NO_WINDOW)
        tail = []
        for line in self._proc.stdout:
            line = line.rstrip()
            self._log.append(line)
            progress = re.match(r"PROGRESS (\d+) ?(.*)", line)
            if line.startswith("STEP "):
                self._on_step(line[5:].strip())
            elif progress:
                self._report(lo + (hi - lo) * int(progress.group(1)) / 100, progress.group(2) or text)
            elif line.strip():
                tail = (tail + [line])[-12:]
        code = self._proc.wait()
        if self._cancelled:
            raise ConvertError("Cancelled")
        if code:
            raise ConvertError(f"{text} failed.\n" + "\n".join(tail[-6:]))
        self._report(hi, text)

    def convert(self, src, target, final=True):
        """Writes the .blend to `target` (a full path). `final=False` skips the 'Done' report (combined mode)."""
        ext = os.path.splitext(src)[1].lower()
        src, target = os.path.abspath(src), os.path.abspath(target)
        blender, major = find_blender()
        work = tempfile.mkdtemp(prefix="3dconvert_")
        engine = engine_dir()
        out = os.path.join(work, "model.blend")
        try:
            if ext == ".unitypackage":
                models = self._unity_models(src, work)
                if len(models) > 1:  # one collection per model
                    self._log.append("Unity package with %d models: converted as separate collections" % len(models))
                    self.convert_combined(models, target)
                    return
                src, ext = models[0], os.path.splitext(models[0])[1].lower()
            elif ext in ALIASES or ext in GZIPPED:
                src, ext = self._unwrap(src, ext, work)
            if ext in NATIVE:
                self._native(blender, engine, src, out)
            elif ext in VIA_GLB or ext in READERS:
                if major < 5:
                    raise ConvertError(f"{ext} files need Blender 5.x (found Blender {major}). "
                                       "Install Blender 5 or set the BLENDER environment variable.")
                if ext in VIA_GLB:
                    self._step(blender, engine, src, work, out, ext)
                else:
                    self._read(blender, engine, src, work, out, ext)
            else:
                raise ConvertError(f"Unsupported format: {ext}")
            shutil.move(out, target)
        except Exception:
            self._write_log(failed=True)
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
        self._write_log()
        if final:
            self._report(100, "Done")
            self._on_step("Done")

    def convert_combined(self, sources, target, on_file=None):
        """Converts every source into a temporary .blend, then combines them into `target`: one collection per file,
        with the file's '!HIDDEN' collection inside it. A file that fails is skipped; returns [(file name, message)]."""
        find_blender()  # fails early when Blender is missing
        work = tempfile.mkdtemp(prefix="3dconvert_")
        parts, failures = [], []
        self._env_extra["COMBINE_MARK"] = "1"  # cleanup.py then remembers which objects are hidden
        try:
            for i, src in enumerate(sources):
                if on_file:
                    on_file(i, len(sources), src)
                self.set_slot(i, len(sources), reserve=0.1)
                part = os.path.join(work, f"part{i}.blend")
                try:
                    self.convert(src, part, final=False)
                    base = os.path.basename(src)
                    stem = os.path.splitext(base)[0]
                    clash = sum(os.path.splitext(os.path.basename(s))[0] == stem for s in sources) > 1
                    parts.append({"blend": part, "name": base if clash else stem})  # keep names unique and readable
                except ConvertError as ex:
                    if self._cancelled:
                        raise
                    failures.append((os.path.basename(src), str(ex)))
            if not parts:
                raise ConvertError("No file could be converted.\n" + "\n".join(f"{n}: {m}" for n, m in failures))
            self.combine(parts, target, work)
        except Exception:
            self._write_log(failed=True)
            raise
        finally:
            self._env_extra.pop("COMBINE_MARK", None)
            shutil.rmtree(work, ignore_errors=True)
        return failures

    def combine(self, parts, target, work):
        """Blender step of a combined run: merges the converted part files ([{'blend', 'name'}]) into `target`."""
        manifest, out = os.path.join(work, "combine.json"), os.path.join(work, "combined.blend")
        with open(manifest, "w", encoding="utf-8") as f:
            json.dump(parts, f)
        self._base, self._span = 90.0, 0.1
        self._run(self._blender_cmd(find_blender()[0], engine_dir(), "combine_blends.py", manifest, out), clean_env(), 0,
                  99, "Combining files")
        shutil.move(out, os.path.abspath(target))
        self._write_log()
        self._base, self._span = 0.0, 1.0
        self._report(100, "Done")
        self._on_step("Done")

    def _write_log(self, failed=False):
        global _keep_failed_log
        with _log_lock:
            if _keep_failed_log and not failed:
                return
            _keep_failed_log = _keep_failed_log or failed
            try:
                with open(os.path.join(app_data_dir(), "last_convert.log"), "w", encoding="utf-8") as f:
                    f.write("\n".join(self._log))
            except OSError:
                pass

    @staticmethod
    def _blender_cmd(blender, engine, script, *args):
        return [blender, "-b", "--factory-startup", "--python-exit-code", "1", "--python",
                os.path.join(engine, "in_blender", script), "--", *args]

    def _native(self, blender, engine, src, out, lo=0, hi=99, extra=()):
        self._run(self._blender_cmd(blender, engine, "convert_native.py", src, out, *extra),
                  clean_env(), lo, hi, "Converting")

    def _step(self, blender, engine, src, work, out, ext):
        env = clean_env({"PYTHONPATH": ensure_libs()})
        glb = os.path.join(work, "step.glb")
        self._run([blender_python(blender), os.path.join(engine, "readers", "step_to_glb.py"), src, glb], env, 0, 30,
                  "Tessellating " + cad_name(ext))
        self._native(blender, engine, glb, out, lo=30, hi=99, extra=("zup",))

    def _oda(self, oda, src, work, version, text, tag):
        """Runs the ODA File Converter on one file; returns the DXF it wrote (the output folder must differ from the
        input folder)."""
        indir, outdir = os.path.join(work, tag + "_in"), os.path.join(work, tag + "_out")
        os.makedirs(indir)
        os.makedirs(outdir)
        shutil.copy(src, indir)
        # arguments: input folder, output folder, version, file type, recurse (0/1), audit (0/1) - audit repairs damage
        self._run([oda, indir, outdir, version, "DXF", "0", "1"], clean_env(), 0, 10, text)
        found = glob.glob(os.path.join(outdir, "*.dxf"))
        if not found:
            raise ConvertError("ODA File Converter did not produce a DXF file.")
        return found[0]

    def _dwg_to_dxf(self, src, work):
        oda = find_oda_converter()
        if not oda:
            return self._libredwg_to_dxf(src, work)
        dxf = self._oda(oda, src, work, ODA_FULL_VERSION, "Converting DWG", "dwg")
        solids, meshes = dxf_entity_kinds(dxf)
        if solids and not meshes:  # R2000 gives ezdxf the solids as text instead of binary SAB
            self._log.append("DWG holds ACIS solids: converted to a DXF R2000 so they can be read completely")
            dxf = self._oda(oda, src, work, ODA_SOLIDS_VERSION, "Converting DWG", "dwg_r2000")
        return dxf

    def _libredwg_to_dxf(self, src, work):
        """No ODA File Converter: the bundled LibreDWG (GPL) reads the DWG. It handles faces, meshes and plain
        geometry; ACIS solids of DWG 2013 and newer are lost."""
        exe = find_libredwg()
        if not exe:
            raise ConvertError("DWG needs the free ODA File Converter (opendesign.com). Install it, "
                               "or save the drawing as DXF from AutoCAD.")
        dxf = os.path.join(work, "libredwg.dxf")
        self._log.append("No ODA File Converter found: reading the DWG with the built-in LibreDWG (limited)")
        try:
            self._run([exe, "-y", "-o", dxf, src], clean_env(), 0, 10, "Converting DWG")
        except ConvertError:
            if self._cancelled or not (os.path.isfile(dxf) and os.path.getsize(dxf)):
                raise ConvertError("The built-in DWG reader (LibreDWG) could not read this file. The free ODA File "
                                   "Converter (opendesign.com) reads more DWG files; or save the drawing as DXF.") from None
        return dxf

    def _dxf_source(self, src, work):
        """A DXF saved as AutoCAD 2013 or newer keeps its solids as binary ACIS that ezdxf reads only in part; with the
        ODA File Converter installed such a file is rewritten as R2000. Everything else is read as it is."""
        version = dxf_version(src)
        if not version or version < "AC1027":
            return src
        solids, meshes = dxf_entity_kinds(src)
        oda = find_oda_converter()
        if not (solids and not meshes and oda):
            return src
        self._log.append("DXF %s holds ACIS solids: rewritten as R2000 with the ODA File Converter" % version)
        return self._oda(oda, src, work, ODA_SOLIDS_VERSION, "Reading DXF", "dxf_r2000")

    def _unzip_ifc(self, src, work):
        """The first .ifc inside an .ifczip, copied to the work folder."""
        self._on_step("Unpacking IFC")
        out = os.path.join(work, "unzipped.ifc")
        try:
            with zipfile.ZipFile(src) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".ifc")]
                if not names:
                    raise ConvertError("No .ifc file found inside this zip.")
                with z.open(names[0]) as a, open(out, "wb") as b:
                    shutil.copyfileobj(a, b, 1 << 20)
        except zipfile.BadZipFile:
            raise ConvertError("This .ifczip file is not a valid zip archive.") from None
        return out

    def _unwrap(self, src, ext, work):
        """VRM -> a glTF binary copy; gzip-compressed X3D/VRML -> plain text. Returns (new path, new extension)."""
        if ext in ALIASES:
            new_ext = ALIASES[ext]
            dest = os.path.join(work, "alias" + new_ext)
            shutil.copyfile(src, dest)
            return dest, new_ext
        self._on_step("Unpacking file")
        new_ext = GZIPPED[ext]
        dest = os.path.join(work, "unzipped" + new_ext)
        try:
            with gzip.open(src, "rb") as a, open(dest, "wb") as b:
                shutil.copyfileobj(a, b, 1 << 20)
        except (OSError, EOFError):
            raise ConvertError("This file is not a valid gzip-compressed X3D/VRML file.") from None
        return dest, new_ext

    def _unity_models(self, src, work):
        """The model files (FBX, OBJ, ...) inside a .unitypackage, extracted to the work folder."""
        self._on_step("Unpacking package")
        found = []
        try:
            with tarfile.open(src, "r:*") as tar:
                members = {m.name: m for m in tar.getmembers() if m.isfile()}
                for name in sorted(members):
                    if not name.endswith("/pathname") or name[:-9] + "/asset" not in members:
                        continue
                    with tar.extractfile(members[name]) as f:
                        path = f.read(2048).decode("utf-8", "replace").splitlines()[0].strip().replace("\\", "/")
                    if os.path.splitext(path)[1].lower() in UNITY_MODELS:
                        found.append((name[:-9] + "/asset", os.path.basename(path)))
                out = []
                for i, (asset, base) in enumerate(found):  # only the file name is used: no path from the package
                    folder = os.path.join(work, "unity%d" % i)
                    os.makedirs(folder)
                    dest = os.path.join(folder, base)
                    with tar.extractfile(members[asset]) as a, open(dest, "wb") as b:
                        shutil.copyfileobj(a, b, 1 << 20)
                    out.append(dest)
        except (tarfile.TarError, OSError, EOFError):
            raise ConvertError("This .unitypackage file could not be opened.") from None
        if not out:
            raise ConvertError("No model (FBX, OBJ, COLLADA, glTF, STL, PLY or 3DS) found in this Unity package.")
        return out

    def _kmz_model(self, src, work):
        """The first COLLADA model inside a .kmz (Google Earth 3D models), or None."""
        try:
            with zipfile.ZipFile(src) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".dae")]
                if not names:
                    return None
                dest = os.path.join(work, "kmz_model.dae")
                with z.open(names[0]) as a, open(dest, "wb") as b:
                    shutil.copyfileobj(a, b, 1 << 20)
                return dest
        except zipfile.BadZipFile:
            return None

    def _read(self, blender, engine, src, work, out, ext):
        """A reader script writes geometry.npz/json into `work`; Blender builds the .blend from it."""
        python = blender_python(blender)
        env = clean_env({"PYTHONPATH": ensure_libs()})

        def run_reader(name, *args, lo, hi, text):
            self._run([python, os.path.join(engine, "readers", name), *args], env, lo, hi, text)

        script, text = READERS[ext]
        if ext in IFC_KINDS:
            if ext == ".ifczip":
                src = self._unzip_ifc(src, work)
            elif ext == ".ifcxml":
                converted = os.path.join(work, "converted.ifc")
                run_reader("ifcxml_to_ifc.py", src, converted, lo=0, hi=5, text="Converting IFC XML")
                src = converted
            measured = os.path.join(work, "measure.json")
            run_reader("ifc_measure.py", src, measured, lo=0, hi=5, text="Measuring IFC")
            with open(measured, encoding="utf-8") as f:
                mesh_share = json.load(f)["mesh_share"]
            if mesh_share > MESH_SHARE_LIMIT:
                run_reader("ifc_meshes.py", src, work, lo=5, hi=40, text=text)
            else:
                reduced = os.path.join(work, "reduced.ifc")
                run_reader("ifc_reduce.py", src, reduced, lo=5, hi=15, text=text)
                run_reader("ifc_solids.py", reduced, work, lo=15, hi=40, text=text)
        elif ext == ".dwg":
            dxf = self._dwg_to_dxf(src, work)
            try:
                run_reader(script, dxf, work, lo=10, hi=40, text=text)
            except ConvertError as ex:
                if os.path.basename(dxf) == "libredwg.dxf" and not self._cancelled:
                    raise ConvertError(str(ex) + "\nThis DWG was read with the built-in LibreDWG, which loses the 3D solids "
                                       "of newer files. The free ODA File Converter (opendesign.com) reads them.") from None
                raise
        elif ext == ".dxf":
            dxf = self._dxf_source(src, work)
            run_reader(script, dxf, work, lo=0 if dxf == src else 10, hi=40, text=text)
        elif ext == ".kmz":
            try:
                run_reader(script, src, work, lo=0, hi=40, text=text)
            except ConvertError:  # no placemarks: a Google Earth model package holds a COLLADA file instead
                model = None if self._cancelled else self._kmz_model(src, work)
                if not model:
                    raise
                self._log.append("No GIS features in the KMZ, reading its COLLADA model")
                run_reader("read_dae.py", model, work, lo=0, hi=40, text=text)
        elif ext == ".skp":
            try:
                run_reader(script, src, work, lo=0, hi=40, text=text)
            except ConvertError as first:  # no SketchUp C API binding bundled, or it cannot open this file
                if self._cancelled:
                    raise
                self._log.append("SketchUp C API reader failed, trying OpenSKP: " + str(first).replace("\n", " | "))
                run_reader("read_skp_open.py", src, work, lo=0, hi=40, text=text)
        else:
            run_reader(script, src, work, lo=0, hi=40, text=text)
        self._run(self._blender_cmd(blender, engine, "build_blend.py", os.path.join(work, "geometry.npz"), out),
                  clean_env(), 40, 99, "Building .blend")


def unique_path(folder, name):
    path = os.path.join(folder, name + ".blend")
    n = 1
    while os.path.exists(path):
        path = os.path.join(folder, f"{name} ({n}).blend")
        n += 1
    return path


_scan_process = None  # the Blender/Python process of a running scan, so closing the app can stop it


def cancel_scan():
    if _scan_process and _scan_process.poll() is None:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(_scan_process.pid)], capture_output=True,
                       creationflags=NO_WINDOW)


def scan_in_blender(src, ext):
    """Detailed scan for 3DM/SketchUp (Blender's Python + bundled libs) and USD/Alembic (Blender importer)."""
    global _scan_process
    blender, major = find_blender()
    engine = engine_dir()
    src = os.path.abspath(src)
    if ext in (".3dm", ".skp"):
        if major < 5:
            raise ConvertError("Detailed scan needs Blender 5.x")
        script = "scan_3dm.py" if ext == ".3dm" else "scan_skp.py"
        cmd = [blender_python(blender), os.path.join(engine, "readers", script), src]
        env = clean_env({"PYTHONPATH": ensure_libs()})
    else:
        cmd = Converter._blender_cmd(blender, engine, "scan_native.py", src)
        env = clean_env()
    _scan_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                     errors="replace", env=env, creationflags=NO_WINDOW)
    try:
        stdout, stderr = _scan_process.communicate(timeout=900)
    except subprocess.TimeoutExpired:
        cancel_scan()
        raise ConvertError("Scan timed out") from None
    for line in stdout.splitlines():
        if line.startswith("SCAN "):
            return json.loads(line[5:])
    detail = (stderr or stdout).strip() or "no output"
    raise ConvertError("Scan failed: " + detail.splitlines()[-1][:90])
