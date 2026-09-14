# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

root = Path(SPECPATH)
textual_data, textual_binaries, textual_imports = collect_all("textual")

a = Analysis(
    [str(root / "packaging" / "entrypoint.py")],
    pathex=[str(root / "src")],
    binaries=textual_binaries,
    datas=textual_data + collect_data_files("term_station", includes=["*.tcss"]),
    hiddenimports=textual_imports,
    excludes=["pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="term-station",
    console=True,
    strip=False,
    upx=False,
)
# Keep dependencies at a stable path so macOS does not validate newly
# extracted dynamic libraries on every UI start.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="term-station",
)
