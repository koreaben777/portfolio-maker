from pathlib import Path

from portfolio_maker.infrastructure.policy import (
    DEFAULT_EXCLUDED_NAMES,
    FilePolicy,
    mask_secrets,
)


def test_default_exclusions_include_sensitive_and_large_dirs():
    assert ".Trash" in DEFAULT_EXCLUDED_NAMES
    assert "Library" in DEFAULT_EXCLUDED_NAMES
    assert "node_modules" in DEFAULT_EXCLUDED_NAMES
    assert ".git" in DEFAULT_EXCLUDED_NAMES


def test_forbidden_path_blocks_descendants(tmp_path):
    forbidden = tmp_path / "private"
    target = forbidden / "notes.md"
    policy = FilePolicy(forbidden_paths=(forbidden,))

    assert policy.is_forbidden(target)
    assert policy.classify_path(target) == "forbidden"


def test_env_and_private_key_files_are_skipped(tmp_path):
    policy = FilePolicy()

    assert policy.classify_path(tmp_path / ".env") == "skipped_policy"
    assert policy.classify_path(tmp_path / "id_rsa") == "skipped_policy"
    assert policy.classify_path(tmp_path / "node_modules" / "pkg.js") == "skipped_policy"
    assert policy.classify_path(tmp_path / "project.md") == "candidate"


def test_secret_masking_removes_token_values():
    text = (
        "GITHUB_TOKEN=github_pat_abcdefghijklmnopqrstuvwxyz1234567890abcd\n"
        "password = supersecret"
    )

    masked = mask_secrets(text)

    assert "github_pat_" not in masked
    assert "supersecret" not in masked
    assert "[REDACTED]" in masked
    assert masked == "GITHUB_TOKEN=[REDACTED]\npassword = [REDACTED]"


def test_secret_masking_redacts_colon_and_json_styles():
    text = (
        'password: "my secret value"\n'
        "token: another-secret\n"
        '{"password": "json-secret", "api_key": "json-key"}'
    )

    masked = mask_secrets(text)

    assert "my secret value" not in masked
    assert "another-secret" not in masked
    assert "json-secret" not in masked
    assert "json-key" not in masked
    assert masked == (
        'password: "[REDACTED]"\n'
        "token: [REDACTED]\n"
        '{"password": "[REDACTED]", "api_key": "[REDACTED]"}'
    )


def test_secret_masking_redacts_json_and_equals_quoted_values():
    text = '{"password": "my secret value"}\napi_key = "abc def ghi"'

    masked = mask_secrets(text)

    assert "my secret value" not in masked
    assert "abc def ghi" not in masked
    assert masked == '{"password": "[REDACTED]"}\napi_key = "[REDACTED]"'


def test_relative_forbidden_path_blocks_descendants(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    policy = FilePolicy(forbidden_paths=(Path("private"),))
    target = Path("private") / "notes.md"

    assert policy.is_forbidden(target)
    assert policy.classify_path(target) == "forbidden"
