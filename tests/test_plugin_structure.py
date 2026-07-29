import json
import tomllib
from pathlib import Path

from portfolio_maker import __version__


def test_plugin_manifest_names_repository_and_skill_root() -> None:
    payload = json.loads(Path(".codex-plugin/plugin.json").read_text())
    assert payload["name"] == "portfolio-maker"
    assert payload["skills"] == "./skills/"


def test_release_versions_stay_aligned() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    plugin = json.loads(Path(".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    web_package = json.loads(Path("web/portfolio/package.json").read_text(encoding="utf-8"))
    web_lock = json.loads(Path("web/portfolio/package-lock.json").read_text(encoding="utf-8"))
    expected = pyproject["project"]["version"]

    assert {
        plugin["version"],
        web_package["version"],
        web_lock["version"],
        web_lock["packages"][""]["version"],
        __version__,
    } == {expected}


def test_plugin_discovers_the_router_and_all_six_child_skills() -> None:
    skill_dirs = sorted(path for path in Path("skills").iterdir() if path.is_dir())

    assert [path.name for path in skill_dirs] == [
        "portfolio-artifacts",
        "portfolio-maker",
        "portfolio-project-curation",
        "portfolio-project-review",
        "portfolio-semantic-index",
        "portfolio-source-governance",
        "portfolio-vision-analysis",
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
        "$portfolio-vision-analysis",
        "$portfolio-semantic-index",
        "$portfolio-project-curation",
        "$portfolio-project-review",
        "$portfolio-artifacts",
    ]

    assert f"description: {description}" in skill
    assert 'display_name: "Portfolio Maker"' in metadata
    assert 'short_description: "Orchestrate the complete evidence-based portfolio workflow"' in metadata
    assert "$portfolio-maker" in metadata
    route_section = skill[skill.index("Always route these stages in this order:") :]
    positions = [route_section.index(child) for child in route]
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


def test_vision_skill_is_the_only_request_bound_raw_source_capability() -> None:
    skill_paths = sorted(Path("skills").glob("*/SKILL.md"))
    skill_text = {
        path.parent.name: path.read_text(encoding="utf-8") for path in skill_paths
    }
    vision = skill_text["portfolio-vision-analysis"]
    router = skill_text["portfolio-maker"]
    artifacts = skill_text["portfolio-artifacts"]
    normalized_router = " ".join(router.split())
    normalized_artifacts = " ".join(artifacts.split())

    assert "only raw selected source permission" in vision
    for name, text in skill_text.items():
        if name != "portfolio-vision-analysis":
            assert "only raw selected source permission" not in text

    required_order = (
        "explicit confirmation",
        "prepare-vision-analysis --confirm-codex-share",
        "pending request",
        "exact approved source",
        "current Codex App native capability",
        "external APIs",
        "completed JSON",
        "status-only unsupported/unavailable/failed/cancelled JSON",
        "apply-vision-analysis",
        "begins pending",
        "set-vision-analysis-state",
    )
    positions = [vision.index(phrase) for phrase in required_order]
    assert positions == sorted(positions)
    for phrase in (
        "one explicitly selected approved local regular file at a time",
        "never bulk-read",
        "never infer consent",
        "Do not edit SQLite",
        "request/result JSON",
        "managed artifacts",
        "approvals",
        "OCR",
        "PDF/Office converters",
        "fallback uploaders",
        "other network services",
        "non-evidence",
        "explicitly included sanitized evidence",
    ):
        assert phrase.casefold() in vision.casefold()

    assert "selected source ID or request ID" in normalized_router
    assert (
        "must not read raw source, request, or result material" in normalized_router
    )
    assert "optional" in router
    assert "text-only" in router
    assert "already included derived evidence" in normalized_artifacts
    assert "does not read raw source, request, result" in normalized_artifacts
    assert "pending/excluded analysis" in normalized_artifacts


def test_vision_skill_metadata_invokes_the_skill_explicitly() -> None:
    metadata = Path(
        "skills/portfolio-vision-analysis/agents/openai.yaml"
    ).read_text(encoding="utf-8")

    assert 'display_name: "Portfolio Vision Analysis"' in metadata
    assert (
        'short_description: "Analyze one explicitly approved source with Codex vision"'
        in metadata
    )
    assert "$portfolio-vision-analysis" in metadata


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
