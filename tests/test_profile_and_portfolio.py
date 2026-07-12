import json

import pytest

import portfolio_maker.application.draft_portfolio as draft_portfolio_module
from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.draft_portfolio import draft_portfolio
from portfolio_maker.application.ingestion import ingest_sources
from portfolio_maker.application.models import (
    BuildProfileRequest,
    BuildProfileResult,
    DraftPortfolioRequest,
    IngestSourcesRequest,
)
from portfolio_maker.domain.models import GitHubActivity, Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.extractors import extract_text
from portfolio_maker.infrastructure.github_connector import parse_workflow_run_list
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def _ingest_approved_source(tmp_path):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "private" / "notes.md"
    source_path.parent.mkdir()
    source_path.write_text("private evidence", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name="notes.md",
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )

    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.ingested_count == 1
    return workspace, source_path, paths


def test_build_profile_treats_github_sources_as_discovery_only_in_mvp(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.GITHUB_REPOSITORY,
            uri="https://github.com/octo/demo",
            display_name="octo/demo",
            owner="octo",
            status=SourceStatus.APPROVED,
        )
    )

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    profile = json.loads(profile_result.json_path.read_text(encoding="utf-8"))
    assert profile_result.claim_count == 0
    assert profile == {"version": 1, "sources": [], "claims": []}


def test_build_profile_and_draft_portfolio_from_ingested_source(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    source_path = tmp_path / "project" / "README.md"
    source_path.parent.mkdir()
    source_path.write_text(
        "# Portfolio Maker\nBuilt an approval-gated evidence pipeline.",
        encoding="utf-8",
    )
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name="Portfolio Maker",
            owner=None,
            status=SourceStatus.INGESTED,
        )
    )
    content_hash = extract_text(source_path).content_hash
    snapshot_path = paths.local_snapshots_dir / f"source-{source_id}-{content_hash}.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "source_id": source_id,
                "source_uri": source_path.resolve().as_uri(),
                "display_name": "README.md",
                "content_hash": content_hash,
                "extractor": "text-v2",
                "extracted_at": "2026-07-09T00:00:00Z",
                "text": "# Portfolio Maker\nBuilt an approval-gated evidence pipeline.",
            }
        ),
        encoding="utf-8",
    )
    repository.insert_source_snapshot(source_id, snapshot_path, content_hash, "text-v2")

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    assert profile_result.json_path == paths.master_profile_json_path
    assert profile_result.markdown_path == paths.master_profile_md_path
    assert profile_result.claim_count == 1
    profile = json.loads(paths.master_profile_json_path.read_text(encoding="utf-8"))
    assert profile["version"] == 1
    assert profile["sources"][0] == {
        "id": source_id,
        "type": "local_file",
        "uri": source_path.resolve().as_uri(),
        "display_name": "Portfolio Maker",
        "owner": None,
        "status": "ingested",
    }
    assert profile["claims"] == [
        {
            "claim_type": "project_evidence",
            "text": "Portfolio Maker: Built an approval-gated evidence pipeline.",
            "confidence": "medium",
            "public_safe": False,
            "evidence_uri": source_path.resolve().as_uri(),
            "evidence_snapshot": str(snapshot_path),
        }
    ]
    profile_markdown = paths.master_profile_md_path.read_text(encoding="utf-8")
    assert "## Sources" in profile_markdown
    assert "## Claims" in profile_markdown
    assert "Portfolio Maker" in profile_markdown
    assert "Built an approval-gated evidence pipeline." in profile_markdown

    portfolio_result = draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    assert portfolio_result.markdown_path == paths.portfolio_draft_path
    assert portfolio_result.project_count == 1
    draft = paths.portfolio_draft_path.read_text(encoding="utf-8")
    assert "Portfolio Maker" in draft
    assert "- Role: Evidence review required" in draft
    assert "- Technical approach: Evidence review required" in draft
    assert "- Outcome: Evidence review required" in draft
    assert "Internal evidence reference: `Portfolio Maker`" in draft
    assert str(source_path) not in draft


