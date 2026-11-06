from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .io import RepoctlError


TEMPLATE_CONTRACT_REL = Path("docs/template-contract.json")
TEMPLATE_CONTRACT_SCHEMA = "agent-handoff-template.document-contract"
SUPPORTED_TEMPLATE_CONTRACT_MAJOR = 1
_VERSION_RE = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)")


def require_compatible_template_contract(root: Path) -> dict[str, Any]:
    """Check an explicit document version without invalidating legacy adopters.

    Pre-separation adopters have no marker and remain in their existing
    compatibility lane; this does not silently stamp a version onto them.
    """
    marker = root / TEMPLATE_CONTRACT_REL
    if not marker.exists() and not marker.is_symlink():
        return {"status": "legacy_unversioned", "version": ""}
    if marker.is_symlink() or not marker.is_file():
        raise RepoctlError("template document contract marker must be a regular file", code="template_contract_invalid", path=TEMPLATE_CONTRACT_REL.as_posix())
    try:
        if marker.stat().st_size > 4096:
            raise ValueError("oversized contract marker")
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise RepoctlError("template document contract marker cannot be read", code="template_contract_invalid", path=TEMPLATE_CONTRACT_REL.as_posix()) from error
    if not isinstance(data, dict) or data.get("schema") != TEMPLATE_CONTRACT_SCHEMA:
        raise RepoctlError("unknown template document contract schema", code="template_contract_invalid", path=TEMPLATE_CONTRACT_REL.as_posix())
    version = data.get("version")
    match = _VERSION_RE.fullmatch(version) if isinstance(version, str) else None
    if match is None:
        raise RepoctlError("template document contract version must be major.minor.patch", code="template_contract_invalid", path=TEMPLATE_CONTRACT_REL.as_posix())
    if int(match.group(1)) != SUPPORTED_TEMPLATE_CONTRACT_MAJOR:
        raise RepoctlError(
            f"template document contract {version} is unsupported; repoctl supports major {SUPPORTED_TEMPLATE_CONTRACT_MAJOR}",
            code="template_contract_unsupported",
            path=TEMPLATE_CONTRACT_REL.as_posix(),
        )
    return {"status": "supported", "version": version}
