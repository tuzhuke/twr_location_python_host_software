# -*- mode: python ; coding: utf-8 -*-

import os

from PyInstaller.utils.hooks import collect_submodules

try:
    from PyInstaller.utils.win32 import winutils
    winutils.set_exe_build_timestamp = lambda *args, **kwargs: None
    winutils.update_exe_pe_checksum = lambda *args, **kwargs: None
except Exception:
    pass


project_root = os.path.abspath(os.path.join(SPECPATH, os.pardir))
app_name = "Landian_UWB_TWR_Host_V1.0"
icon_file = os.path.join(project_root, "uwb_location.ico")

datas = [
    (icon_file, "."),
]

hiddenimports = []
hiddenimports += collect_submodules("serial.tools")


a = Analysis(
    [os.path.join(project_root, "UWB_Location_Tool.pyw")],
    pathex=[project_root],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "pandas",
        "scipy",
        "tkinter",
        "unittest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    exclude_binaries=False,
    name=app_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)
