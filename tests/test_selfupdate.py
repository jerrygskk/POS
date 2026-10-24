"""線上更新：版本比較、release 解析、下載核對、換檔、跨行程更新鎖（暫存資料夾模擬，不連網）。"""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from lib import selfupdate

ROOT = Path(__file__).resolve().parent.parent
HEX = "ab" * 32


class _Resp(io.BytesIO):
    """假回應：記錄讀了多少位元組。"""

    def __init__(self, data):
        super().__init__(data)
        self.read_total = 0

    def read(self, n=-1):
        buf = super().read(n)
        self.read_total += len(buf)
        return buf

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _info(data, version="0.1.2"):
    return {"version": version, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
            "url": f"{selfupdate.DOWNLOAD_PREFIX}v{version}/POS.exe"}


def _release(tag="v0.1.2", **asset):
    a = {"name": "POS.exe", "state": "uploaded", "size": 1234, "digest": "sha256:" + HEX,
         "browser_download_url": f"{selfupdate.DOWNLOAD_PREFIX}{tag}/POS.exe"}
    a.update(asset)
    return {"tag_name": tag, "assets": [a]}


def _opener(data):
    return lambda url, headers=None: _Resp(data)


class VersionTest(unittest.TestCase):
    def test_version_compare(self):
        self.assertTrue(selfupdate.is_newer("0.1.10", "0.1.9"))  # 逐段比數字，不是比字串
        self.assertTrue(selfupdate.is_newer("v0.2.0", "0.1.99"))
        self.assertFalse(selfupdate.is_newer("0.1.2", "0.1.2"))
        self.assertFalse(selfupdate.is_newer("0.1.1", "0.1.2"))
        self.assertFalse(selfupdate.is_newer("abc", "0.1.2"))


class ParseReleaseTest(unittest.TestCase):
    def test_parse_release_ok(self):
        info = selfupdate.parse_release(_release(digest="sha256:" + HEX.upper()))
        self.assertEqual(info, {"version": "0.1.2", "sha256": HEX, "size": 1234,
                                "url": "https://github.com/jerrygskk/POS/releases/download/v0.1.2/POS.exe"})

    def test_parse_release_rejects(self):
        no_tag = _release()
        del no_tag["tag_name"]
        cases = {
            "無 tag": no_tag,
            "tag latest": _release(tag="latest"),
            "tag 含底線": _release(tag="v1_0.1.2"),
            "tag 含全形數字": _release(tag="v０.1.2"),
            "沒有 POS.exe": _release(name="POS.zip"),
            "缺 digest": _release(digest=None),
            "digest md5": _release(digest="md5:" + "a" * 32),
            "digest 長度 63": _release(digest="sha256:" + "a" * 63),
            "size 0": _release(size=0),
            "size 超過上限": _release(size=selfupdate.MAX_SIZE + 1),
            "state starter": _release(state="starter"),
            "url 別的 repo": _release(browser_download_url="https://github.com/evil/POS/releases/download/v0.1.2/POS.exe"),
            "url 版號不同": _release(browser_download_url=selfupdate.DOWNLOAD_PREFIX + "v0.1.3/POS.exe"),
        }
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                selfupdate.parse_release(data)


class FetchLatestTest(unittest.TestCase):
    def test_fetch_latest_parses_and_sends_accept(self):
        seen = []

        def opener(url, headers=None):
            seen.append((url, headers))
            return _Resp(json.dumps(_release()).encode())

        self.assertEqual(selfupdate.fetch_latest(opener)["version"], "0.1.2")
        self.assertEqual(seen, [(selfupdate.RELEASE_API, {"Accept": "application/vnd.github+json"})])

    def test_fetch_latest_404_returns_none(self):
        def opener(code):
            def f(url, headers=None):
                raise urllib.error.HTTPError(url, code, "x", {}, None)
            return f

        self.assertIsNone(selfupdate.fetch_latest(opener(404)))
        with self.assertRaises(urllib.error.HTTPError):
            selfupdate.fetch_latest(opener(500))


class DownloadTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dest = os.path.join(tmp.name, "POS.exe.new")

    def test_download_checks_hash_and_size(self):
        data = os.urandom(600_000)
        seen, urls = [], []

        def opener(url, headers=None):
            urls.append((url, headers))
            return _Resp(data)

        selfupdate.download(self.dest, _info(data), opener, progress=lambda g, t: seen.append(g))
        self.assertEqual(Path(self.dest).read_bytes(), data)
        self.assertEqual(seen[-1], len(data))
        self.assertGreater(len(seen), 1)
        self.assertEqual(urls, [(_info(data)["url"], None)])  # 下載不帶 Accept 標頭
        for bad in (dict(_info(data), size=len(data) + 1), dict(_info(data), sha256="0" * 64)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                selfupdate.download(self.dest, bad, _opener(data))
            self.assertFalse(os.path.exists(self.dest))

    def test_download_aborts_when_over_size(self):
        data = os.urandom(selfupdate.CHUNK * 4)
        info = _info(data[:1000])
        resp = _Resp(data)
        with self.assertRaises(ValueError):
            selfupdate.download(self.dest, info, lambda url, headers=None: resp)
        self.assertLessEqual(resp.read_total, info["size"] + selfupdate.CHUNK)
        self.assertFalse(os.path.exists(self.dest))

    def test_download_replaces_stale_new(self):
        Path(self.dest).write_bytes(b"stale content")
        data = b"fresh"
        selfupdate.download(self.dest, _info(data), _opener(data))
        self.assertEqual(Path(self.dest).read_bytes(), data)

    def test_download_cancel_removes_file(self):
        data = os.urandom(600_000)
        with self.assertRaises(InterruptedError):
            selfupdate.download(self.dest, _info(data), _opener(data), cancelled=lambda: True)
        self.assertFalse(os.path.exists(self.dest))

    def test_verify_file_detects_tamper(self):
        data = os.urandom(10_000)
        info = _info(data)
        selfupdate.download(self.dest, info, _opener(data))
        selfupdate.verify_file(self.dest, info)  # 原檔通過
        with open(self.dest, "r+b") as f:
            f.seek(5000)
            b = f.read(1)
            f.seek(5000)
            f.write(bytes([b[0] ^ 1]))
        with self.assertRaises(ValueError):
            selfupdate.verify_file(self.dest, info)
        with open(self.dest, "ab") as f:  # 大小不符
            f.write(b"x")
        with self.assertRaises(ValueError):
            selfupdate.verify_file(self.dest, info)


class SwapTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.exe = os.path.join(tmp.name, "POS.exe")

    def test_swap_and_rollback(self):
        new = selfupdate.new_path(self.exe)
        Path(self.exe).write_bytes(b"old")
        Path(new).write_bytes(b"new")
        selfupdate.swap(self.exe, new)
        self.assertEqual(Path(self.exe).read_bytes(), b"new")
        self.assertEqual(Path(selfupdate.old_path(self.exe)).read_bytes(), b"old")
        self.assertFalse(os.path.exists(new))

        # 第二次改名（.new → exe）失敗：舊檔要改回原名
        Path(new).write_bytes(b"newer")
        real = os.replace
        calls = []

        def flaky(src, dst):
            calls.append((src, dst))
            if len(calls) == 2:
                raise OSError("模擬改名失敗")
            return real(src, dst)

        with mock.patch("lib.selfupdate.os.replace", side_effect=flaky), self.assertRaises(OSError):
            selfupdate.swap(self.exe, new)
        self.assertEqual(Path(self.exe).read_bytes(), b"new")

    def test_swap_restore_failure_chains_both_errors(self):
        new = selfupdate.new_path(self.exe)
        Path(self.exe).write_bytes(b"old")
        Path(new).write_bytes(b"new")
        real = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(1)
            if len(calls) >= 2:
                raise OSError(f"失敗{len(calls)}")
            return real(src, dst)

        with mock.patch("lib.selfupdate.os.replace", side_effect=flaky):
            with self.assertRaises(OSError) as ctx:
                selfupdate.swap(self.exe, new)
        self.assertEqual(str(ctx.exception), "失敗3")
        self.assertEqual(str(ctx.exception.__cause__), "失敗2")

    def test_cleanup_and_can_write(self):
        Path(self.exe).write_bytes(b"exe")
        Path(selfupdate.old_path(self.exe)).write_bytes(b"old")
        Path(selfupdate.new_path(self.exe)).write_bytes(b"new")
        selfupdate.cleanup(self.exe)
        self.assertFalse(os.path.exists(selfupdate.old_path(self.exe)))
        self.assertFalse(os.path.exists(selfupdate.new_path(self.exe)))
        self.assertTrue(os.path.exists(self.exe))
        self.assertTrue(selfupdate.can_write(self.dir))
        self.assertFalse(selfupdate.can_write(os.path.join(self.dir, "missing")))
        self.assertEqual(sorted(os.listdir(self.dir)), ["POS.exe"])  # 探測檔不留下
        self.assertIsNone(selfupdate.frozen_exe())  # 從原始碼執行不做線上更新

    def test_restart_env(self):
        env = {"_MEIPASS2": "x", "_PYI_APPLICATION_HOME_DIR": "y", "KEEP_ME": "1"}
        with mock.patch.dict(os.environ, env), mock.patch("lib.selfupdate.subprocess.Popen") as popen:
            selfupdate.restart(self.exe)
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [self.exe])
        self.assertEqual(kwargs["cwd"], self.dir)
        child = kwargs["env"]
        self.assertNotIn("_MEIPASS2", child)
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", child)
        self.assertEqual(child["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(child["KEEP_ME"], "1")


_HOLDER = """
import sys
sys.path.insert(0, sys.argv[1])
from lib import selfupdate
h = selfupdate.acquire_update_lock(sys.argv[2])
print("locked" if h is not None else "failed", flush=True)
sys.stdin.readline()
"""


class UpdateLockTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.exe = os.path.join(tmp.name, "POS.exe")

    def _holder(self):
        p = subprocess.Popen([sys.executable, "-c", _HOLDER, str(ROOT), self.exe],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        self.assertEqual(p.stdout.readline().strip(), "locked")
        return p

    def _assert_free_then_release(self):
        h = selfupdate.acquire_update_lock(self.exe)
        self.assertIsNotNone(h)
        selfupdate.release_update_lock(h)

    def test_update_lock_across_processes(self):
        self.assertEqual(selfupdate.lock_path(self.exe), self.exe + ".update.lock")
        # 另一個行程持有 → 拿不到；正常結束後可取得
        p = self._holder()
        self.assertIsNone(selfupdate.acquire_update_lock(self.exe))
        p.communicate("\n", timeout=30)
        self._assert_free_then_release()
        # 持有的行程被強制結束 → Windows 自動釋放
        p = self._holder()
        self.assertIsNone(selfupdate.acquire_update_lock(self.exe))
        p.kill()
        p.communicate(timeout=30)
        self._assert_free_then_release()
        # 同一行程：持有時再取拿不到，release 後可再取得
        h = selfupdate.acquire_update_lock(self.exe)
        self.assertIsNotNone(h)
        self.assertIsNone(selfupdate.acquire_update_lock(self.exe))
        selfupdate.release_update_lock(h)
        self._assert_free_then_release()


if __name__ == "__main__":
    unittest.main()
