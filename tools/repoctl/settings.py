from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .io import RepoctlError

def load_repoctl_settings(root: Path) -> dict[str, Any]:
    path = root / "docs/repoctl.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RepoctlError(f"invalid docs/repoctl.json: {exc.msg}", code="invalid_repoctl_settings", path="docs/repoctl.json") from exc
    if not isinstance(data, dict):
        raise RepoctlError("docs/repoctl.json must contain a JSON object", code="invalid_repoctl_settings", path="docs/repoctl.json")
    return data
