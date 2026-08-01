from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from portfolio_maker.application.approval import ApprovalFormatError, ApprovalMissingError, write_sample_approval
from portfolio_maker.application.artifact_approval import write_sample_artifact_policy
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.discovery import discover_sources
from portfolio_maker.application.draft_portfolio import ProfileFormatError, draft_portfolio
from portfolio_maker.application.ingestion import ingest_sources
from portfolio_maker.application.history import (
    HistoryError,
    apply_history_plan,
    export_history_plan,
    plan_history_cleanup,
    recover_history_cleanup,
    resume_history_cleanup,
    write_sample_history_policy,
)
from portfolio_maker.application.render_html import HtmlRenderError, render_html
from portfolio_maker.application.semantic_index import (
    SemanticIndexError,
    apply_semantic_index,
    prepare_semantic_index,
)
from portfolio_maker.application.semantic_work import (
    SemanticWorkError,
    finalize_semantic_index,
    next_semantic_work,
    semantic_index_status,
    validate_semantic_work,
)
from portfolio_maker.application.vision_analysis import (
    VisionAnalysisError,
    apply_vision_analysis,
    prepare_vision_analysis,
    set_vision_analysis_state,
)
from portfolio_maker.application.project_composition import (
    ProjectCompositionError,
    compose_projects,
    compose_projects_v2,
    prepare_project_review,
    write_sample_project_approval,
)
from portfolio_maker.application.project_boundary import (
    ProjectBoundaryError,
    prepare_project_review_v2,
)
from portfolio_maker.application.project_review_v3 import prepare_project_review_v3
from portfolio_maker.application.models import (
    BuildProfileRequest,
    DiscoverSourcesRequest,
    DraftPortfolioRequest,
    FinalizeSemanticIndexRequest,
    HistoryApplyRequest,
    HistoryExecutionRequest,
    HistoryExportRequest,
    HistoryPlanRequest,
    IngestSourcesRequest,
    RenderHtmlRequest,
    ComposeProjectsRequest,
    ComposeProjectsV2Request,
    PrepareProjectReviewRequest,
    ApplySemanticIndexRequest,
    ApplyVisionAnalysisRequest,
    NextSemanticWorkRequest,
    PrepareSemanticIndexRequest,
    PrepareSemanticIndexResult,
    PrepareVisionAnalysisRequest,
    SemanticIndexStatusRequest,
    SetVisionAnalysisStateRequest,
    ValidateSemanticWorkRequest,
)
from portfolio_maker.infrastructure.github_connector import GitHubDiscoveryError
from portfolio_maker.infrastructure.local_discovery import DiscoveryRootError
from portfolio_maker.infrastructure.managed_files import write_managed_text
from portfolio_maker.infrastructure.sqlite_repository import RepositoryError
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.infrastructure.semantic_crawler import (
    DEFAULT_DIRECTORY_TIMEOUT_BUDGET_SECONDS,
    DEFAULT_DIRECTORY_TIMEOUT_SECONDS,
    MAX_DIRECTORY_TIMEOUT_BUDGET_SECONDS,
    MAX_DIRECTORY_TIMEOUT_SECONDS,
    MIN_DIRECTORY_TIMEOUT_BUDGET_SECONDS,
)
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

    history_policy = subparsers.add_parser("history-policy")
    history_policy.add_argument("--workspace", type=Path, default=Path("."))
    history_policy.add_argument("--write-sample", action="store_true")

    history_plan = subparsers.add_parser("history-plan")
    history_plan.add_argument("--workspace", type=Path, default=Path("."))

    history_export = subparsers.add_parser("history-export")
    history_export.add_argument("--workspace", type=Path, default=Path("."))
    history_export.add_argument("--plan-id", required=True)
    history_export.add_argument("--backup-dir", type=Path, required=True)

    history_apply = subparsers.add_parser("history-apply")
    history_apply.add_argument("--workspace", type=Path, default=Path("."))
    history_apply.add_argument("--plan-id")
    history_apply.add_argument("--export-receipt")
    history_apply.add_argument("--confirm")
    history_apply.add_argument("--resume")
    history_apply.add_argument("--recover")

    review = subparsers.add_parser("prepare-project-review")
    review.add_argument("--workspace", type=Path, default=Path("."))
    review.add_argument("--version", choices=("v1", "v2", "v3"), default="v1")

    compose = subparsers.add_parser("compose-projects")
    compose.add_argument("--workspace", type=Path, default=Path("."))
    compose.add_argument("--mode", choices=("review", "automatic"))

    set_state = subparsers.add_parser("set-project-state")
    set_state.add_argument("--workspace", type=Path, default=Path("."))
    set_state.add_argument("--project-id", required=True)
    set_state.add_argument("--state", choices=("excluded", "included"), required=True)

    list_projects = subparsers.add_parser("list-projects")
    list_projects.add_argument("--workspace", type=Path, default=Path("."))
    list_projects.add_argument("--decision-status")
    list_projects.add_argument("--format", choices=("table", "ids"), default="table")

    prepare_index = subparsers.add_parser("prepare-semantic-index")
    prepare_index.add_argument("--workspace", type=Path, default=Path("."))
    prepare_index.add_argument("--root", type=Path, required=True)
    prepare_index.add_argument(
        "--directory-timeout-seconds",
        type=_directory_timeout_argument,
        default=DEFAULT_DIRECTORY_TIMEOUT_SECONDS,
    )
    prepare_index.add_argument(
        "--directory-timeout-budget-seconds",
        type=_directory_timeout_budget_argument,
        default=DEFAULT_DIRECTORY_TIMEOUT_BUDGET_SECONDS,
    )

    apply_index = subparsers.add_parser("apply-semantic-index")
    apply_index.add_argument("--workspace", type=Path, default=Path("."))

    semantic_status = subparsers.add_parser("semantic-index-status")
    semantic_status.add_argument("--workspace", type=Path, default=Path("."))
    semantic_status.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
    )

    next_semantic = subparsers.add_parser("next-semantic-work")
    next_semantic.add_argument("--workspace", type=Path, default=Path("."))

    validate_semantic = subparsers.add_parser("validate-semantic-work")
    validate_semantic.add_argument("--workspace", type=Path, default=Path("."))
    validate_semantic.add_argument("--unit-id", required=True)

    finalize_semantic = subparsers.add_parser("finalize-semantic-index")
    finalize_semantic.add_argument("--workspace", type=Path, default=Path("."))

    prepare_vision = subparsers.add_parser("prepare-vision-analysis")
    prepare_vision.add_argument("--workspace", type=Path, default=Path("."))
    prepare_vision.add_argument("--source-id", type=int, required=True)
    prepare_vision.add_argument("--confirm-codex-share", action="store_true")

    apply_vision = subparsers.add_parser("apply-vision-analysis")
    apply_vision.add_argument("--workspace", type=Path, default=Path("."))
    apply_vision.add_argument("--result", type=Path, required=True)

    list_vision = subparsers.add_parser("list-vision-analyses")
    list_vision.add_argument("--workspace", type=Path, default=Path("."))

    set_vision_state = subparsers.add_parser("set-vision-analysis-state")
    set_vision_state.add_argument("--workspace", type=Path, default=Path("."))
    set_vision_state.add_argument("--analysis-id", required=True)
    set_vision_state.add_argument(
        "--state",
        choices=("included", "excluded"),
        required=True,
    )

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
    except HistoryError as error:
        print(_safe_history_error_message(error), file=sys.stderr)
        return 1
    except VisionAnalysisError as error:
        print(_safe_vision_error_message(error), file=sys.stderr)
        return 1
    except SemanticWorkError:
        print("semantic work command failed safely", file=sys.stderr)
        return 1
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

    if args.command == "history-policy":
        if not args.write_sample:
            raise HistoryError("history policy sample option is required")
        write_sample_history_policy(WorkspacePaths.from_root(args.workspace))
        print("History policy sample created.")
        return 0

    if args.command == "history-plan":
        result = plan_history_cleanup(HistoryPlanRequest(workspace=args.workspace))
        print(f"History plan: {result.plan_id or 'none'}")
        print(f"Candidates: {result.candidate_count}")
        print(f"Bytes: {result.total_bytes}")
        return 0

    if args.command == "history-export":
        result = export_history_plan(
            HistoryExportRequest(
                workspace=args.workspace,
                plan_id=args.plan_id,
                backup_dir=args.backup_dir,
            )
        )
        print(f"History plan: {result.plan_id}")
        print(f"Export receipt: {result.receipt_hash}")
        return 0

    if args.command == "history-apply":
        mode = _history_apply_mode(args)
        if mode == "new":
            result = apply_history_plan(
                HistoryApplyRequest(
                    workspace=args.workspace,
                    plan_id=args.plan_id,
                    export_receipt_hash=args.export_receipt,
                    confirmation=args.confirm,
                )
            )
        elif mode == "resume":
            result = resume_history_cleanup(
                HistoryExecutionRequest(
                    workspace=args.workspace,
                    execution_id=args.resume,
                )
            )
        else:
            result = recover_history_cleanup(
                HistoryExecutionRequest(
                    workspace=args.workspace,
                    execution_id=args.recover,
                )
            )
        print(f"Execution: {result.execution_id}")
        print(f"State: {result.state.value}")
        print(f"Affected: {result.affected_count}")
        return 0

    if args.command == "prepare-project-review":
        if args.version == "v3":
            result = prepare_project_review_v3(
                PrepareProjectReviewRequest(workspace=args.workspace)
            )
        elif args.version == "v2":
            result = prepare_project_review_v2(
                PrepareProjectReviewRequest(workspace=args.workspace)
            )
        else:
            result = prepare_project_review(
                PrepareProjectReviewRequest(workspace=args.workspace)
            )
        if args.version == "v3":
            print("Project review version: v3")
            print(f"Chunks: {result.chunk_count}")
        else:
            print(f"Project review input: {result.input_path}")
        print(f"Evidence: {result.evidence_count}")
        return 0

    if args.command == "compose-projects":
        paths = WorkspacePaths.from_root(args.workspace)
        if args.mode is not None or paths.project_review_input_v2_path.exists():
            result = compose_projects_v2(
                ComposeProjectsV2Request(
                    workspace=args.workspace,
                    mode=args.mode or "review",
                )
            )
            print(f"Composed projects: {result.project_count}")
            print(f"Review required: {result.review_required_count}")
            print(f"Excluded projects: {result.excluded_project_count}")
            return 0
        result = compose_projects(ComposeProjectsRequest(workspace=args.workspace))
        print(f"Composed projects: {result.project_count}")
        print(f"Unassigned evidence: {result.unassigned_evidence_count}")
        return 0

    if args.command == "set-project-state":
        repository = SQLiteRepository(WorkspacePaths.from_root(args.workspace).db_path)
        repository.initialize()
        repository.set_project_decision_state(args.project_id, args.state)
        print(f"Project state: {args.project_id} -> {args.state}")
        return 0

    if args.command == "list-projects":
        repository = SQLiteRepository(WorkspacePaths.from_root(args.workspace).db_path)
        repository.initialize()
        projects = repository.list_portfolio_projects(active_only=False)
        if args.decision_status is not None:
            projects = [
                project
                for project in projects
                if project.get("decision_status") == args.decision_status
            ]
        if args.format == "ids":
            for project in projects:
                print(project["id"])
        else:
            for project in projects:
                print(
                    "\t".join(
                        (
                            str(project["id"]),
                            str(project["decision_status"]),
                            str(project["decision_origin"]),
                        )
                    )
                )
        return 0

    if args.command == "prepare-semantic-index":
        try:
            result = prepare_semantic_index(
                PrepareSemanticIndexRequest(
                    workspace=args.workspace,
                    root=args.root,
                    directory_timeout_seconds=args.directory_timeout_seconds,
                    directory_timeout_budget_seconds=(
                        args.directory_timeout_budget_seconds
                    ),
                )
            )
            _write_prepared_semantic_index_report(
                WorkspacePaths.from_root(args.workspace),
                result,
            )
        except ApprovalMissingError as error:
            raise SemanticIndexError("semantic index approval is missing") from error
        except ApprovalFormatError as error:
            raise SemanticIndexError("semantic index approval is invalid") from error
        except OSError as error:
            raise SemanticIndexError("semantic index preparation failed") from error
        print("Semantic index input prepared.")
        print(f"Chunks: {result.chunk_count}")
        print(f"Work units: {result.work_unit_count}")
        print(f"Managed input bytes: {result.managed_input_bytes}")
        print(f"Analysis targets: {result.analysis_target_count}")
        print(f"Structural only: {result.structural_only_count}")
        print(
            "Directory timeout seconds: "
            f"{result.directory_timeout_seconds}"
        )
        print(
            "Directory timeout budget seconds: "
            f"{result.directory_timeout_budget_seconds}"
        )
        print(
            "Timed-out directories: "
            f"{result.timed_out_directory_count}"
        )
        print(
            "Unreadable directories: "
            f"{result.unreadable_directory_count}"
        )
        return 0

    if args.command == "apply-semantic-index":
        try:
            status = semantic_index_status(
                SemanticIndexStatusRequest(workspace=args.workspace)
            )
            if not status.complete:
                raise SemanticIndexError("semantic index output is missing")
            result = apply_semantic_index(
                ApplySemanticIndexRequest(workspace=args.workspace)
            )
            _write_applied_semantic_index_report(
                WorkspacePaths.from_root(args.workspace), result.revision_id
            )
        except OSError as error:
            raise SemanticIndexError("semantic index application failed") from error
        print("Semantic index applied.")
        return 0

    if args.command == "semantic-index-status":
        result = semantic_index_status(
            SemanticIndexStatusRequest(workspace=args.workspace)
        )
        revision_id = _safe_semantic_revision(result.revision_id)
        if args.format == "json":
            payload = asdict(result)
            payload["revision_id"] = revision_id
            print(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        else:
            print(f"Semantic revision: {revision_id}")
            print(f"Nodes: {result.node_count}")
            print(f"Work units: {result.work_unit_count}")
            print(f"Ready: {result.ready_count}")
            print(f"Receipted: {result.receipted_count}")
            print(f"Invalid: {result.invalid_count}")
            print(f"Complete: {'yes' if result.complete else 'no'}")
        return 0

    if args.command == "next-semantic-work":
        result = next_semantic_work(
            NextSemanticWorkRequest(workspace=args.workspace)
        )
        revision_id = _safe_semantic_revision(result.revision_id)
        if result.unit_id is None:
            print(f"Semantic revision: {revision_id}")
            print("Semantic work complete.")
            print("Remaining: 0")
            return 0
        unit_id = _safe_semantic_unit(result.unit_id)
        if result.input_path is None:
            raise SemanticWorkError("semantic work output is unsafe")
        relative_path = _safe_semantic_managed_path(
            args.workspace,
            result.input_path,
            "work-input",
            unit_id,
        )
        print(f"Semantic revision: {revision_id}")
        print(f"Semantic work unit: {unit_id}")
        print(f"Managed input: {relative_path}")
        print(f"Remaining: {result.remaining_count}")
        return 0

    if args.command == "validate-semantic-work":
        result = validate_semantic_work(
            ValidateSemanticWorkRequest(
                workspace=args.workspace,
                unit_id=args.unit_id,
            )
        )
        unit_id = _safe_semantic_unit(result.unit_id)
        if (
            not isinstance(result.output_sha256, str)
            or len(result.output_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in result.output_sha256
            )
        ):
            raise SemanticWorkError("semantic work output is unsafe")
        relative_path = _safe_semantic_managed_path(
            args.workspace,
            result.receipt_path,
            "receipts",
            unit_id,
        )
        print(f"Semantic work unit: {unit_id}")
        print(f"Receipt: {relative_path}")
        print(f"Output SHA-256: {result.output_sha256}")
        return 0

    if args.command == "finalize-semantic-index":
        result = finalize_semantic_index(
            FinalizeSemanticIndexRequest(workspace=args.workspace)
        )
        revision_id = _safe_semantic_revision(result.revision_id)
        print(f"Semantic revision: {revision_id}")
        print(f"Final chunks: {result.chunk_count}")
        print(f"Nodes: {result.node_count}")
        return 0

    if args.command == "prepare-vision-analysis":
        if args.confirm_codex_share is not True:
            raise VisionAnalysisError("Codex analysis confirmation is required")
        result = prepare_vision_analysis(
            PrepareVisionAnalysisRequest(
                workspace=args.workspace,
                source_id=args.source_id,
                confirm_codex_share=True,
            )
        )
        request_id = _safe_vision_identifier(result.id)
        status = _safe_vision_choice(result.status, {"pending"})
        print(f"Vision request: {request_id}")
        print(f"Status: {status}")
        return 0

    if args.command == "apply-vision-analysis":
        result = apply_vision_analysis(
            ApplyVisionAnalysisRequest(
                workspace=args.workspace,
                result_path=args.result,
            )
        )
        if result is None:
            print("Vision result recorded.")
            return 0
        analysis_id = _safe_vision_identifier(result.id)
        review_status = _safe_vision_choice(result.review_status, {"pending"})
        try:
            repository = SQLiteRepository(
                WorkspacePaths.from_root(args.workspace).db_path
            )
            repository.initialize()
            analysis = repository.get_vision_analysis(analysis_id)
        except (RepositoryError, OSError) as error:
            raise VisionAnalysisError("vision result is unsafe or unavailable") from error
        confidence = _safe_vision_choice(
            analysis.get("confidence"),
            {"low", "medium", "high"},
        )
        print(f"Vision analysis: {analysis_id}")
        print(f"Review state: {review_status}")
        print(f"Confidence: {confidence}")
        return 0

    if args.command == "list-vision-analyses":
        try:
            repository = SQLiteRepository(
                WorkspacePaths.from_root(args.workspace).db_path
            )
            repository.initialize()
            analyses = repository.list_vision_analyses()
            safe_rows = [
                (
                    _safe_vision_identifier(analysis.get("id")),
                    _safe_vision_choice(analysis.get("status"), {"completed"}),
                    _safe_vision_choice(
                        analysis.get("review_status"),
                        {"pending", "included", "excluded"},
                    ),
                    _safe_vision_choice(
                        analysis.get("confidence"),
                        {"low", "medium", "high"},
                    ),
                )
                for analysis in analyses
            ]
        except (RepositoryError, OSError) as error:
            raise VisionAnalysisError("vision analyses are unsafe or unavailable") from error
        print(f"Vision analyses: {len(safe_rows)}")
        for row in safe_rows:
            print("\t".join(row))
        return 0

    if args.command == "set-vision-analysis-state":
        result = set_vision_analysis_state(
            SetVisionAnalysisStateRequest(
                workspace=args.workspace,
                analysis_id=args.analysis_id,
                state=args.state,
            )
        )
        analysis_id = _safe_vision_identifier(result.analysis_id)
        state = _safe_vision_choice(result.state, {"included", "excluded"})
        print(f"Vision analysis: {analysis_id}")
        print(f"Review state: {state}")
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


def _write_prepared_semantic_index_report(
    paths: WorkspacePaths,
    result: PrepareSemanticIndexResult,
) -> None:
    report = "\n".join(
        (
            "# Semantic Index Report",
            "",
            f"Nodes: {result.node_count}",
            f"Chunks: {result.chunk_count}",
            f"Work units: {result.work_unit_count}",
            f"Analysis targets: {result.analysis_target_count}",
            f"Structural only: {result.structural_only_count}",
            f"Managed input bytes: {result.managed_input_bytes}",
            f"Partial: {result.partial_count}",
            f"Directory timeout seconds: {result.directory_timeout_seconds}",
            (
                "Directory timeout budget seconds: "
                f"{result.directory_timeout_budget_seconds}"
            ),
            f"Timed-out directories: {result.timed_out_directory_count}",
            f"Unreadable directories: {result.unreadable_directory_count}",
            "",
        )
    )
    write_managed_text(paths.semantic_index_report_path, report)


def _directory_timeout_argument(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "directory timeout must be a finite positive number"
        ) from error
    if (
        not math.isfinite(timeout)
        or timeout <= 0
        or timeout > MAX_DIRECTORY_TIMEOUT_SECONDS
    ):
        raise argparse.ArgumentTypeError(
            "directory timeout must be a finite positive number no greater "
            f"than {MAX_DIRECTORY_TIMEOUT_SECONDS:g}"
        )
    return timeout


def _directory_timeout_budget_argument(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "directory timeout budget must be a finite number"
        ) from error
    if (
        not math.isfinite(timeout)
        or timeout < MIN_DIRECTORY_TIMEOUT_BUDGET_SECONDS
        or timeout > MAX_DIRECTORY_TIMEOUT_BUDGET_SECONDS
    ):
        raise argparse.ArgumentTypeError(
            "directory timeout budget must be no smaller than "
            f"{MIN_DIRECTORY_TIMEOUT_BUDGET_SECONDS:g} and no greater than "
            f"{MAX_DIRECTORY_TIMEOUT_BUDGET_SECONDS:g}"
        )
    return timeout


def _write_applied_semantic_index_report(
    paths: WorkspacePaths,
    revision_id: str,
) -> None:
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


def _safe_semantic_revision(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 32
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SemanticWorkError("semantic work output is unsafe")
    return value


def _safe_semantic_unit(value: object) -> str:
    if (
        type(value) is not str
        or not value.startswith("unit-")
        or len(value) < 11
        or not value[5:].isdigit()
    ):
        raise SemanticWorkError("semantic work output is unsafe")
    return value


def _safe_semantic_managed_path(
    workspace: Path,
    path: Path,
    directory_name: str,
    unit_id: str,
) -> str:
    expected = (
        WorkspacePaths.from_root(workspace).reviews_dir
        / "semantic-index"
        / directory_name
        / f"{unit_id}.json"
    )
    if path.resolve(strict=False) != expected.resolve(strict=False):
        raise SemanticWorkError("semantic work output is unsafe")
    return str(expected.relative_to(WorkspacePaths.from_root(workspace).workspace))


def _history_apply_mode(args: argparse.Namespace) -> str:
    new_values = (args.plan_id, args.export_receipt, args.confirm)
    new_apply = all(isinstance(value, str) and value for value in new_values)
    any_new_value = any(value is not None for value in new_values)
    resume_present = args.resume is not None
    recover_present = args.recover is not None

    if new_apply and not resume_present and not recover_present:
        return "new"
    if (
        resume_present
        and not any_new_value
        and not recover_present
        and isinstance(args.resume, str)
        and args.resume
    ):
        return "resume"
    if (
        recover_present
        and not any_new_value
        and not resume_present
        and isinstance(args.recover, str)
        and args.recover
    ):
        return "recover"
    raise HistoryError("history apply mode is invalid")


def _safe_history_error_message(error: HistoryError) -> str:
    message = str(error)
    if "/" in message or "\\" in message or "file:" in message.casefold():
        return "managed history command failed safely"
    return message


def _safe_vision_error_message(error: VisionAnalysisError) -> str:
    if "confirmation" in str(error).casefold():
        return "Codex analysis confirmation is required"
    return "vision command failed safely"


def _safe_vision_identifier(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 32
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise VisionAnalysisError("vision output is unsafe")
    return value


def _safe_vision_choice(value: object, allowed: set[str]) -> str:
    normalized = value.value if hasattr(value, "value") else value
    if type(normalized) is not str or normalized not in allowed:
        raise VisionAnalysisError("vision output is unsafe")
    return normalized
