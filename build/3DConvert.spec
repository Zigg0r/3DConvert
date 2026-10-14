# -*- mode: python ; coding: utf-8 -*-
# PyInstaller recipe for 3DConvert.exe. Run through build.bat, which also sets where the exe and work files go.
import os

FILES = os.path.dirname(SPECPATH)  # SPECPATH is the folder of this spec file (FILES/build)
APP = os.path.join(FILES, 'app')
ENGINE = os.path.join(FILES, 'engine')
ASSETS = os.path.join(FILES, 'assets')

# conversion engine (scripts, wheels, bundled add-on), unpacked next to the app at runtime
engine_datas = []
for root, dirs, files in os.walk(ENGINE):
    dirs[:] = [d for d in dirs if d != '__pycache__']
    relative = os.path.relpath(root, ENGINE)
    destination = 'engine' if relative == '.' else os.path.join('engine', relative)
    engine_datas += [(os.path.join(root, f), destination) for f in files if not f.endswith('.pyc')]

# program icons of the supported-formats page (a PNG named after a program replaces its drawn badge)
icon_datas = [(os.path.join(ASSETS, 'icons', f), os.path.join('assets', 'icons'))
              for f in os.listdir(os.path.join(ASSETS, 'icons')) if f.endswith('.png')]

a = Analysis(
    [os.path.join(APP, 'main.py')],
    pathex=[APP],
    binaries=[],
    datas=engine_datas + icon_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6.QtNetwork', 'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtOpenGL', 'PySide6.QtPdf', 'PySide6.QtSvg'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# Qt parts this widgets-only app never loads (software OpenGL, Quick/QML, PDF, network, D2D platform)
UNUSED_QT = ('opengl32sw', 'qt6quick', 'qt6qml', 'qt6pdf', 'qt6network', 'qt6opengl', 'qt6svg', 'qsvg', 'qdirect2d')
a.binaries = [b for b in a.binaries if not any(k in os.path.basename(b[0]).lower() for k in UNUSED_QT)]

splash = Splash(
    os.path.join(ASSETS, 'splash.png'),
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(36, 214),
    text_size=12,
    text_color='#ff9a1f',
    text_font='Consolas',
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    splash,
    splash.binaries,
    [],
    name='3DConvert',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(ASSETS, 'icon.ico')],
)
