import json

import pytest

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


def test_write_markdown_rejects_symlink_target_and_preserves_external_file(tmp_path):
    external = tmp_path / "external.md"
    external.write_text("external marker", encoding="utf-8")
    path = tmp_path / "artifacts" / "profile.md"
    path.parent.mkdir()
    path.symlink_to(external)

    with pytest.raises(OSError, match="regular file"):
        write_markdown(path, "replacement")

    assert external.read_text(encoding="utf-8") == "external marker"


def test_write_json_rejects_symlinked_parent_and_preserves_external_directory(tmp_path):
    external_directory = tmp_path / "external"
    external_directory.mkdir()
    managed_parent = tmp_path / "artifacts"
    managed_parent.symlink_to(external_directory, target_is_directory=True)

    with pytest.raises(OSError):
        write_json(managed_parent / "profile.json", {"version": 1})

    assert not (external_directory / "profile.json").exists()