def test_build_profile_includes_only_explicitly_approved_public_github_activity(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    activity_url = "https://github.com/octo/demo/pull/1"
    approval["approved_github_activity_urls"] = [activity_url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.GITHUB_REPOSITORY,
            uri="https://github.com/octo/demo",
            display_name="octo/demo",
            owner="octo",
            status=SourceStatus.DISCOVERED,
        )
    )
    repository.insert_github_activity(
        GitHubActivity(
            id=None,
            source_id=source_id,
            repo="octo/demo",
            activity_type="pull_request",
            url=activity_url,
            title="Add evidence",
            state="MERGED",
            author="octo",
            created_at="2026-01-01T00:00:00Z",
            merged_at="2026-01-02T00:00:00Z",
        )
    )

    result = build_profile(BuildProfileRequest(workspace=workspace))
    profile = json.loads(result.json_path.read_text(encoding="utf-8"))

    assert result.claim_count == 1
    assert profile["claims"][0]["evidence_uri"] == activity_url
    assert profile["claims"][0]["public_safe"] is True


def test_build_profile_revalidates_github_activity_policy_before_use(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approved_url = "https://github.com/octo/demo/pull/1"
    approval.update(
        {
            "approved_github_activity_urls": [
                approved_url,
                "https://github.com/other/demo/pull/2",
                "https://github.com/octo/private/pull/3",
            ],
            "allowed_repositories": ["octo/demo", "octo/private"],
            "excluded_repositories": ["octo/private"],
        }
    )
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    for repo in ("octo/demo", "other/demo", "octo/private"):
        source_id = repository.upsert_source(
            Source(
                id=None,
                type=SourceType.GITHUB_REPOSITORY,
                uri=f"https://github.com/{repo}",
                display_name=repo,
                owner=repo.split("/", 1)[0],
                status=SourceStatus.DISCOVERED,
            )
        )
        url = {
            "octo/demo": approved_url,
            "other/demo": "https://github.com/other/demo/pull/2",
            "octo/private": "https://github.com/octo/private/pull/3",
        }[repo]
        repository.insert_github_activity(
            GitHubActivity(
                id=None,
                source_id=source_id,
                repo=repo,
                activity_type="pull_request",
                url=url,
                title=f"{repo} evidence",
                state="MERGED",
                author="octo",
                created_at="2026-01-01T00:00:00Z",
                merged_at="2026-01-02T00:00:00Z",
                is_private=repo == "octo/private",
            )
        )

    result = build_profile(BuildProfileRequest(workspace=workspace))
    profile = json.loads(result.json_path.read_text(encoding="utf-8"))

    assert result.claim_count == 1
    assert [claim["evidence_uri"] for claim in profile["claims"]] == [approved_url]


def test_build_profile_traces_approved_github_claim_to_evidence(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    activity_url = "https://github.com/octo/demo/commit/abc123"
    approval["approved_github_activity_urls"] = [activity_url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.GITHUB_REPOSITORY,
            uri="https://github.com/octo/demo",
            display_name="octo/demo",
            owner="octo",
            status=SourceStatus.DISCOVERED,
        )
    )
    activity_id = repository.insert_github_activity(
        GitHubActivity(
            id=None,
            source_id=source_id,
            repo="octo/demo",
            activity_type="commit",
            url=activity_url,
            title="Add traceable evidence",
            state="committed",
            author="octo",
            created_at="2026-01-01T00:00:00Z",
            merged_at=None,
        )
    )

    build_profile(BuildProfileRequest(workspace=workspace))

    with repository._read_connection() as conn:
        row = conn.execute(
            """
            SELECT evidence_items.github_activity_id, evidence_items.locator,
                   evidence_items.public_safe, claim_evidence.support_level,
                   career_claims.public_safe
            FROM evidence_items
            JOIN claim_evidence ON claim_evidence.evidence_id = evidence_items.id
            JOIN career_claims ON career_claims.id = claim_evidence.claim_id
            """
        ).fetchone()
    assert tuple(row) == (activity_id, activity_url, 1, "direct", 1)


def test_build_profile_masks_bearer_workflow_author_from_parser_to_artifact(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    activity_url = "https://github.com/octo/demo/actions/runs/1"
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_github_activity_urls"] = [activity_url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    candidate = parse_workflow_run_list(
        "octo/demo",
        {
            "workflow_runs": [
                {
                    "html_url": activity_url,
                    "name": "CI",
                    "conclusion": "success",
                    "status": "completed",
                    "actor": {"login": "Bearer author-sentinel"},
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        },
    )[0]
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            None,
            SourceType.GITHUB_REPOSITORY,
            "https://github.com/octo/demo",
            "octo/demo",
            "octo",
            SourceStatus.DISCOVERED,
        )
    )
    repository.insert_github_activity(
        GitHubActivity(
            None,
            source_id,
            candidate.repo,
            candidate.activity_type,
            candidate.url,
            candidate.title,
            candidate.state,
            candidate.author,
            candidate.created_at,
            candidate.merged_at,
            state_field=candidate.state_field,
        )
    )

    result = build_profile(BuildProfileRequest(workspace=workspace))
    profile = json.loads(result.json_path.read_text(encoding="utf-8"))

    assert result.claim_count == 1
    assert "Bearer author-sentinel" not in json.dumps(profile)
    assert profile["claims"][0]["author"] == "[REDACTED]"


def test_draft_portfolio_renders_approved_github_activity_as_evidence_with_provenance(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    activity_url = "https://github.com/octo/demo/pull/1"
    approval["approved_github_activity_urls"] = [activity_url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(None, SourceType.GITHUB_REPOSITORY, "https://github.com/octo/demo", "octo/demo", "octo", SourceStatus.DISCOVERED)
    )
    repository.insert_github_activity(
        GitHubActivity(None, source_id, "octo/demo", "pull_request", activity_url, "Safe title", "MERGED", "octo", "2026-01-01T00:00:00Z", None)
    )

    result = draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    draft = paths.portfolio_draft_path.read_text(encoding="utf-8")
    with repository._read_connection() as conn:
        artifact = conn.execute(
            "SELECT input_manifest FROM artifacts WHERE kind = 'portfolio_draft' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        master_artifact = conn.execute(
            "SELECT input_manifest FROM artifacts WHERE kind = 'master_profile' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        project_id = conn.execute("SELECT project_id FROM career_claims").fetchone()[0]
    assert result.project_count == 0
    assert "## GitHub Activity Evidence" in draft
    assert "Safe title" in draft
    assert activity_url in draft
    assert "Role: Evidence review required" not in draft
    assert json.loads(artifact["input_manifest"])["claim_ids"]
    assert json.loads(artifact["input_manifest"])["evidence_ids"]
    assert artifact["input_manifest"] == master_artifact["input_manifest"]
    assert project_id is not None


def test_build_profile_skips_malformed_or_case_duplicate_github_activity_rows(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    activity_url = "https://github.com/octo/demo/pull/1"
    approval["approved_github_activity_urls"] = [activity_url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(None, SourceType.GITHUB_REPOSITORY, "https://github.com/octo/demo", "octo/demo", "octo", SourceStatus.DISCOVERED)
    )
    with repository._connection() as conn:
        for repo, activity_type, state in (
            ("octo/demo", "pull_request", "MERGED"),
            ("Octo/Demo", "pull_request", "MERGED"),
            ("octo/demo", "issue", "MERGED"),
            ("octo/demo", "workflow_run", ""),
        ):
            conn.execute(
                """
                INSERT INTO github_activities
                    (source_id, repo, activity_type, url, title, state, author, created_at, is_private)
                VALUES (?, ?, ?, ?, 'OPENAI_API_KEY=synthetic', ?, 'author\nname', '2026-01-01T00:00:00Z', 0)
                """,
                    (source_id, repo, activity_type, activity_url, state),
            )

    result = build_profile(BuildProfileRequest(workspace=workspace))
    profile = json.loads(result.json_path.read_text(encoding="utf-8"))

    assert result.claim_count == 1
    assert "OPENAI_API_KEY=synthetic" not in profile["claims"][0]["text"]
    assert "\n" not in profile["claims"][0]["author"]


@pytest.mark.parametrize("created_at", ("", "not-a-timestamp"))
def test_build_profile_excludes_legacy_github_activity_with_invalid_timestamp(tmp_path, created_at):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    activity_url = "https://github.com/octo/demo/pull/1"
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_github_activity_urls"] = [activity_url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(None, SourceType.GITHUB_REPOSITORY, "https://github.com/octo/demo", "octo/demo", "octo", SourceStatus.DISCOVERED)
    )
    with repository._connection() as conn:
        conn.execute(
            """
            INSERT INTO github_activities
                (source_id, repo, activity_type, url, title, state, author, created_at, is_private)
            VALUES (?, 'octo/demo', 'pull_request', ?, 'Legacy row', 'MERGED', 'octo', ?, 0)
            """,
            (source_id, activity_url, created_at),
        )

    result = build_profile(BuildProfileRequest(workspace=workspace))
    draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    assert result.claim_count == 0
    assert activity_url not in paths.portfolio_draft_path.read_text(encoding="utf-8")


def test_build_profile_excludes_legacy_github_activity_with_cross_repository_url(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    activity_url = "https://github.com/octo/other/pull/1"
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_github_activity_urls"] = [activity_url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(None, SourceType.GITHUB_REPOSITORY, "https://github.com/octo/demo", "octo/demo", "octo", SourceStatus.DISCOVERED)
    )
    with repository._connection() as conn:
        conn.execute(
            """
            INSERT INTO github_activities
                (source_id, repo, activity_type, url, title, state, author, created_at, is_private)
            VALUES (?, 'octo/demo', 'pull_request', ?, 'Legacy row', 'MERGED', 'octo', '2026-01-01T00:00:00Z', 0)
            """,
            (source_id, activity_url),
        )

    result = build_profile(BuildProfileRequest(workspace=workspace))
    draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    assert result.claim_count == 0
    assert activity_url not in paths.portfolio_draft_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "source_uri",
    (
        "not-a-url",
        "https://github.com/octo/other",
        "https://github.com/octo/demo/pull/1",
    ),
)
def test_build_profile_excludes_legacy_github_activity_with_invalid_source_uri(
    tmp_path,
    source_uri,
):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    activity_url = "https://github.com/octo/demo/pull/1"
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_github_activity_urls"] = [activity_url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(None, SourceType.GITHUB_REPOSITORY, source_uri, "octo/demo", "octo", SourceStatus.DISCOVERED)
    )
    repository.insert_github_activity(
        GitHubActivity(None, source_id, "octo/demo", "pull_request", activity_url, "Legacy row", "MERGED", "octo", "2026-01-01T00:00:00Z", None)
    )

    result = build_profile(BuildProfileRequest(workspace=workspace))
    draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    assert result.claim_count == 0
    assert activity_url not in paths.portfolio_draft_path.read_text(encoding="utf-8")


def test_build_profile_retires_github_artifacts_when_activity_becomes_private(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    activity_url = "https://github.com/octo/demo/pull/1"
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_github_activity_urls"] = [activity_url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.upsert_project("local:sentinel", public_safe=True)
    source_id = repository.upsert_source(
        Source(None, SourceType.GITHUB_REPOSITORY, "https://github.com/octo/demo", "octo/demo", "octo", SourceStatus.DISCOVERED)
    )
    activity_id = repository.insert_github_activity(
        GitHubActivity(None, source_id, "octo/demo", "pull_request", activity_url, "Safe title", "MERGED", "octo", "2026-01-01T00:00:00Z", None)
    )

    assert build_profile(BuildProfileRequest(workspace=workspace)).claim_count == 1
    with repository._connection() as conn:
        conn.execute("UPDATE github_activities SET is_private = 1 WHERE id = ?", (activity_id,))

    result = build_profile(BuildProfileRequest(workspace=workspace))
    draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    with repository._read_connection() as conn:
        rows = conn.execute(
            """
            SELECT evidence_items.public_safe, career_claims.public_safe
            FROM evidence_items
            JOIN claim_evidence ON claim_evidence.evidence_id = evidence_items.id
            JOIN career_claims ON career_claims.id = claim_evidence.claim_id
            WHERE evidence_items.github_activity_id = ?
            """,
            (activity_id,),
        ).fetchall()
        project = conn.execute(
            "SELECT public_safe FROM projects WHERE name = 'github:octo/demo'"
        ).fetchone()
        local_project = conn.execute(
            "SELECT public_safe FROM projects WHERE name = 'local:sentinel'"
        ).fetchone()
    assert result.claim_count == 0
    assert [tuple(row) for row in rows] == [(0, 0)]
    assert project["public_safe"] == 0
    assert local_project["public_safe"] == 1
    assert activity_url not in paths.portfolio_draft_path.read_text(encoding="utf-8")


def test_build_profile_updates_github_claim_when_activity_metadata_changes(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    activity_url = "https://github.com/octo/demo/pull/1"
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_github_activity_urls"] = [activity_url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(None, SourceType.GITHUB_REPOSITORY, "https://github.com/octo/demo", "octo/demo", "octo", SourceStatus.DISCOVERED)
    )
    activity = GitHubActivity(None, source_id, "octo/demo", "pull_request", activity_url, "Title v1", "MERGED", "octo", "2026-01-01T00:00:00Z", None)
    repository.insert_github_activity(activity)
    assert build_profile(BuildProfileRequest(workspace=workspace)).claim_count == 1
    repository.insert_github_activity(
        GitHubActivity(None, source_id, "octo/demo", "pull_request", activity_url, "Title v2", "MERGED", "octo", "2026-01-01T00:00:00Z", None)
    )

    result = build_profile(BuildProfileRequest(workspace=workspace))

    with repository._read_connection() as conn:
        rows = conn.execute(
            """
            SELECT career_claims.text, career_claims.public_safe
            FROM career_claims
            JOIN claim_evidence ON claim_evidence.claim_id = career_claims.id
            JOIN evidence_items ON evidence_items.id = claim_evidence.evidence_id
            WHERE evidence_items.github_activity_id IS NOT NULL
            ORDER BY career_claims.id
            """
        ).fetchall()
    assert result.claim_count == 1
    assert [tuple(row) for row in rows] == [("octo/demo: Title v2", 1)]


def test_build_profile_excludes_legacy_github_activity_with_empty_normalized_title(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    activity_url = "https://github.com/octo/demo/pull/1"
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_github_activity_urls"] = [activity_url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(None, SourceType.GITHUB_REPOSITORY, "https://github.com/octo/demo", "octo/demo", "octo", SourceStatus.DISCOVERED)
    )
    repository.insert_github_activity(
        GitHubActivity(None, source_id, "octo/demo", "pull_request", activity_url, " \n", "MERGED", "octo", "2026-01-01T00:00:00Z", None)
    )

    result = build_profile(BuildProfileRequest(workspace=workspace))
    draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    assert result.claim_count == 0
    assert activity_url not in paths.portfolio_draft_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("state", ("Bearer synthetic.token", "\u0000"))
def test_build_profile_excludes_legacy_github_activity_with_invalid_state(tmp_path, state):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    activity_url = "https://github.com/octo/demo/pull/1"
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_github_activity_urls"] = [activity_url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(None, SourceType.GITHUB_REPOSITORY, "https://github.com/octo/demo", "octo/demo", "octo", SourceStatus.DISCOVERED)
    )
    with repository._connection() as conn:
        conn.execute(
            """
            INSERT INTO github_activities
                (source_id, repo, activity_type, url, title, state, author, created_at, is_private)
            VALUES (?, 'octo/demo', 'pull_request', ?, 'Safe title', ?, 'octo', ?, 0)
            """,
            (source_id, activity_url, state, "2026-01-01T00:00:00Z"),
        )

    result = build_profile(BuildProfileRequest(workspace=workspace))
    draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    assert result.claim_count == 0
    assert activity_url not in paths.portfolio_draft_path.read_text(encoding="utf-8")


def test_build_profile_derives_legacy_github_source_metadata_from_repository(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    activity_url = "https://github.com/octo/demo/pull/1"
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_github_activity_urls"] = [activity_url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            None,
            SourceType.GITHUB_REPOSITORY,
            "https://github.com/octo/demo",
            "Bearer legacy-display",
            "Bearer legacy-owner",
            SourceStatus.DISCOVERED,
        )
    )
    repository.insert_github_activity(
        GitHubActivity(
            None,
            source_id,
            "octo/demo",
            "pull_request",
            activity_url,
            "Safe title",
            "MERGED",
            "octo",
            "2026-01-01T00:00:00Z",
            None,
        )
    )

    result = build_profile(BuildProfileRequest(workspace=workspace))
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    profile_source = payload["sources"][0]
    markdown = result.markdown_path.read_text(encoding="utf-8")

    assert profile_source["display_name"] == "octo/demo"
    assert profile_source["owner"] == "octo"
    assert "Bearer legacy-display" not in json.dumps(payload)
    assert "Bearer legacy-owner" not in json.dumps(payload)
    assert "Bearer legacy-display" not in markdown


