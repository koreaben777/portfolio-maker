from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from portfolio_maker.application.approval import ApprovalFormatError, ApprovalMissingError, write_sample_approval
from portfolio_maker.application.artifact_approval import write_sample_artifact_policy
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.discovery import discover_sources
from portfolio_maker.application.draft_portfolio import ProfileFormatError, draft_portfolio
from portfolio_maker.application.ingestion import ingest_sources
from portfolio_maker.application.render_html import HtmlRenderError, render_html
from portfolio_maker.application.semantic_index import (
    SemanticIndexError,
    apply_semantic_index,
    prepare_semantic_index,
)
from portfolio_maker.application.project_composition import (
    ProjectCompositionError,
    compose_projects,
    prepare_project_review,
    write_sample_project_approval,
)
from portfolio_maker.application.project_boundary import ProjectBoundaryError
from portfolio_maker.application.models import (
    BuildProfileRequest,
    DiscoverSourcesRequest,
    DraftPortfolioRequest,
    IngestSourcesRequest,
    RenderHtmlRequest,
    ComposeProjectsRequest,
    PrepareProjectReviewRequest,
    ApplySemanticIndexRequest,
    PrepareSemanticIndexRequest,
)
from portfolio_maker.infrastructure.github_connector import GitHubDiscoveryError
from portfolio_maker.infrastructure.local_discovery import DiscoveryRootError
from portfolio_maker.infrastructure.managed_files import write_managed_text
from portfolio_maker.infrastructure.sqlite_repository import RepositoryError
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="portfolio-maker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover")
    discover.add_argument("--workspace", type=Path, default=Path("."))
    discover.add_argument("--home", type=Path, default=Path.home())
    discover.add_argument("--no-github", action="store_true")
    discover.add_argument("--forbidden-path", type=Path, action="append", default=[])
    discover.add_argument("--exclude-directory", type=Path, action="append", default=[])

    approve = subparsers.add_parser("approve")
    approve.add_argument("--workspace", type=Path, default=Path("."))
    approve.add_argument("--write-sample", action="store_true")
    approve.add_argument("--write-sample-artifact-policy", action="store_true")
    approve.add_argument("--write-sample-project-approval", action="store_true")
    approve.add_argument("--force", action="store_true")

    ingest = subparsers.add_parser("ingest")
    ingest.add_argument("--workspace", type=Path, default=Path("."))

    profile = subparsers.add_parser("build-profile")
    profile.add_argument("--workspace", type=Path, default=Path("."))

    draft = subparsers.add_parser("draft-portfolio")
    draft.add_argument("--workspace", type=Path, default=Path("."))

    render = subparsers.add_parser("render-html")
    render.add_argument("--workspace", type=Path, default=Path("."))

    review = subparsers.add_parser("prepare-project-review")
    review.add_argument("--workspace", type=Path, default=Path("."))

    compose = subparsers.add_parser("compose-projects")
    compose.add_argument("--workspace", type=Path, default=Path("."))

    prepare_index = subparsers.add_parser("prepare-semantic-index")
    prepare_index.add_argument("--workspace", type=Path, default=Path("."))
    prepare_index.add_argument("--root", type=Path, required=True)

    apply_index = subparsers.add_parser("apply-semantic-index")
    apply_index.add_argument("--workspace", type=Path, default=Path("."))

    run_mvp = subparsers.add_parser("run-mvp")
    run_mvp.add_argument("--workspace", type=Path, default=Path("."))
    run_mvp.add_argument("--home", type=Path, default=Path.home())
    run_mvp.add_argument("--no-github", action="store_true")
    run_mvp.add_argument("--forbidden-path", type=Path, action="append", default=[])
    run_mvp.add_argument("--exclude-directory", type=Path, action="append", default=[])

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _main(argv)
    except (
        ApprovalMissingError,
        ApprovalFormatError,
        DiscoveryRootError,
        GitHubDiscoveryError,
        HtmlRenderError,
        ProfileFormatError,
        ProjectBoundaryError,
        ProjectCompositionError,
        RepositoryError,
        SemanticIndexError,
        json.JSONDecodeError,
        OSError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1


def _main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "discover":
        result = discover_sources(
            DiscoverSourcesRequest(
                workspace=args.workspace,
                home=args.home,
                include_github=not args.no_github,
                forbidden_paths=tuple(args.forbidden_path),
                excluded_directories=tuple(args.exclude_directory),
            )
        )
        print(f"Discovery report: {result.report_path}")
        print(f"Discovered: {result.discovered_count}, skipped: {result.skipped_count}")
        return 0

    if args.command == "approve":
        paths = WorkspacePaths.from_root(args.workspace)
        if args.write_sample:
            print(f"Sample approval file: {write_sample_approval(paths, force=args.force)}")
        elif args.write_sample_artifact_policy:
            print(
                "Sample artifact policy file: "
                f"{write_sample_artifact_policy(paths, force=args.force)}"
            )
        elif args.write_sample_project_approval:
            print(
                "Sample project approval file: "
                f"{write_sample_project_approval(paths, force=args.force)}"
            )
        else:
            print(f"Approval file: {paths.approval_path}")
        return 0

    if args.command == "ingest":
        result = ingest_sources(IngestSourcesRequest(workspace=args.workspace))
        print(f"Ingested: {result.ingested_count}, skipped: {result.skipped_count}")
        return 0

    if args.command == "build-profile":
        result = build_profile(BuildProfileRequest(workspace=args.workspace))
        print(f"Master profile: {result.markdown_path}")
        print(f"Claims: {result.claim_count}")
        return 0

    if args.command == "draft-portfolio":
        result = draft_portfolio(DraftPortfolioRequest(workspace=args.workspace))
        print(f"Portfolio draft: {result.markdown_path}")
        print(f"Projects: {result.project_count}")
        return 0

    if args.command == "render-html":
        result = render_html(RenderHtmlRequest(workspace=args.workspace))
        print(f"Public manifest: {result.manifest_path}")
        print(f"Portfolio HTML: {result.html_path}")
        return 0

    if args.command == "prepare-project-review":
        result = prepare_project_review(
            PrepareProjectReviewRequest(workspace=args.workspace)
        )
        print(f"Project review input: {result.input_path}")
        print(f"Evidence: {result.evidence_count}")
        return 0

    if args.command == "compose-projects":
        result = compose_projects(ComposeProjectsRequest(workspace=args.workspace))
        print(f"Composed projects: {result.project_count}")
        print(f"Unassigned evidence: {result.unassigned_evidence_count}")
        return 0

    if args.command == "prepare-semantic-index":
        try:
            result = prepare_semantic_index(
                PrepareSemanticIndexRequest(workspace=args.workspace, root=args.root)
            )
            _write_semantic_index_report(
                WorkspacePaths.from_root(args.workspace), result.revision_id
            )
        except ApprovalMissingError as error:
            raise SemanticIndexError("semantic index approval is missing") from error
        except ApprovalFormatError as error:
            raise SemanticIndexError("semantic index approval is invalid") from error
        except OSError as error:
            raise SemanticIndexError("semantic index preparation failed") from error
        print("Semantic index input prepared.")
        print(f"Chunks: {result.chunk_count}")
        return 0

    if args.command == "apply-semantic-index":
        try:
            result = apply_semantic_index(
                ApplySemanticIndexRequest(workspace=args.workspace)
            )
            _write_semantic_index_report(
                WorkspacePaths.from_root(args.workspace), result.revision_id
            )
        except OSError as error:
            raise SemanticIndexError("semantic index application failed") from error
        print("Semantic index applied.")
        return 0

    if args.command == "run-mvp":
        result = discover_sources(
            DiscoverSourcesRequest(
                workspace=args.workspace,
                home=args.home,
                include_github=not args.no_github,
                forbidden_paths=tuple(args.forbidden_path),
                excluded_directories=tuple(args.exclude_directory),
            )
        )
        print(f"Discovery report: {result.report_path}")
        print("Review source approval before ingestion.")
        return 0

    return 2


def _write_semantic_index_report(paths: WorkspacePaths, revision_id: str) -> None:
    repository = SQLiteRepository(paths.db_path)
    nodes = repository.list_semantic_nodes(revision_id)
    source_id = str(nodes[0]["source_id"])
    active = repository.get_active_semantic_revision(source_id)

    def count_nodes(field: str, value: str) -> int:
        return sum(node[field] == value for node in nodes)

    report = "\n".join(
        (
            "# Semantic Index Report",
            "",
            f"Directories: {count_nodes('node_kind', 'directory')}",
            f"Files: {count_nodes('node_kind', 'file')}",
            f"Complete: {count_nodes('analysis_status', 'complete')}",
            f"Partial: {count_nodes('analysis_status', 'partial')}",
            f"Unsupported: {count_nodes('analysis_status', 'unsupported')}",
            f"Unreadable: {count_nodes('analysis_status', 'unreadable')}",
            f"Failed: {count_nodes('analysis_status', 'failed')}",
            f"Active revision: {active['id'] if active is not None else 'none'}",
            "",
        )
    )
    write_managed_text(paths.semantic_index_report_path, report)
