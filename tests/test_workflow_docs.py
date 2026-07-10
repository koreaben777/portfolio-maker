from pathlib import Path


def test_skill_documents_portfolio_skeleton_and_first_discovery_exclusions():
    skill = Path(".agents/skills/portfolio-maker/SKILL.md").read_text(encoding="utf-8")

    assert "portfolio skeleton" in skill
    assert "Before the first GitHub discovery" in skill