def test_build_profile_excludes_legacy_workflow_activity_with_unsupported_state(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    activity_url = "https://github.com/octo/demo/actions/runs/1"
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_github_activity_urls"] = [activity_url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            None,
            SourceType.GITHUB_REPOSITORY,
            "https://github.com/octo/demo",
            "octo/demo",
            "octo",
            SourceStatus.DISCOVERED,
        )
    )
    with repository._connection() as conn:
        conn.execute(
            """
            INSERT INTO github_activities
                (source_id, repo, activity_type, url, title, state, author, created_at, is_private)
            VALUES (?, 'octo/demo', 'workflow_run', ?, 'CI', 'unsupported', 'octo', ?, 0)
            """,
            (source_id, activity_url, "2026-01-01T00:00:00Z"),
        )

    result = build_profile(BuildProfileRequest(workspace=workspace))

    assert result.claim_count == 0
    assert activity_url not in result.markdown_path.read_text(encoding="utf-8")


def test_build_profile_accepts_persisted_workflow_provenance_but_rejects_ambiguous_legacy(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    activity_url = "https://github.com/octo/demo/actions/runs/1"
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_github_activity_urls"] = [activity_url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            None,
            SourceType.GITHUB_REPOSITORY,
            "https://github.com/octo/demo",
            "octo/demo",
            "octo",
            SourceStatus.DISCOVERED,
        )
    )
    activity_id = repository.insert_github_activity(
        GitHubActivity(
            None,
            source_id,
            "octo/demo",
            "workflow_run",
            activity_url,
            "CI",
            "queued",
            "octo",
            "2026-01-01T00:00:00Z",
            None,
            state_field="status",
        )
    )

    assert repository.list_github_activities()[0].state_field == "status"
    assert build_profile(BuildProfileRequest(workspace=workspace)).claim_count == 1

    with repository._connection() as conn:
        conn.execute(
            "UPDATE github_activities SET state = 'completed' WHERE id = ?",
            (activity_id,),
        )

    completed_result = build_profile(BuildProfileRequest(workspace=workspace))
    draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    assert completed_result.claim_count == 0
    assert activity_url not in paths.portfolio_draft_path.read_text(encoding="utf-8")

    control_state = "queued" + chr(0)
    with repository._connection() as conn:
        conn.execute(
            "UPDATE github_activities SET state = ? WHERE id = ?",
            (control_state, activity_id),
        )

    control_result = build_profile(BuildProfileRequest(workspace=workspace))
    draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    assert control_result.claim_count == 0
    assert activity_url not in paths.portfolio_draft_path.read_text(encoding="utf-8")

    with repository._connection() as conn:
        conn.execute(
            "UPDATE github_activities SET state_field = NULL WHERE id = ?",
            (activity_id,),
        )

    result = build_profile(BuildProfileRequest(workspace=workspace))
    draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    assert result.claim_count == 0
    assert activity_url not in paths.portfolio_draft_path.read_text(encoding="utf-8")


