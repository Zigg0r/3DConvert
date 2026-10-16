"""Finds every blender.exe on this PC, wherever it was installed.

Sources, from most to least reliable: the .blend file association, the Windows uninstall list, the Blender
registry keys, App Paths, Steam libraries, PATH, and the usual install/download folders of every fixed drive
(which also finds portable zip copies)."""
import ctypes
import itertools
import os
import re
import shutil

try:
    import winreg
except ImportError:  # not Windows
    winreg = None

UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\blender.exe"
VERSION_DIR = re.compile(r"\d+\.\d+")


def blender_version(exe):
    """(major, minor) from the version folder next to blender.exe (e.g. '5.2'), else from the install path."""
    try:
        folders = [tuple(int(n) for n in d.split(".")) for d in os.listdir(os.path.dirname(exe))
                   if VERSION_DIR.fullmatch(d)]
    except OSError:
        folders = []
    if folders:
        return max(folders)
    m = re.search(r"Blender (\d+)\.(\d+)", exe)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _exe_in(folder):
    exe = os.path.join(folder, "blender.exe")
    return exe if os.path.isfile(exe) else None


def _search(folder, depth):
    """blender.exe in `folder` and in nested folders that look like Blender installs, up to `depth` levels."""
    found = []
    exe = _exe_in(folder)
    if exe:
        found.append(exe)
    if depth:
        try:
            names = os.listdir(folder)
        except OSError:
            return found
        for name in names:
            sub = os.path.join(folder, name)
            if (("blender" in name.lower() or VERSION_DIR.fullmatch(name)) and os.path.isdir(sub)):
                found += _search(sub, depth - 1)
    return found


def _command_folder(command):
    """Folder of the program in a registry command line such as '"C:\\x\\blender-launcher.exe" "%1"'."""
    m = re.match(r'\s*"([^"]+)"', command) or re.match(r"\s*(.+?\.(?:exe|ico))", command, re.I)
    return os.path.dirname(m.group(1).split(",")[0]) if m else None


def _value(key, name):
    try:
        value = winreg.QueryValueEx(key, name)[0]
    except OSError:
        return ""
    return value if isinstance(value, str) else ""


def _subkeys(key):
    for i in itertools.count():
        try:
            yield winreg.EnumKey(key, i)
        except OSError:
            return


def _registry_folders():
    folders = []
    if winreg is None:
        return folders
    try:  # the .blend file association points at blender-launcher.exe in the install folder
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, ".blend") as key:
            progid = _value(key, "")
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r"\shell\open\command") as key:
            folders.append(_command_folder(_value(key, "")))
    except OSError:
        pass
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                base = winreg.OpenKey(root, UNINSTALL, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with base:
                for name in _subkeys(base):
                    try:
                        with winreg.OpenKey(base, name) as key:
                            if "blender" not in _value(key, "DisplayName").lower():
                                continue
                            location = _value(key, "InstallLocation").strip('" ')
                            if location:
                                folders.append(location)
                            for field in ("DisplayIcon", "UninstallString"):
                                folders.append(_command_folder(_value(key, field)))
                    except OSError:
                        continue
        try:  # App Paths and the keys the Blender installer writes itself
            with winreg.OpenKey(root, APP_PATHS) as key:
                folders.append(os.path.dirname(_value(key, "")))
        except OSError:
            pass
        for path in (r"SOFTWARE\BlenderFoundation", r"SOFTWARE\WOW6432Node\BlenderFoundation"):
            folders += _blender_foundation_values(root, path, 3)
    return [f for f in folders if f]


def _blender_foundation_values(root, path, depth):
    folders = []
    try:
        with winreg.OpenKey(root, path) as key:
            for i in itertools.count():
                try:
                    _, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                if isinstance(value, str) and os.path.isdir(value.strip('" ')):
                    folders.append(value.strip('" '))
            if depth:
                for name in _subkeys(key):
                    folders += _blender_foundation_values(root, path + "\\" + name, depth - 1)
    except OSError:
        pass
    return folders


def _fixed_drives():
    """Local hard drives only; network and removable drives are left alone (they can hang)."""
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        return [f"{chr(65 + i)}:\\" for i in range(26)
                if mask >> i & 1 and ctypes.windll.kernel32.GetDriveTypeW(f"{chr(65 + i)}:\\") == 3]
    except (AttributeError, OSError):
        return []


def _steam_folders():
    roots = []
    if winreg is not None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam") as key:
                roots.append(_value(key, "SteamPath").replace("/", "\\"))
        except OSError:
            pass
    roots += [os.path.join(os.environ.get(var, ""), "Steam") for var in ("ProgramFiles(x86)", "ProgramFiles")]
    folders = []
    for steam in filter(None, roots):
        folders.append(os.path.join(steam, "steamapps", "common", "Blender"))
        try:
            with open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="ignore") as f:
                for library in re.findall(r'"path"\s+"([^"]+)"', f.read()):
                    folders.append(os.path.join(library.replace("\\\\", "\\"), "steamapps", "common", "Blender"))
        except OSError:
            pass
    return folders


def _common_bases():
    bases = []
    for drive in _fixed_drives():
        bases += [drive] + [os.path.join(drive, n) for n in (
            "Program Files", "Program Files (x86)", "Programs", "Apps", "Software", "Tools", "Applications")]
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA", "")
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        bases.append(os.environ.get(var, ""))
    for user in (home, os.environ.get("OneDrive", ""), os.environ.get("OneDriveConsumer", "")):
        if user:
            bases += [user] + [os.path.join(user, n) for n in ("Desktop", "Downloads", "Documents")]
    if local:
        bases += [local, os.path.join(local, "Programs")]
    return [b for b in bases if b]


def find_all():
    """Every blender.exe found, newest version first."""
    exes = []
    for folder in _registry_folders() + _steam_folders():
        exes += _search(folder, 2)
    on_path = shutil.which("blender")
    if on_path:
        exes.append(on_path)
    for base in _common_bases():
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            if "blender" in name.lower():
                sub = os.path.join(base, name)
                if os.path.isdir(sub):
                    exes += _search(sub, 3)
    unique = {}
    for exe in exes:
        if os.path.basename(exe).lower() == "blender.exe" and os.path.isfile(exe):
            unique.setdefault(os.path.normcase(os.path.realpath(exe)), os.path.normpath(exe))
    return sorted(unique.values(), key=blender_version, reverse=True)
