from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .util import run, sha256_file

SOURCE_SUFFIXES = {".f", ".f90", ".f95", ".for", ".c", ".h"}
SEED_TERMS = (
    "bl_pbl_physics",
    "pblh",
    "mynn",
    "ysu",
    "myj",
    "qke",
    "tke",
    "pbl_driver",
    "surface_driver",
)
PATH_HINTS = ("mynn", "module_bl_", "surface", "pbl")


def _tracked_files(wrf_root: Path) -> list[Path]:
    try:
        names = run(("git", "ls-files", "--recurse-submodules"), cwd=wrf_root)
        return [wrf_root / name for name in names.splitlines() if name]
    except Exception:
        return [path for path in wrf_root.rglob("*") if path.is_file()]


def _category(relative: str) -> str:
    lower = relative.lower()
    if lower.startswith("registry/"):
        return "registry"
    if lower.startswith("phys/mynn-edmf/"):
        return "mynn_submodule"
    if lower.startswith("phys/"):
        return "wrf_physics"
    if lower.startswith(("share/", "frame/", "dyn_em/", "main/")):
        return "wrf_core"
    if lower.startswith(("arch/", "configure", "makefile")):
        return "build_configuration"
    if lower.endswith((".md", ".rst", ".txt")):
        return "documentation"
    return "other"


def create_pbl_inventory(wrf_root: Path) -> dict:
    wrf_root = wrf_root.resolve()
    records = []
    term_patterns = {
        term: re.compile(rf"(?i)(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])")
        for term in SEED_TERMS
    }

    for path in _tracked_files(wrf_root):
        if not path.is_file():
            continue
        relative = path.relative_to(wrf_root).as_posix()
        lower_path = relative.lower()
        suffix = path.suffix.lower()
        is_registry = lower_path.startswith("registry/")
        is_text_candidate = suffix in SOURCE_SUFFIXES or is_registry or suffix in {".md", ".txt"}
        path_matches = [hint for hint in PATH_HINTS if hint in lower_path]
        if not is_text_candidate and not path_matches:
            continue

        # A path hint can admit extensionless files such as Makefiles. Once a
        # file is in scope, read it consistently so its line metadata and term
        # evidence describe the actual file rather than an empty placeholder.
        text = path.read_text(errors="replace")
        term_counts = {
            term: len(pattern.findall(text))
            for term, pattern in term_patterns.items()
            if pattern.search(text)
        }
        if not path_matches and not term_counts:
            continue

        records.append(
            {
                "path": relative,
                "category": _category(relative),
                "sha256": sha256_file(path),
                "line_count": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
                "discovery": {"path_hints": path_matches, "term_counts": term_counts},
            }
        )

    records.sort(key=lambda item: item["path"])
    categories = Counter(record["category"] for record in records)
    return {
        "schema_version": 1,
        "scope": "pbl_proof_of_concept",
        "seed_terms": list(SEED_TERMS),
        "file_count": len(records),
        "category_counts": dict(sorted(categories.items())),
        "files": records,
    }
