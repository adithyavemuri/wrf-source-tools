from __future__ import annotations

import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .util import run, sha256_file


def _git_value(wrf_root: Path, *arguments: str) -> str | None:
    try:
        return run(("git", *arguments), cwd=wrf_root)
    except (OSError, RuntimeError):
        return None
    except Exception:
        return None


def _parse_key_values(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def create_manifest(wrf_root: Path, build_manifest: Path | None = None) -> dict:
    wrf_root = wrf_root.resolve()
    if not (wrf_root / "main").is_dir() or not (wrf_root / "Registry").is_dir():
        raise ValueError(f"Not a recognizable WRF source tree: {wrf_root}")

    configure = wrf_root / "configure.wrf"
    submodule_text = _git_value(wrf_root, "submodule", "status", "--recursive") or ""
    worktree_status = _git_value(wrf_root, "status", "--porcelain=v1", "--untracked-files=no") or ""
    submodules = []
    for line in submodule_text.splitlines():
        fields = line.strip().split()
        if len(fields) >= 2:
            submodules.append({"commit": fields[0].lstrip("-+U"), "path": fields[1]})

    tools = {}
    for name in ("git", "gcc", "gfortran", "rg"):
        executable = shutil.which(name)
        tools[name] = {"path": executable}
        if executable:
            flag = "--version"
            try:
                tools[name]["version"] = run((executable, flag)).splitlines()[0]
            except Exception:
                tools[name]["version"] = "unknown"

    return {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "indexer_version": __version__,
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "wrf": {
            "root": str(wrf_root),
            "commit": _git_value(wrf_root, "rev-parse", "HEAD"),
            "describe": _git_value(wrf_root, "describe", "--tags", "--always"),
            "worktree": {
                "tracked_changes_present": bool(worktree_status),
                "tracked_change_count": len(worktree_status.splitlines()),
            },
            "submodules": submodules,
            "configure_wrf": {
                "path": str(configure) if configure.is_file() else None,
                "sha256": sha256_file(configure) if configure.is_file() else None,
            },
        },
        "compiled_build": _parse_key_values(build_manifest),
        "tools": tools,
        "policy": {"source_read_only": True},
    }
