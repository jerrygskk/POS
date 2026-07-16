import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    root_dir: Path
    db_path: Path
    error_log_path: Path
    backup_dir: Path
    static_dir: Path

    @classmethod
    def from_root(cls, root_dir, resource_root=None):
        root = Path(root_dir).resolve()
        resources = Path(resource_root).resolve() if resource_root is not None else root
        return cls(
            root,
            root / "pos.db",
            root / "error.log",
            root / "backups",
            resources / "static",
        )

    @classmethod
    def detect(
        cls,
        module_file=__file__,
        executable=None,
        frozen=None,
        bundle_dir=None,
    ):
        if frozen is None:
            frozen = bool(getattr(sys, "frozen", False))
        if executable is None:
            executable = sys.executable
        root = Path(executable).resolve().parent if frozen else Path(module_file).resolve().parent
        if frozen:
            bundle_dir = bundle_dir or getattr(sys, "_MEIPASS", root)
            return cls.from_root(root, resource_root=bundle_dir)
        return cls.from_root(root)
