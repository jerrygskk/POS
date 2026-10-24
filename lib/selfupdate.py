"""程式線上更新：查版本、下載核對、換檔（移植自 Lottery539，來源改 GitHub Releases）。

為何放 GitHub Releases：repo 本來就公開，release 檔案由 GitHub 代管、API 直接附 SHA-256 指紋，
不必自建伺服器。信任根是維護者的 GitHub 帳號（無程式碼簽章），帳號須開兩步驟驗證、repo 開
「不可變更 release」，發布後的檔案不能被抽換。

流程：
1. `fetch_latest` 查 releases/latest（只回已公開、非草稿、非 prerelease），`parse_release` 嚴格核對
   tag、`POS.exe` asset、上傳狀態、大小上限、digest 與下載網址；任何一項不符就不更新。
2. 呼叫端以 `acquire_update_lock` 取得跨行程更新鎖，從清理、下載、驗證到換檔全程持有。
3. `download` 下載到 `POS.exe.new`：先刪舊暫存檔再以 "xb" 開檔，超過宣稱大小立即中止，
   完成時核對大小與指紋；中止或不符就刪暫存檔。
4. 換檔前 `verify_file` 從硬碟重讀一次核對（防別的行程或防毒軟體動到暫存檔）。
5. `swap`：執行中的 exe 改名 `.old`（Windows 允許改名執行中的檔案，不能覆寫）→ `.new` 放回原名，
   放不回去就把舊檔改回來；`restart` 啟動新版、舊版結束；下次開啟 `cleanup` 刪 `.old`／`.new`。

憑證交給 Windows 驗證（truststore，原因同 Lottery539 PITFALLS NET-1：新電腦缺根憑證時由
Windows 自動補裝）；不得關閉驗證。
"""
import hashlib
import json
import msvcrt
import os
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

import truststore

RELEASE_API = "https://api.github.com/repos/jerrygskk/POS/releases/latest"
ASSET_NAME = "POS.exe"
DOWNLOAD_PREFIX = "https://github.com/jerrygskk/POS/releases/download/"
TIMEOUT = 20
CHUNK = 256 * 1024
MAX_SIZE = 500 * 1024 * 1024

SSL_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

# 只收 ASCII 數字；不靠 int() 寬鬆解析（int() 會收「1_0」、全形數字）
_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)", re.ASCII)
_TAG_RE = re.compile(r"v\d+\.\d+\.\d+", re.ASCII)
_DIGEST_RE = re.compile(r"sha256:([0-9a-fA-F]{64})", re.ASCII)


def parse_version(text):
    """「0.1.12」或「v0.1.12」→ (0, 1, 12)；格式不對回傳 None。"""
    m = _VERSION_RE.fullmatch(text) if isinstance(text, str) else None
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(latest, current):
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def get_url(url, headers=None):
    """開啟網址；一律帶 User-Agent（GitHub API 要求）。轉址保持 urllib 預設（下載會轉到 githubusercontent）。"""
    h = {"User-Agent": "POS"}
    h.update(headers or {})
    return urllib.request.urlopen(urllib.request.Request(url, headers=h),
                                  timeout=TIMEOUT, context=SSL_CONTEXT)


def parse_release(data):
    """GitHub release JSON → {"version", "sha256", "size", "url"}；任何一項不符丟 ValueError。"""
    if not isinstance(data, dict):
        raise ValueError("版本資訊格式不符：不是物件")
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not _TAG_RE.fullmatch(tag):
        raise ValueError(f"版本標籤格式不符：{tag!r}")
    assets = data.get("assets")
    found = [a for a in assets if isinstance(a, dict) and a.get("name") == ASSET_NAME] \
        if isinstance(assets, list) else []
    if len(found) != 1:
        raise ValueError(f"release {tag} 沒有唯一的 {ASSET_NAME}")
    asset = found[0]
    if asset.get("state") != "uploaded":
        raise ValueError(f"{ASSET_NAME} 尚未上傳完成：{asset.get('state')!r}")
    size = asset.get("size")
    if type(size) is not int or not 0 < size <= MAX_SIZE:
        raise ValueError(f"{ASSET_NAME} 大小不符：{size!r}")
    digest = asset.get("digest")
    m = _DIGEST_RE.fullmatch(digest) if isinstance(digest, str) else None
    if not m:
        raise ValueError(f"{ASSET_NAME} 指紋格式不符：{digest!r}")
    url = asset.get("browser_download_url")
    if url != f"{DOWNLOAD_PREFIX}{tag}/{ASSET_NAME}":
        raise ValueError(f"{ASSET_NAME} 下載網址不符：{url!r}")
    return {"version": tag[1:], "sha256": m.group(1).lower(), "size": size, "url": url}


