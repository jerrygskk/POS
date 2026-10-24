# -*- coding: utf-8 -*-
"""
發版上傳工具 —— 把 dist/POS.exe 發布成 GitHub Release，供線上更新下載
======================================================================
用法（專案根目錄，打包完成、tag 已推上去之後）：
    python tools/publish_release.py

各關卡防的是什麼：
1. exe 版本＝原始碼版號：進了版號卻沒重新打包就上傳，使用者裝到的仍是舊版，
   每次開啟都再問一次更新、永遠更新不成功。
2. 工作區乾淨且 HEAD 就是該版 tag：避免發出去的 exe 和 tag 指的程式碼對不上。
3. 遠端已有同一個 tag：`--verify-tag` 需要；也防本機 tag 被移動過卻沒重推。
4. 已公開就停、殘留草稿就刪掉重建：repo 開了不可變更 release，公開後檔案改不回來。
5. release note 必須存在（根目錄 release_note_v{版號}.md，不入庫）。
6～8. 先建草稿、核對草稿上的檔名／大小／SHA-256 都對，才公開；不對就保留草稿停下。
9. 公開後再從 releases/latest 核對一次（此時已改不回來，只能提示改發下一版）。

外部指令一律用參數陣列（不經 shell 字串）。`gh release` 帶 -R；`gh api` 不支援 -R，
一律寫完整路徑。檔案傳絕對路徑，且不得含 #（gh 會把 # 之後當成顯示標籤）。
"""
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lib.version import __version__  # noqa: E402

REPO = "jerrygskk/POS"
ASSET = "POS.exe"
EXE = os.path.join(ROOT, "dist", ASSET)


def run(args):
    """唯一呼叫外部指令的地方。"""
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8")


def exe_version(path):
    """exe 檔案資訊裡的產品版本（version_info.txt 打包進去的）；讀不到回傳空字串。"""
    # 路徑當參數傳給 script block，不拼進指令字串
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "& { (Get-Item -LiteralPath $args[0]).VersionInfo.ProductVersion }", path],
                       capture_output=True, text=True)
    return r.stdout.strip()


def same_version(exe_ver, version):
    """exe 的版本與原始碼版號是否相同；exe 可能寫成四段（0.1.2.0），多出來的段須為 0。"""
    if not exe_ver:
        return False
    a, b = exe_ver.split("."), version.split(".")
    return a[:len(b)] == b and all(x == "0" for x in a[len(b):])


def find_release(releases, tag):
    return next((r for r in releases if r.get("tag_name") == tag), None)


def release_problems(data, version, sha256, size):
    """逐項比對 release 與本機 exe；回傳問題清單，空清單＝通過。"""
    tag = f"v{version}"
    if data.get("tag_name") != tag:
        return [f"tag 為「{data.get('tag_name')}」，應為 {tag}"]
    asset = next((a for a in data.get("assets") or [] if a.get("name") == ASSET), None)
    if asset is None:
        return [f"找不到 {ASSET}"]
    problems = []
    if asset.get("state") != "uploaded":
        problems.append(f"檔案狀態為「{asset.get('state')}」，尚未上傳完成")
    if asset.get("size") != size:
        problems.append(f"大小為 {asset.get('size')}，本機為 {size}")
    if asset.get("digest") != f"sha256:{sha256}":
        problems.append(f"指紋為「{asset.get('digest')}」，本機為 sha256:{sha256}")
    return problems


def _ok(runner, args, message):
    result = runner(args)
    if result.returncode != 0:
        sys.exit(f"已停止：{message}\n{(result.stderr or result.stdout or '').strip()}")
    return result.stdout.strip()


def _abs_path(path):
    path = os.path.abspath(path)
    if "#" in path:
        sys.exit(f"已停止：路徑含 #，gh 會讀錯檔案：{path}")
    return path


def main(runner=run, exe=EXE, root=ROOT, version=__version__):
    tag = f"v{version}"
    exe = _abs_path(exe)
    note = _abs_path(os.path.join(root, f"release_note_{tag}.md"))

    built = exe_version(exe)
    if not same_version(built, version):
        sys.exit(f"已停止：dist 的 exe 版本為「{built or '讀不到'}」，原始碼版號為 {version}。請重新打包後再上傳。")

    if _ok(runner, ["git", "status", "--porcelain"], "無法讀取 git 狀態"):
        sys.exit("已停止：工作區有未提交的改動。")
    head = _ok(runner, ["git", "rev-parse", "HEAD"], "無法讀取 HEAD")
    tagged = _ok(runner, ["git", "rev-parse", f"{tag}^{{commit}}"], f"本機沒有 {tag} 標籤")
    if head != tagged:
        sys.exit(f"已停止：目前 HEAD 不是 {tag} 標籤所在的提交。")

    remote = _ok(runner, ["git", "ls-remote", "origin", f"refs/tags/{tag}"], "無法查詢遠端標籤").split()
    local = _ok(runner, ["git", "rev-parse", tag], f"本機沒有 {tag} 標籤")
    if not remote or remote[0] != local:
        sys.exit(f"已停止：{tag} 標籤尚未推上遠端，或與本機不同。")

    view = runner(["gh", "release", "view", tag, "-R", REPO, "--json", "isDraft"])
    if view.returncode == 0:
        if not json.loads(view.stdout).get("isDraft"):
            sys.exit(f"已停止：{tag} 已公開，不能重新上傳；請改發下一版。")
        _ok(runner, ["gh", "release", "delete", tag, "-R", REPO, "--yes"], "無法刪除舊草稿")

    if not os.path.isfile(note):
        sys.exit(f"已停止：找不到 release note：{note}")

    with open(exe, "rb") as f:
        data = f.read()
    sha256, size = hashlib.sha256(data).hexdigest(), len(data)

    _ok(runner, ["gh", "release", "create", tag, exe, "-R", REPO, "--draft", "--verify-tag",
                 "--title", tag, "--notes-file", note], "建立草稿失敗")

    releases = json.loads(_ok(runner, ["gh", "api", f"repos/{REPO}/releases"], "無法讀取草稿"))
    draft = find_release(releases, tag)
    problems = ["找不到剛建立的草稿"] if draft is None else release_problems(draft, version, sha256, size)
    if problems:
        sys.exit("已停止，草稿保留未公開：\n" + "\n".join(problems))

    _ok(runner, ["gh", "release", "edit", tag, "-R", REPO, "--draft=false", "--latest"], "公開失敗")

    latest = json.loads(_ok(runner, ["gh", "api", f"repos/{REPO}/releases/latest"], "無法讀取已公開版本"))
    problems = release_problems(latest, version, sha256, size)
    if problems:
        sys.exit(f"⚠️ {tag} 已公開但核對不符，無法修改，請改發下一版：\n" + "\n".join(problems))
    print(f"已發布 {tag}（{size:,} bytes）")


if __name__ == "__main__":
    main()
