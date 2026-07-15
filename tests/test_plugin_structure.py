import json
from pathlib import Path


def test_plugin_manifest_names_repository_and_skill_root() -> None:
    payload = json.loads(Path(".codex-plugin/plugin.json").read_text())
    assert payload["name"] == "portfolio-maker"
    assert payload["version"] == "0.2.0"
    assert payload["skills"] == "./skills/"


def test_plugin_discovers_the_router_and_all_five_child_skills() -> None:
    skill_dirs = sorted(path for path in Path("skills").iterdir() if path.is_dir())

    assert [path.name for path in skill_dirs] == [
        "portfolio-artifacts",
        "portfolio-maker",
        "portfolio-project-curation",
        "portfolio-project-review",
        "portfolio-semantic-index",
        "portfolio-source-governance",
    ]
    for skill_dir in skill_dirs:
        assert (skill_dir / "SKILL.md").is_file()
        assert (skill_dir / "agents" / "openai.yaml").is_file()


def test_router_metadata_and_route_order_are_explicit() -> None:
    skill = Path("skills/portfolio-maker/SKILL.md").read_text()
    metadata = Path("skills/portfolio-maker/agents/openai.yaml").read_text()
    description = (
        "Use when starting, resuming, diagnosing, or completing an end-to-end "
        "Portfolio Maker workflow across sources, semantic indexing, project review, and artifacts."
    )
    route = [
        "$portfolio-source-governance",
        "$portfolio-semantic-index",
        "$portfolio-project-curation",
        "$portfolio-project-review",
        "$portfolio-artifacts",
    ]

    assert f"description: {description}" in skill
    assert 'display_name: "Portfolio Maker"' in metadata
    assert 'short_description: "Orchestrate the complete evidence-based portfolio workflow"' in metadata
    assert "$portfolio-maker" in metadata
    positions = [skill.index(child) for child in route]
    assert positions == sorted(positions)
    for phrase in (
        "inspect workspace state",
        "Do not read raw files",
        "missing or stale",
        "approval",
        "zero-project",
        "do not invent",
        "never auto-host",
        "never auto-publish",
        "never auto-commit",
        "never auto-push",
    ):
        assert phrase in skill


def test_repository_skill_is_a_legacy_compatible_router_shim() -> None:
    shim = Path(".agents/skills/portfolio-maker/SKILL.md").read_text()

    assert "$portfolio-maker" in shim
    assert "plugin router" in shim
    assert "0.1.0" in shim
    for command in (
        "portfolio-maker approve",
        "portfolio-maker discover",
        "portfolio-maker ingest",
        "portfolio-maker build-profile",
        "portfolio-maker draft-portfolio",
        "portfolio-maker render-html",
    ):
        assert command in shim
    assert "legacy" in shim.lower()
