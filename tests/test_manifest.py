"""manifest.json and hacs.json: what HACS needs to offer the integration as an update."""
from __future__ import annotations

import json
import re
from pathlib import Path

from custom_components import solar_smart_miner

PACKAGE = Path(solar_smart_miner.__file__).parent
MANIFEST = json.loads((PACKAGE / "manifest.json").read_text())
HACS = json.loads((PACKAGE.parent.parent / "hacs.json").read_text())


def test_version_is_semver() -> None:
    # Release tags are v<version>; release.yml refuses a tag that doesn't match.
    assert re.fullmatch(r"\d+\.\d+\.\d+", MANIFEST["version"])


def test_manifest_has_the_keys_hacs_requires() -> None:
    for key in ("domain", "name", "version", "documentation", "issue_tracker", "codeowners"):
        assert MANIFEST.get(key), key


def test_manifest_keys_are_in_hassfest_order() -> None:
    # hassfest: domain, name, then the rest alphabetically.
    keys = list(MANIFEST)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


def test_hacs_json_names_the_integration() -> None:
    assert HACS["name"] == MANIFEST["name"]
    assert HACS.get("homeassistant")
