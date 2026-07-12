# -*- coding: utf-8 -*-
"""
進版工具 —— 手機配件店 POS 系統
======================================================================
一次完成「改版號 + 產出 version_info.txt」。

用法（從專案根目錄執行）：
    python tools/bump_version.py 1.0.4

搭配 PyInstaller：
    pyinstaller --onefile --version-file version_info.txt ...
version_info.txt 就是 exe 右鍵→內容→詳細資料看到的版本資訊。
"""
import re
import sys
from pathlib import Path

# ── 顯示字串 ────────────────────────────────────────────────────
COMPANY     = "POS"
PRODUCT     = "手機配件店 POS"
DESCRIPTION = "手機配件店單機 POS 系統"
COPYRIGHT   = "© 2026 POS"
EXE_NAME    = "POS.exe"

# ── 路徑（錨定 repo 根＝本檔上一層，與當前工作目錄脫鉤）────────
ROOT        = Path(__file__).resolve().parent.parent
VERSION_PY  = ROOT / "lib" / "version.py"
INFO_TXT    = ROOT / "version_info.txt"
README_MD   = ROOT / "README.md"               # 本專案無 README，停用同步

_VER_RE = re.compile(r'__version__\s*=\s*"([^"]*)"')
# README 門面顯示的版號字樣；本專案無 README，停用同步
_README_RES = ()


def read_current() -> str:
    m = _VER_RE.search(VERSION_PY.read_text(encoding="utf-8"))
    if not m:
        sys.exit(f"錯誤：無法於 {VERSION_PY} 讀取版本號碼（__version__）。")
    return m.group(1)


def write_version(new: str) -> None:
    text = VERSION_PY.read_text(encoding="utf-8")
    text = _VER_RE.sub(f'__version__ = "{new}"', text, count=1)
    VERSION_PY.write_text(text, encoding="utf-8")


def update_readme(new: str) -> bool:
    """同步 README 門面顯示的版號。回傳是否有改到。"""
    if not README_MD.exists() or not _README_RES:
        return False
    text = README_MD.read_text(encoding="utf-8")
    orig = text
    for pat in _README_RES:
        # 有第 2 群組（尾綴）就補回去，沒有就只換前綴＋版號
        repl = rf'\g<1>{new}\g<2>' if pat.groups >= 2 else rf'\g<1>{new}'
        text = pat.sub(repl, text)
    if text != orig:
        README_MD.write_text(text, encoding="utf-8")
        return True
    return False


def gen_info(version: str) -> None:
    """產出 PyInstaller --version-file 用的 version_info.txt。"""
    parts = [int(x) for x in version.split(".")]
    while len(parts) < 4:
        parts.append(0)
    vers = tuple(parts[:4])
    INFO_TXT.write_text(f"""# UTF-8
# 由 bump_version.py 自動產生，請勿手改（用本工具進版即可）
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={vers},
    prodvers={vers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040404b0',
        [
          StringStruct('CompanyName', '{COMPANY}'),
          StringStruct('FileDescription', '{DESCRIPTION}'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', '{EXE_NAME}'),
          StringStruct('LegalCopyright', '{COPYRIGHT}'),
          StringStruct('OriginalFilename', '{EXE_NAME}'),
          StringStruct('ProductName', '{PRODUCT}'),
          StringStruct('ProductVersion', '{version}')
        ])
    ]),
    VarFileInfo([VarStruct('Translation', [1028, 1200])])
  ]
)
""", encoding="utf-8")


if __name__ == "__main__":
    current = read_current()
    print(f"目前版本號碼：v{current}")

    if len(sys.argv) < 2:
        sys.exit("請指定欲進版之版本號碼，如：python tools/bump_version.py 1.0.4")
    new = sys.argv[1].lstrip("v")
    if not re.fullmatch(r"\d+(\.\d+){1,3}", new):
        sys.exit(f"版本號碼格式不正確：{new}（如：1.0.4）")

    write_version(new)
    gen_info(new)
    readme_done = update_readme(new)
    suffix = "、README 版號" if readme_done else ""
    print(f"已完成進版：v{current} → v{new}（已更新 version.py 與 version_info.txt{suffix}）")
    if _README_RES and not readme_done:
        print("注意：README 版號未變動（找不到對應字樣或已是新版），請手動確認。")
