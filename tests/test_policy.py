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
    assert policy.classify_path(tmp_path / "credentials.JSON") == "skipped_policy"
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


def test_secret_masking_redacts_prefixed_environment_secret_keys():
    text = (
        "OPENAI_API_KEY=fake-openai-secret\n"
        "GITHUB_TOKEN=fake-github-secret\n"
        "AWS_SECRET_ACCESS_KEY=fake-aws-secret"
    )

    masked = mask_secrets(text)

    assert "fake-openai-secret" not in masked
    assert "fake-github-secret" not in masked
    assert "fake-aws-secret" not in masked
    assert masked == (
        "OPENAI_API_KEY=[REDACTED]\n"
        "GITHUB_TOKEN=[REDACTED]\n"
        "AWS_SECRET_ACCESS_KEY=[REDACTED]"
    )


def test_secret_masking_redacts_unquoted_multiword_secret_values():
    masked = mask_secrets(
        "password: my secret value\nOPENAI_API_KEY=my secret value"
    )

    assert "my secret value" not in masked
    assert masked == "password: [REDACTED]\nOPENAI_API_KEY=[REDACTED]"


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


def test_secret_masking_redacts_bare_value_with_comma():
    masked = mask_secrets("password: abc,def")

    assert "abc,def" not in masked
    assert masked == "password: [REDACTED]"


def test_secret_masking_redacts_json_bare_value_with_comma():
    masked = mask_secrets('{"token": abc,def}')

    assert "abc,def" not in masked
    assert masked == '{"token": [REDACTED]}'


def test_secret_masking_preserves_json_delimiter_after_bare_comma_value():
    masked = mask_secrets('{"token": abc,def, "next": 1}')

    assert "abc,def" not in masked
    assert masked == '{"token": [REDACTED], "next": 1}'


def test_relative_forbidden_path_blocks_descendants(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    policy = FilePolicy(forbidden_paths=(Path("private"),))
    target = Path("private") / "notes.md"

    assert policy.is_forbidden(target)
    assert policy.classify_path(target) == "forbidden"


def test_default_excluded_directory_names_are_case_insensitive(tmp_path):
    policy = FilePolicy()

    assert policy.classify_path(tmp_path / "NODE_MODULES" / "pkg.js") == "skipped_policy"


def test_file_policy_skips_common_password_export_names(tmp_path):
    policy = FilePolicy()

    assert policy.classify_path(tmp_path / "passwords.csv") == "skipped_policy"


def test_file_policy_skips_password_export_variants_and_secret_shaped_names(tmp_path):
    policy = FilePolicy()

    assert policy.classify_path(tmp_path / "LastPass_Export.CSV") == "skipped_policy"
    assert policy.classify_path(tmp_path / "Bitwarden-Export.CSV") == "skipped_policy"
    assert policy.classify_path(tmp_path / "sk-synthetic-file-token.txt") == "skipped_policy"


def test_file_policy_skips_timestamped_password_manager_exports(tmp_path):
    policy = FilePolicy()

    assert policy.classify_path(tmp_path / "bitwarden_export_20260710.json") == "skipped_policy"
    assert policy.classify_path(tmp_path / "LastPass-Export-2026-07-10-123456.CSV") == "skipped_policy"


def test_secret_masking_redacts_bearer_private_key_and_token_prefixes():
    text = (
        "Authorization: Bearer synthetic-bearer-token\n"
        "-----BEGIN PRIVATE KEY-----\n"
        "synthetic-private-key-material\n"
        "-----END PRIVATE KEY-----\n"
        "credential=sk-synthetic-token"
    )

    masked = mask_secrets(text)

    assert "synthetic-bearer-token" not in masked
    assert "synthetic-private-key-material" not in masked
    assert "sk-synthetic-token" not in masked
