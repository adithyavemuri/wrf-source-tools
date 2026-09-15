from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path


def load_adapter(path: Path | None = None) -> dict:
    """Load the fixed WRF layout used by the implementation comparator."""
    raw = path.read_bytes() if path else files("wrf_source_tools").joinpath("adapters/wrf.json").read_bytes()
    adapter = json.loads(raw)
    adapter["adapter_sha256"] = hashlib.sha256(raw).hexdigest()
    return adapter
