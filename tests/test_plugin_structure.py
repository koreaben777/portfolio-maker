import json
from pathlib import Path


def test_plugin_manifest_names_repository_and_skill_root() -> None:
    payload = json.loads(Path(".codex-plugin/plugin.json").read_text())
    assert payload["name"] == "portfolio-maker"
    assert payload["version"] == "0.2.0"
    assert payload["skills"] == "./skills/"
