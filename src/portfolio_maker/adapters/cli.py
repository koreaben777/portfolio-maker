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
from portfolio_maker.application.models import (
    BuildProfileRequest,
    DiscoverSourcesRequest,
    DraftPortfolioRequest,
    IngestSourcesRequest,
    RenderHtmlRequest,
)
from portfolio_maker.infrastructure.github_connector import GitHubDiscoveryError
from portfolio_maker.infrastructure.local_discovery import DiscoveryRootError
from portfolio_maker.infrastructure.sqlite_repository import RepositoryError
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
    approve.add_argument("--force", action="store_true")

    ingest = subparsers.add_parser("ingest")
    ingest.add_argument("--workspace", type=Path, default=Path("."))

    profile = subparsers.add_parser("build-profile")
    profile.add_argument("--workspace", type=Path, default=Path("."))

    draft = subparsers.add_parser("draft-portfolio")
    draft.add_argument("--workspace", type=Path, default=Path("."))

    render = subparsers.add_parser("render-html")
    render.add_argument("--workspace", type=Path, default=Path("."))

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
        RepositoryError,
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
