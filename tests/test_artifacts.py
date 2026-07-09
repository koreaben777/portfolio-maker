import json

from portfolio_maker.infrastructure.artifacts import write_json, write_markdown


def test_write_json_creates_parent_and_writes_payload(tmp_path):
    path = tmp_path / "nested" / "profile.json"

    result = write_json(path, {"version": 1, "sources": ["demo"]})

    assert result == path
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "sources": ["demo"],
    }


def test_write_markdown_creates_parent_and_writes_content(tmp_path):
    path = tmp_path / "nested" / "profile.md"

    result = write_markdown(path, "# Profile\n")

    assert result == path
    assert path.read_text(encoding="utf-8") == "# Profile\n"
