from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from portfolio_maker.application.approval import ApprovalFormatError
from portfolio_maker.application.models import ArtifactDeliveryScope, ArtifactKind
from portfolio_maker.infrastructure.github_connector import (
    canonical_public_github_activity_url,
    canonical_repository_name,
    contains_unicode_control,
)
from portfolio_maker.infrastructure.managed_files import read_managed_bytes, write_managed_text
from portfolio_maker.workspace import WorkspacePaths


ARTIFACT_KINDS: tuple[ArtifactKind, ...] = (
    "master_profile",
    "portfolio_draft",
    "portfolio_public_manifest",
    "portfolio_html",
)


@dataclass(frozen=True)
class ArtifactPolicy:
    artifact_kind: ArtifactKind
    delivery_scope: ArtifactDeliveryScope
    include_local: bool
    include_public_github: bool
    include_private_github: bool
    excluded_source_uris: tuple[str, ...] = ()
    excluded_repositories: tuple[str, ...] = ()
    excluded_activity_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArtifactPolicySet:
    policies: tuple[ArtifactPolicy, ...]
    explicit: bool

    @property
    def artifact_kinds(self) -> tuple[ArtifactKind, ...]:
        return ARTIFACT_KINDS

    @property
    def legacy_compatibility(self) -> bool:
        return not self.explicit

    def for_kind(self, artifact_kind: ArtifactKind) -> ArtifactPolicy:
        for policy in self.policies:
            if policy.artifact_kind == artifact_kind:
                return policy
        raise ApprovalFormatError("unknown artifact kind")


def load_artifact_policy(paths: WorkspacePaths) -> ArtifactPolicySet:
    try:
        raw_payload = read_managed_bytes(paths.artifact_approval_path)
    except FileNotFoundError:
        return ArtifactPolicySet(_default_policies(), explicit=False)
    try:
        payload = json.loads(raw_payload.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise ApprovalFormatError("Artifact approval file has invalid UTF-8") from error
    except json.JSONDecodeError as error:
        raise ApprovalFormatError("Artifact approval file has invalid JSON") from error
    if not isinstance(payload, dict):
        raise ApprovalFormatError("artifact approval payload must be an object")
    version = payload.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ApprovalFormatError("artifact approval version must be 1")
    raw_artifacts = payload.get("artifacts", {})
    if not isinstance(raw_artifacts, dict):
        raise ApprovalFormatError("artifacts must be an object")
    unknown_kinds = set(raw_artifacts) - set(ARTIFACT_KINDS)
    if unknown_kinds:
        raise ApprovalFormatError("unknown artifact kind")

    defaults = {policy.artifact_kind: policy for policy in _default_policies()}
    policies = tuple(
        _parse_policy(kind, raw_artifacts.get(kind, {}), defaults[kind])
        for kind in ARTIFACT_KINDS
    )
    return ArtifactPolicySet(policies, explicit=True)


def write_sample_artifact_policy(paths: WorkspacePaths, force: bool = False):
    paths.ensure()
    artifacts = {
        policy.artifact_kind: {
            "delivery_scope": policy.delivery_scope,
            "include_local": policy.include_local,
            "include_public_github": policy.include_public_github,
            "include_private_github": policy.include_private_github,
            "excluded_source_uris": list(policy.excluded_source_uris),
            "excluded_repositories": list(policy.excluded_repositories),
            "excluded_activity_urls": list(policy.excluded_activity_urls),
        }
        for policy in _default_policies()
    }
    payload = json.dumps({"version": 1, "artifacts": artifacts}, indent=2) + "\n"
    try:
        return write_managed_text(paths.artifact_approval_path, payload, overwrite=force)
    except FileExistsError as error:
        raise ApprovalFormatError(
            "Artifact approval file already exists. Use --force to reset it"
        ) from error


def _default_policies() -> tuple[ArtifactPolicy, ...]:
    return tuple(
        ArtifactPolicy(
            artifact_kind=kind,
            delivery_scope="restricted",
            include_local=True,
            include_public_github=True,
            include_private_github=True,
        )
        for kind in ARTIFACT_KINDS
    )


def _parse_policy(
    kind: ArtifactKind, raw: Any, default: ArtifactPolicy
) -> ArtifactPolicy:
    if not isinstance(raw, dict):
        raise ApprovalFormatError("artifact policy entries must be objects")
    scope = raw.get("delivery_scope", default.delivery_scope)
    if scope not in {"restricted", "open_public"}:
        raise ApprovalFormatError("delivery_scope must be restricted or open_public")
    include_local = _bool_value(raw, "include_local", scope == "restricted")
    include_public = _bool_value(raw, "include_public_github", True)
    include_private = _bool_value(raw, "include_private_github", scope == "restricted")
    if scope == "open_public" and (include_local or include_private):
        raise ApprovalFormatError("open_public cannot include local or private evidence")
    return ArtifactPolicy(
        artifact_kind=kind,
        delivery_scope=scope,
        include_local=include_local,
        include_public_github=include_public,
        include_private_github=include_private,
        excluded_source_uris=_safe_string_list(raw, "excluded_source_uris"),
        excluded_repositories=_canonical_repositories(raw, "excluded_repositories"),
        excluded_activity_urls=_canonical_activity_urls(raw, "excluded_activity_urls"),
    )


def _bool_value(raw: dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ApprovalFormatError(f"{key} must be a bool")
    return value


def _safe_string_list(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ApprovalFormatError(f"{key} must be a list of strings")
    if any(contains_unicode_control(item) for item in value):
        raise ApprovalFormatError(f"{key} contains unsafe text")
    return tuple(value)


def _canonical_repositories(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    values = _safe_string_list(raw, key)
    try:
        return tuple(canonical_repository_name(value) for value in values)
    except ValueError as error:
        raise ApprovalFormatError(f"{key} entries must use owner/repo form") from error


def _canonical_activity_urls(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    values = _safe_string_list(raw, key)
    canonical_values = tuple(canonical_public_github_activity_url(value) for value in values)
    if any(value is None for value in canonical_values):
        raise ApprovalFormatError(f"{key} entries must be GitHub activity URLs")
    return tuple(value for value in canonical_values if value is not None)