def fetch_latest(opener=get_url):
    """查最新正式版並核對格式；還沒有任何 release（HTTP 404）回 None，其餘錯誤往上丟。"""
    try:
        with opener(RELEASE_API, headers={"Accept": "application/vnd.github+json"}) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    return parse_release(data)


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def download(dest, info, opener=get_url, progress=None, cancelled=lambda: False):
    """下載 info["url"] 到 dest，核對大小與 SHA-256；不符或中止時刪暫存檔並丟例外。progress(已下載, 總數)。"""
    size = info["size"]
    h = hashlib.sha256()
    got = 0
    _remove(dest)
    created = False
    try:
        with opener(info["url"]) as r, open(dest, "xb") as f:
            created = True
            while True:
                if cancelled():
                    raise InterruptedError("下載已取消")
                buf = r.read(CHUNK)
                if not buf:
                    break
                got += len(buf)
                if got > size:
                    raise ValueError("下載的檔案超過宣稱大小")
                f.write(buf)
                h.update(buf)
                if progress:
                    progress(got, size)
        if got != size or h.hexdigest() != info["sha256"].lower():
            raise ValueError("下載的檔案不完整（大小或指紋不符）")
    except BaseException:
        if created:
            try:
                _remove(dest)
            except OSError:
                pass  # 刪不掉（例如防毒軟體占用）留給下次開啟的 cleanup；保留原本的例外
        raise


def verify_file(path, info):
    """從硬碟重讀 path 核對大小與 SHA-256，不符丟 ValueError。"""
    if os.path.getsize(path) != info["size"]:
        raise ValueError("暫存檔大小與版本資訊不符")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while buf := f.read(CHUNK):
            h.update(buf)
    if h.hexdigest() != info["sha256"].lower():
        raise ValueError("暫存檔指紋與版本資訊不符")


def old_path(exe):
    return exe + ".old"


def new_path(exe):
    return exe + ".new"


def lock_path(exe):
    return exe + ".update.lock"


def swap(exe, new):
    """執行中的 exe 改名為 .old，新檔放回原名；放不回去時把舊檔改回來。
    還原也失敗時丟還原的例外並以 from 串起原本的例外，呼叫端寫 error.log 兩個都看得到。"""
    old = old_path(exe)
    if os.path.exists(old):
        os.remove(old)
    os.replace(exe, old)
    try:
        os.replace(new, exe)
    except OSError as err:
        try:
            os.replace(old, exe)
        except OSError as restore_err:
            raise restore_err from err
        raise


def cleanup(exe):
    """開啟時刪掉上次更新留下的 .old／.new；舊版行程可能還沒完全結束，刪不掉就下次再刪。
    呼叫端須持有更新鎖，免得刪到別的 POS 正在下載的 .new。"""
    for p in (old_path(exe), new_path(exe)):
        try:
            _remove(p)
        except OSError:
            pass


def can_write(folder):
    """exe 所在資料夾能不能寫（放在 Program Files 之類的地方就不能換檔）。探測檔用唯一檔名，兩個 POS 同時探測不互相干擾。"""
    try:
        fd, probe = tempfile.mkstemp(prefix=".update_probe", dir=folder)
        os.close(fd)
        os.remove(probe)
        return True
    except OSError:
        return False


def acquire_update_lock(exe):
    """取得跨行程更新鎖（鎖住鎖定檔第 1 個位元組）；別的行程已持有或開不了鎖定檔回 None。
    行程結束（含被強制結束）時 Windows 自動釋放，不會留下卡死的鎖；鎖定檔本身不刪。"""
    try:
        fd = os.open(lock_path(exe), os.O_RDWR | os.O_CREAT)
    except OSError:
        return None
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        os.close(fd)
        return None
    return fd


def release_update_lock(handle):
    try:
        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(handle)


def frozen_exe():
    """打包後的 exe 路徑；從原始碼執行回傳 None（不做線上更新）。"""
    return sys.executable if getattr(sys, "frozen", False) else None


def restart(exe):
    """啟動新版（脫離目前行程）；呼叫端接著結束程式。
    ⚠️ onefile 會把 _MEIPASS 等環境變數傳給子行程，新版會誤用舊版的暫存解壓目錄，要清掉（PyInstaller 文件）。"""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("_MEI") and k.upper() != "_PYI_APPLICATION_HOME_DIR"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen([exe], cwd=os.path.dirname(exe), env=env, close_fds=True,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
