import json

from portfolio_maker.infrastructure.artifacts import write_json, write_markdown


def test_write_json_creates_parent_and_writes_payload(tmp_path):
    path = tmp_path / "nested" / "profile.json"

    write_json(path, {"version": 1, "sources": ["demo"]})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "sources": ["demo"],
    }


def test_write_markdown_creates_parent_and_writes_content(tmp_path):
    path = tmp_path / "nested" / "profile.md"

    write_markdown(path, "# Profile\n")

    assert path.read_text(encoding="utf-8") == "# Profile\n"
