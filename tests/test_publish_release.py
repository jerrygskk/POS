import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_spec = importlib.util.spec_from_file_location(
    "publish_release", Path(__file__).resolve().parent.parent / "tools" / "publish_release.py")
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)

VERSION = "0.1.2"
TAG = "v0.1.2"
EXE_BYTES = b"fake exe"
SHA = hashlib.sha256(EXE_BYTES).hexdigest()


def release(**override):
    asset = {"name": "POS.exe", "state": "uploaded", "size": len(EXE_BYTES), "digest": f"sha256:{SHA}"}
    asset.update(override.pop("asset", {}))
    data = {"tag_name": TAG, "assets": [asset]}
    data.update(override)
    return data


def done(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")


class FakeRunner:
    """依指令內容回傳腳本化結果，並記錄呼叫順序。"""

    def __init__(self, **override):
        self.calls = []
        self.answers = {
            "status": done(""), "head": done("abc"), "tag_commit": done("abc"),
            "ls_remote": done(f"tagsha\trefs/tags/{TAG}"), "tag": done("tagsha"),
            "view": done("", 1), "delete": done(), "create": done(),
            "releases": done(json.dumps([release(), release(tag_name="v0.1.1")])),
            "edit": done(), "latest": done(json.dumps(release())),
        }
        self.answers.update(override)

    def key(self, args):
        if args[:2] == ["git", "status"]: return "status"
        if args[:2] == ["git", "rev-parse"]:
            return {"HEAD": "head", f"{TAG}^{{commit}}": "tag_commit"}.get(args[2], "tag")
        if args[:2] == ["git", "ls-remote"]: return "ls_remote"
        if args[:2] == ["gh", "release"]: return args[2]
        if args[:2] == ["gh", "api"]: return "latest" if args[2].endswith("/latest") else "releases"
        raise AssertionError(args)

    def __call__(self, args):
        self.calls.append(args)
        return self.answers[self.key(args)]

    def names(self):
        return [self.key(c) for c in self.calls]


class PureFunctionTests(unittest.TestCase):
    def test_same_version(self):
        self.assertTrue(pr.same_version("0.1.2.0", "0.1.2"))
        self.assertTrue(pr.same_version("0.1.2", "0.1.2"))
        self.assertFalse(pr.same_version("0.1.2.1", "0.1.2"))
        self.assertFalse(pr.same_version("", "0.1.2"))
        self.assertFalse(pr.same_version("0.1.3", "0.1.2"))

    def test_release_problems(self):
        self.assertEqual(pr.release_problems(release(), VERSION, SHA, len(EXE_BYTES)), [])
        for bad in (release(tag_name="v0.1.1"), release(assets=[]),
                    release(asset={"state": "starter"}), release(asset={"size": 1}),
                    release(asset={"digest": "sha256:" + "0" * 64})):
            self.assertEqual(len(pr.release_problems(bad, VERSION, SHA, len(EXE_BYTES))), 1, bad)

    def test_find_release(self):
        items = [release(tag_name="v0.1.1"), release()]
        self.assertIs(pr.find_release(items, TAG), items[1])
        self.assertIsNone(pr.find_release(items, "v9.9.9"))


class MainFlowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.exe = self.root / "dist" / "POS.exe"
        self.exe.parent.mkdir()
        self.exe.write_bytes(EXE_BYTES)
        (self.root / f"release_note_{TAG}.md").write_text("note", encoding="utf-8")
        patcher = patch.object(pr, "exe_version", return_value="0.1.2.0")
        self.exe_version = patcher.start()
        self.addCleanup(patcher.stop)

    def main(self, runner):
        pr.main(runner=runner, exe=str(self.exe), root=str(self.root), version=VERSION)

    def test_main_happy_path(self):
        runner = FakeRunner()
        self.main(runner)
        self.assertEqual(runner.names(), ["status", "head", "tag_commit", "ls_remote", "tag",
                                          "view", "create", "releases", "edit", "latest"])
        for args in runner.calls:
            if args[:2] == ["gh", "release"]:
                self.assertIn("-R", args)
                self.assertEqual(args[args.index("-R") + 1], "jerrygskk/POS")
            if args[:2] == ["gh", "api"]:
                self.assertNotIn("-R", args)
                self.assertTrue(args[2].startswith("repos/jerrygskk/POS/"))
        create = runner.calls[runner.names().index("create")]
        self.assertTrue(os.path.isabs(create[4]))
        self.assertTrue(os.path.isabs(create[create.index("--notes-file") + 1]))

    def test_main_draft_mismatch_does_not_publish(self):
        bad = json.dumps([release(asset={"digest": "sha256:" + "0" * 64})])
        runner = FakeRunner(releases=done(bad))
        with self.assertRaises(SystemExit):
            self.main(runner)
        self.assertNotIn("edit", runner.names())

    def test_main_stops_on_each_gate(self):
        cases = {
            "version": ({}, "0.1.1"),
            "dirty": ({"status": done(" M x.py")}, None),
            "head": ({"head": done("other")}, None),
            "remote": ({"ls_remote": done("")}, None),
            "published": ({"view": done(json.dumps({"isDraft": False}))}, None),
        }
        for name, (override, built) in cases.items():
            with self.subTest(name):
                self.exe_version.return_value = built or "0.1.2.0"
                runner = FakeRunner(**override)
                with self.assertRaises(SystemExit):
                    self.main(runner)
                self.assertNotIn("create", runner.names())
        self.exe_version.return_value = "0.1.2.0"
        with self.subTest("note"):
            (self.root / f"release_note_{TAG}.md").unlink()
            runner = FakeRunner()
            with self.assertRaises(SystemExit):
                self.main(runner)
            self.assertNotIn("create", runner.names())
        with self.subTest("hash"):
            runner = FakeRunner()
            with self.assertRaises(SystemExit):
                pr.main(runner=runner, exe=str(self.root / "a#b" / "POS.exe"),
                        root=str(self.root), version=VERSION)
            self.assertNotIn("create", runner.names())

    def test_main_existing_draft_is_replaced(self):
        runner = FakeRunner(view=done(json.dumps({"isDraft": True})))
        self.main(runner)
        delete = runner.calls[runner.names().index("delete")]
        self.assertIn("--yes", delete)
        self.assertNotIn("--cleanup-tag", delete)


if __name__ == "__main__":
    unittest.main()