def test_build_profile_excludes_ingested_source_after_approval_revoked(tmp_path):
    workspace, source_path, paths = _ingest_approved_source(tmp_path)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = []
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))
    portfolio_result = draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    assert profile_result.claim_count == 0
    assert json.loads(profile_result.json_path.read_text(encoding="utf-8")) == {
        "version": 1,
        "sources": [],
        "claims": [],
    }
    assert source_path.name not in profile_result.markdown_path.read_text(encoding="utf-8")
    assert portfolio_result.project_count == 0
    assert source_path.name not in portfolio_result.markdown_path.read_text(encoding="utf-8")


def test_build_profile_invalidates_existing_portfolio_after_approval_revoked(tmp_path):
    workspace, source_path, paths = _ingest_approved_source(tmp_path)
    draft_portfolio(DraftPortfolioRequest(workspace=workspace))
    assert source_path.name in paths.portfolio_draft_path.read_text(encoding="utf-8")
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = []
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")

    build_profile(BuildProfileRequest(workspace=workspace))

    assert not paths.portfolio_draft_path.exists()


def test_build_profile_excludes_ingested_source_under_new_forbidden_path(tmp_path):
    workspace, source_path, paths = _ingest_approved_source(tmp_path)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["forbidden_paths"] = [str(source_path.parent)]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))
    portfolio_result = draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    assert profile_result.claim_count == 0
    assert json.loads(profile_result.json_path.read_text(encoding="utf-8")) == {
        "version": 1,
        "sources": [],
        "claims": [],
    }
    assert source_path.name not in profile_result.markdown_path.read_text(encoding="utf-8")
    assert portfolio_result.project_count == 0
    assert source_path.name not in portfolio_result.markdown_path.read_text(encoding="utf-8")


