# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the desktop backend. Produces a one-folder build
# (`gst-backend/` with the executable + `_internal/`) which starts far faster
# than a one-file build because nothing has to be unpacked on launch.
#
#   cd backend
#   python -m PyInstaller --noconfirm --distpath ../desktop/backend-dist desktop_server.spec
#
# desktop/scripts/build-backend.mjs runs exactly that.

from PyInstaller.utils.hooks import collect_submodules

# uvicorn and anyio pick their event loop / HTTP / backend implementations with
# runtime imports that static analysis cannot see, so pull in every submodule.
hidden_imports = (
    collect_submodules("uvicorn")
    + collect_submodules("anyio")
    + ["app", "app.main", "app.parser", "app.reconcile", "app.limits"]
)

a = Analysis(
    ["desktop_server.py"],
    pathex=["."],
    binaries=[],
    # app.main resolves sample_data relative to its own file, which in the
    # frozen build lives under _internal/ -- so the samples go there too.
    datas=[("sample_data", "sample_data")],
    hiddenimports=hidden_imports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "_tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="gst-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Keep a console-type binary so stdout/stderr exist (Electron reads them
    # for logs). Electron spawns it with windowsHide, so no window flashes.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="gst-backend",
)
