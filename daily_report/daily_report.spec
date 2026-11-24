# -*- mode: python ; coding: utf-8 -*-
"""日報表打包設定（onefile，入庫）。在 repo 根目錄執行，指令見 DEVELOPER.md §4。"""

import os

_ROOT = os.path.dirname(SPECPATH)
# 同 POS.spec：PATH 上的 Git for Windows 會混進 mingw64 的 OpenSSL DLL
_BAD_SOURCE_DIRS = (r"\program files\git", r"\program files (x86)\git")


def _drop_git_openssl(entries):
    return [e for e in entries if not any(bad in (e[1] or "").lower() for bad in _BAD_SOURCE_DIRS)]


# 只帶日報表畫面用得到的前端檔，不帶整個 POS 前端
_STATIC = [
    ('static/daily_report.html', 'static'),
    ('static/css/pos.css', 'static/css'),
    ('static/css/dialog-theme.css', 'static/css'),
    ('static/js/vue.global.prod.js', 'static/js'),
    ('static/js/confirm.js', 'static/js'),
    ('static/js/daily_report.js', 'static/js'),
]

a = Analysis(
    [os.path.join(SPECPATH, 'main.py')],
    pathex=[_ROOT],
    binaries=[],
    datas=[(os.path.join(_ROOT, src), dst) for src, dst in _STATIC],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'pytest'],
    noarchive=False,
    optimize=0,
)
a.binaries = _drop_git_openssl(a.binaries)
a.datas = _drop_git_openssl(a.datas)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='日報表',
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
)
