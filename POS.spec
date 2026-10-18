# -*- mode: python ; coding: utf-8 -*-
"""打包設定（onefile，入庫）。說明見 DEVELOPER.md §4。"""

# 打包機器 PATH 上若有 Git for Windows，PyInstaller 會撿到 mingw64 的 OpenSSL
# （libcrypto-3-x64.dll／libssl-3-x64.dll）混進來；Python 自己的 libcrypto-3.dll 不受影響。
_BAD_SOURCE_DIRS = (r"\program files\git", r"\program files (x86)\git")


def _drop_git_openssl(entries):
    return [e for e in entries if not any(bad in (e[1] or "").lower() for bad in _BAD_SOURCE_DIRS)]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('static', 'static')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 前端走 pywebview（WebView2），不用 tkinter；測試工具不進正式版
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
    name='POS',
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
    icon='assets/POS.ico',
    version='version_info.txt',
)