def test_draft_portfolio_rebuilds_profile_after_approval_revoked(tmp_path):
    workspace, source_path, paths = _ingest_approved_source(tmp_path)
    build_profile(BuildProfileRequest(workspace=workspace))
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = []
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")

    portfolio_result = draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    assert portfolio_result.project_count == 0
    assert source_path.name not in portfolio_result.markdown_path.read_text(encoding="utf-8")


def test_build_profile_marks_changed_source_stale_and_requires_reingestion(tmp_path):
    workspace, source_path, paths = _ingest_approved_source(tmp_path)
    source_path.write_text("changed evidence", encoding="utf-8")

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    repository = SQLiteRepository(paths.db_path)
    assert profile_result.claim_count == 0
    assert repository.list_sources()[0].status == SourceStatus.STALE_SOURCE


def test_build_profile_marks_missing_snapshot_stale_without_fallback_claim(tmp_path):
    workspace, _, paths = _ingest_approved_source(tmp_path)
    repository = SQLiteRepository(paths.db_path)
    snapshot_path = repository.latest_snapshot_metadata_by_source_id()[repository.list_sources()[0].id][1]
    snapshot_path.unlink()

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    assert profile_result.claim_count == 0
    assert repository.list_sources()[0].status == SourceStatus.STALE_SOURCE


