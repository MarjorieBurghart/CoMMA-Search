# -*- mode: python ; coding: utf-8 -*-
# Construction des exécutables : pyinstaller --noconfirm --clean packaging/comma_search.spec
# Windows et Linux : un seul fichier exécutable ; macOS : une application CoMMA-Search.app.
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(ROOT, "CoMMAsearch.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "donnees"), "donnees")],
    hiddenimports=["certifi"],  # certificats HTTPS (indispensable sous macOS)
    excludes=["matplotlib", "numpy", "pandas", "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython"],
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="CoMMA-Search", console=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="CoMMA-Search")
    app = BUNDLE(coll, name="CoMMA-Search.app", bundle_identifier="io.github.comma-search")
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="CoMMA-Search", console=False, upx=False)