def test_relative_forbidden_path_is_anchored_to_workspace_for_profile(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    source_path = workspace / "private" / "notes.md"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("private evidence", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name="notes.md",
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )
    ingest_sources(IngestSourcesRequest(workspace=workspace))
    approval["forbidden_paths"] = ["private"]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    assert profile_result.claim_count == 0


def test_build_profile_rejects_legacy_or_tampered_snapshot_text(tmp_path):
    workspace, _, paths = _ingest_approved_source(tmp_path)
    repository = SQLiteRepository(paths.db_path)
    source = repository.list_sources()[0]
    snapshot_path = repository.latest_snapshot_metadata_by_source_id()[source.id][1]
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["extractor"] = "text-v1"
    payload["text"] = "fabricated synthetic evidence"
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    assert profile_result.claim_count == 0
    assert repository.list_sources()[0].status == SourceStatus.STALE_SOURCE


def test_build_profile_excludes_empty_snapshot_from_claims(tmp_path):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "empty.txt"
    source_path.write_text("", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name="empty.txt",
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )
    ingest_sources(IngestSourcesRequest(workspace=workspace))

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    assert profile_result.claim_count == 0
    assert json.loads(profile_result.json_path.read_text(encoding="utf-8"))["claims"] == []


def test_draft_portfolio_masks_secret_shaped_display_names(tmp_path, monkeypatch):
    paths = WorkspacePaths.from_root(tmp_path / "workspace")
    paths.ensure()
    paths.master_profile_json_path.write_text(
        json.dumps({"sources": [{"display_name": "sk-synthetic-file-token"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        draft_portfolio_module,
        "build_profile",
        lambda request: BuildProfileResult(paths.master_profile_json_path, paths.master_profile_md_path, 0),
    )

    draft_portfolio_module.draft_portfolio(DraftPortfolioRequest(workspace=paths.workspace))

    draft = paths.portfolio_draft_path.read_text(encoding="utf-8")
    assert "sk-synthetic-file-token" not in draft
    assert "[REDACTED]" in draft


def test_draft_portfolio_masks_timestamped_password_export_display_name(tmp_path, monkeypatch):
    paths = WorkspacePaths.from_root(tmp_path / "workspace")
    paths.ensure()
    paths.master_profile_json_path.write_text(
        json.dumps(
            {
                "sources": [
                    {"display_name": "bitwarden_export_20260710.json"},
                    {"display_name": "chrome_passwords_20260710.csv"},
                    {"display_name": "firefox_logins_20260710.json"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        draft_portfolio_module,
        "build_profile",
        lambda request: BuildProfileResult(paths.master_profile_json_path, paths.master_profile_md_path, 0),
    )

    draft_portfolio_module.draft_portfolio(DraftPortfolioRequest(workspace=paths.workspace))

    draft = paths.portfolio_draft_path.read_text(encoding="utf-8")
    assert "bitwarden_export_20260710.json" not in draft
    assert "chrome_passwords_20260710.csv" not in draft
    assert "firefox_logins_20260710.json" not in draft
    assert "[REDACTED]" in draft


def test_draft_portfolio_normalizes_control_characters_and_markdown_label(tmp_path, monkeypatch):
    paths = WorkspacePaths.from_root(tmp_path / "workspace")
    paths.ensure()
    paths.master_profile_json_path.write_text(
        json.dumps({"sources": [{"display_name": "safe\n## Forged"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        draft_portfolio_module,
        "build_profile",
        lambda request: BuildProfileResult(paths.master_profile_json_path, paths.master_profile_md_path, 0),
    )

    draft_portfolio_module.draft_portfolio(DraftPortfolioRequest(workspace=paths.workspace))

    draft = paths.portfolio_draft_path.read_text(encoding="utf-8")
    assert "\n## Forged" not in draft
    assert "\\#\\# Forged" in draft


def test_build_profile_rejects_snapshot_with_stale_db_extractor_metadata(tmp_path):
    workspace, _, paths = _ingest_approved_source(tmp_path)
    repository = SQLiteRepository(paths.db_path)
    source_id = repository.list_sources()[0].id
    with repository._connection() as conn:
        conn.execute(
            "UPDATE source_snapshots SET extractor = ? WHERE source_id = ?",
            ("text-v1", source_id),
        )

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    assert profile_result.claim_count == 0
    assert repository.list_sources()[0].status == SourceStatus.STALE_SOURCE
