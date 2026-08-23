from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path

import pytest

from portfolio_maker.domain.semantic_frontier import (
    DEEPENING_BUDGET,
    FRONTIER_BUDGET,
    FRONTIER_PHASE_HARD_BUDGETS,
    FrontierAction,
    FrontierPhase,
    PackRecordRef,
    SemanticDeferredAuditRow,
    SemanticExecutionProposal,
    SemanticPhaseBinding,
    SemanticPlannedUnit,
    SemanticProposalEnvelope,
    SemanticReceiptCommit,
    SemanticRevisionBinding,
    SemanticRunState,
    SemanticRuntimeDeferredRow,
    SemanticCheckpoint,
    SemanticAuditFreeze,
    SemanticAuditMember,
    semantic_canonical_json_bytes,
    semantic_proposal_sha256,
)
from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.models import (
    ApproveSemanticExecutionRequest,
    PrepareSemanticIndexRequest,
    SemanticIndexStatusRequest,
)
from portfolio_maker.application.semantic_index import prepare_semantic_index
from portfolio_maker.application.semantic_lifecycle_v3 import (
    SemanticLifecycleV3Error,
    approve_semantic_execution,
)
from portfolio_maker.application.semantic_work import semantic_index_status
from portfolio_maker.infrastructure.sqlite_repository import RepositoryError, SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def _digest(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _prepared_frontier_run(workspace: Path) -> tuple[SQLiteRepository, object]:
    root = workspace / "approved-source"
    root.mkdir()
    (root / "README.md").write_text("# Approved semantic fixture\n", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [root.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    prepared = prepare_semantic_index(PrepareSemanticIndexRequest(workspace, root))
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    return repository, prepared


def test_approval_transitions_awaiting_run_without_claiming_execution(
    workspace: Path,
) -> None:
    repository, prepared = _prepared_frontier_run(workspace)
    assert repository.run_state(prepared.run_id) is SemanticRunState.AWAITING_APPROVAL

    result = approve_semantic_execution(
        ApproveSemanticExecutionRequest(
            workspace,
            prepared.revision_id,
            prepared.proposal_sha256,
        )
    )

    assert result.run_state is SemanticRunState.PREPARED
    assert repository.run_state(prepared.run_id) is SemanticRunState.PREPARED
    assert repository.execution_lock() is None
    with repository._read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_runs WHERE state = 'running'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_leases WHERE run_id = ?", (prepared.run_id,)
        ).fetchone()[0] == 0


def test_repository_approval_rejects_prepared_run_without_receipt(
    workspace: Path,
) -> None:
    repository, prepared = _prepared_frontier_run(workspace)
    with repository._connection() as connection:
        connection.execute(
            "UPDATE semantic_v3_runs SET state = 'prepared' WHERE run_id = ?",
            (prepared.run_id,),
        )

    with repository._read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_approvals WHERE run_id = ?", (prepared.run_id,)
        ).fetchone()[0] == 0
    before = _lifecycle_snapshot(repository, prepared.run_id, prepared.revision_id)

    with pytest.raises(RepositoryError, match="awaiting approval"):
        repository.approve_semantic_v3_run(prepared.run_id, prepared.proposal_sha256)

    assert _lifecycle_snapshot(repository, prepared.run_id, prepared.revision_id) == before


def test_repository_approval_replays_only_exact_existing_receipt(
    workspace: Path,
) -> None:
    repository, prepared = _prepared_frontier_run(workspace)

    assert repository.approve_semantic_v3_run(
        prepared.run_id, prepared.proposal_sha256
    ) is SemanticRunState.PREPARED
    before = _lifecycle_snapshot(repository, prepared.run_id, prepared.revision_id)

    assert repository.approve_semantic_v3_run(
        prepared.run_id, prepared.proposal_sha256
    ) is SemanticRunState.PREPARED

    assert _lifecycle_snapshot(repository, prepared.run_id, prepared.revision_id) == before
    with repository._read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_approvals WHERE run_id = ?", (prepared.run_id,)
        ).fetchone()[0] == 1


def test_approval_rejects_tampered_durable_plan_without_state_mutation(
    workspace: Path,
) -> None:
    repository, prepared = _prepared_frontier_run(workspace)
    with repository._connection() as connection:
        connection.execute(
            "UPDATE semantic_v3_proposals SET plan_canonical_json = ? WHERE run_id = ?",
            (b'{"tampered":true}', prepared.run_id),
        )

    with pytest.raises(SemanticLifecycleV3Error, match="approval authority"):
        approve_semantic_execution(
            ApproveSemanticExecutionRequest(
                workspace,
                prepared.revision_id,
                prepared.proposal_sha256,
            )
        )

    assert repository.run_state(prepared.run_id) is SemanticRunState.AWAITING_APPROVAL
    with repository._read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_approvals WHERE run_id = ?", (prepared.run_id,)
        ).fetchone()[0] == 0


def test_v3_status_dispatches_to_aggregate_authority_without_project_readiness(
    workspace: Path,
) -> None:
    _repository, prepared = _prepared_frontier_run(workspace)

    status = semantic_index_status(SemanticIndexStatusRequest(workspace))

    assert status.protocol == "codex-semantic-v3"
    assert status.run_id == prepared.run_id
    assert status.run_state is SemanticRunState.AWAITING_APPROVAL
    assert status.integrity_level == "aggregate_bound"
    assert status.completion.structural_complete is False
    assert status.completion.project_discovery_ready is False
    assert status.resumable is True


def test_approval_rejects_current_source_policy_drift_without_state_mutation(
    workspace: Path,
) -> None:
    repository, prepared = _prepared_frontier_run(workspace)
    paths = WorkspacePaths.from_root(workspace)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["excluded_file_patterns"] = ["*.synthetic"]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")

    with pytest.raises(SemanticLifecycleV3Error, match="stale"):
        approve_semantic_execution(
            ApproveSemanticExecutionRequest(
                workspace, prepared.revision_id, prepared.proposal_sha256
            )
        )

    assert repository.run_state(prepared.run_id) is SemanticRunState.AWAITING_APPROVAL
    with repository._read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_approvals WHERE run_id = ?", (prepared.run_id,)
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("catalog_root", _digest("tampered-catalog")),
        ("selector_version", "tampered-selector"),
        ("private_context_scope", "tampered-scope"),
        ("total_budget_json", "{}"),
        ("phase_bindings_json", "[]"),
    ),
)
def test_approval_rejects_durable_projection_drift_without_state_mutation(
    workspace: Path, column: str, value: str,
) -> None:
    repository, prepared = _prepared_frontier_run(workspace)
    with repository._connection() as connection:
        connection.execute(
            f"UPDATE semantic_v3_proposals SET {column} = ? WHERE run_id = ?",
            (value, prepared.run_id),
        )

    with pytest.raises(SemanticLifecycleV3Error, match="approval authority"):
        approve_semantic_execution(
            ApproveSemanticExecutionRequest(
                workspace, prepared.revision_id, prepared.proposal_sha256
            )
        )

    assert repository.run_state(prepared.run_id) is SemanticRunState.AWAITING_APPROVAL
    with repository._read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_approvals WHERE run_id = ?", (prepared.run_id,)
        ).fetchone()[0] == 0


def test_two_exact_approved_v3_runs_remain_prepared_without_running_slot(
    workspace: Path,
) -> None:
    repository, first = _prepared_frontier_run(workspace)
    root = workspace / "approved-source"
    second = prepare_semantic_index(PrepareSemanticIndexRequest(workspace, root))

    first_result = approve_semantic_execution(
        ApproveSemanticExecutionRequest(workspace, first.revision_id, first.proposal_sha256)
    )
    second_result = approve_semantic_execution(
        ApproveSemanticExecutionRequest(workspace, second.revision_id, second.proposal_sha256)
    )

    assert first_result.run_state is second_result.run_state is SemanticRunState.PREPARED
    assert repository.execution_lock() is None
    with repository._read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_runs WHERE state = 'running'"
        ).fetchone()[0] == 0


def test_v3_status_uses_only_constant_aggregate_queries(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repository, _prepared = _prepared_frontier_run(workspace)
    statements: list[str] = []
    original = SQLiteRepository._read_connection

    @contextmanager
    def traced_read_connection(repository: SQLiteRepository):
        with original(repository) as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(SQLiteRepository, "_read_connection", traced_read_connection)

    status = semantic_index_status(SemanticIndexStatusRequest(workspace))

    selects = [statement.lower() for statement in statements if statement.lstrip().lower().startswith("select")]
    assert len(selects) <= 2
    assert all(
        forbidden not in "\n".join(selects)
        for forbidden in (
            "semantic_v3_units",
            "semantic_v3_dependencies",
            "semantic_v3_deferred",
            "semantic_v3_receipts",
        )
    )
    assert status.integrity_level == "aggregate_bound"


def _proposal(
    run_id: str,
    revision_id: str = "revision-1",
    *,
    parent_checkpoint_root: str | None = None,
) -> SemanticProposalEnvelope:
    proposal = SemanticExecutionProposal(
        run_id=run_id,
        revision_id=revision_id,
        approval_kind="frontier",
        source_approval_sha256=_digest("approval"),
        manifest_sha256=_digest("manifest"),
        policy_sha256=_digest("policy"),
        selector_version="selector-v1",
        catalog_root=_digest("catalog"),
        safe_source_label="approved-source",
        private_context_scope="private",
        authority_root=_digest("authority"),
        plan_sha256=_digest("plan"),
        parent_checkpoint_root=parent_checkpoint_root or _digest("checkpoint"),
        target_projection_contract="projection-v1",
        input_schema_version="input-v1",
        output_schema_version="output-v1",
        validator_version="validator-v1",
        purpose="frontier",
        structural_node_count=1,
        baseline_coverage_counts=(("complete", 1),),
        phase_bindings=tuple(
            SemanticPhaseBinding(
                phase, budget, budget.max_units, budget.max_input_bytes,
                budget.max_wall_seconds,
            )
            for phase, budget in FRONTIER_PHASE_HARD_BUDGETS
        ),
        audit_selector_rule="selector-v1",
        deferred_semantics="defer",
        budget=FRONTIER_BUDGET,
    )
    return SemanticProposalEnvelope(proposal, semantic_proposal_sha256(proposal))


def _bind_revision(
    repository: SQLiteRepository, *, current_checkpoint_root: str | None = None,
) -> None:
    binding = SemanticRevisionBinding(
        revision_id="revision-1",
        manifest_sha256=_digest("manifest"),
        source_policy_sha256=_digest("policy"),
        source_approval_sha256=_digest("approval"),
        catalog_file_sha256=_digest("catalog-file"),
        catalog_logical_root=_digest("catalog"),
        lifecycle_schema_version="v3",
        lifecycle_genesis_root=_digest("genesis"),
        current_checkpoint_root=current_checkpoint_root or _digest("checkpoint"),
    )
    try:
        repository.create_semantic_revision("revision-1", "source-1", _digest("policy"), "v3")
    except RepositoryError:
        pass
    try:
        repository.bind_semantic_v3_revision(binding)
    except RepositoryError:
        pass


def _proposal_material(
    run_id: str,
    *,
    project_deepening: bool = False,
    parent_checkpoint_root: str | None = None,
) -> tuple[SemanticProposalEnvelope, bytes, bytes]:
    envelope = _proposal(run_id, parent_checkpoint_root=parent_checkpoint_root)
    plan = {"run_id": run_id, "units": ["unit-1"]}
    plan_bytes = semantic_canonical_json_bytes(plan)
    proposal = envelope.proposal
    project_authority = (
        {
            "approval_kind": "project_deepening",
            "purpose": "project-deepening",
            "phase_bindings": (
                SemanticPhaseBinding(FrontierPhase.DEEPENING, DEEPENING_BUDGET, 1, 1, 1),
            ),
            "budget": DEEPENING_BUDGET,
            "project_id": "project-1",
            "project_boundary_sha256": _digest("project-boundary"),
            "parent_proposal_sha256": _digest("parent-proposal"),
            "evidence_policy_sha256": _digest("evidence-policy"),
        }
        if project_deepening
        else {}
    )
    proposal = SemanticExecutionProposal(
        **{
            **proposal.__dict__,
            "plan_sha256": sha256(plan_bytes).hexdigest(),
            **project_authority,
        }
    )
    envelope = SemanticProposalEnvelope(proposal, semantic_proposal_sha256(proposal))
    return envelope, semantic_canonical_json_bytes(proposal), plan_bytes


def _stored_proposal(
    repository: SQLiteRepository,
    run_id: str,
    *,
    project_deepening: bool = False,
    current_checkpoint_root: str | None = None,
) -> SemanticProposalEnvelope:
    checkpoint_root = current_checkpoint_root or _digest("checkpoint")
    _bind_revision(repository, current_checkpoint_root=checkpoint_root)
    envelope, proposal_bytes, plan_bytes = _proposal_material(
        run_id,
        project_deepening=project_deepening,
        parent_checkpoint_root=checkpoint_root,
    )
    repository.create_semantic_v3_proposal(
        envelope, proposal_bytes, plan_bytes
    )
    return envelope


def _unit(unit_id: str, *, project_deepening: bool = False) -> SemanticPlannedUnit:
    return SemanticPlannedUnit(
        unit_id,
        FrontierPhase.DEEPENING if project_deepening else FrontierPhase.ORIENTATION,
        (f"node-{unit_id}",),
        (),
        1,
        0,
        "source-1",
    )


def _lifecycle_snapshot(
    repository: SQLiteRepository,
    run_id: str,
    revision_id: str = "revision-1",
) -> dict[str, object]:
    tables = (
        "semantic_v3_proposals",
        "semantic_v3_runs",
        "semantic_v3_units",
        "semantic_v3_dependencies",
        "semantic_v3_leases",
        "semantic_v3_receipts",
        "semantic_v3_aggregates",
        "semantic_v3_checkpoints",
        "semantic_v3_approvals",
        "semantic_v3_deferred",
        "semantic_v3_runtime_deferred",
        "semantic_v3_expansion_cursors",
        "semantic_v3_audit_members",
        "semantic_v3_execution_lock",
    )
    with repository._read_connection() as connection:
        snapshot = {
            table: tuple(
                tuple(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} WHERE run_id = ? ORDER BY rowid", (run_id,)
                )
            )
            for table in tables
        }
        snapshot["revision_checkpoint_root"] = connection.execute(
            "SELECT current_checkpoint_root FROM semantic_v3_revision_bindings WHERE revision_id = ?",
            (revision_id,),
        ).fetchone()[0]
    return snapshot


def _receipt(run_id: str, unit_id: str, sequence: int) -> SemanticReceiptCommit:
    return SemanticReceiptCommit(
        run_id,
        unit_id,
        f"nonce-{sequence}",
        _digest(f"input-{sequence}"),
        _digest(f"output-{sequence}"),
        PackRecordRef(sequence - 1, 1, _digest(f"result-{sequence}")),
        FrontierAction.ACCEPT_BOUNDARY,
        "validator-v1",
        _digest(f"receipt-{sequence}"),
        1.0,
        sequence,
        _digest(f"result-pack-{sequence}"),
    )


def _initialized_run(repository: SQLiteRepository, run_id: str) -> SemanticProposalEnvelope:
    envelope = _stored_proposal(repository, run_id)
    repository.create_semantic_v3_run(envelope)
    repository.add_semantic_v3_units(
        run_id,
        (
            SemanticPlannedUnit(
                "unit-1", FrontierPhase.ORIENTATION, ("node-1",), (), 1, 0, "source-1"
            ),
        ),
    )
    _approve_legacy_fixture_run(repository, run_id, envelope.proposal_sha256)
    return envelope


def _approve_legacy_fixture_run(
    repository: SQLiteRepository,
    run_id: str,
    proposal_sha256: str,
) -> SemanticRunState:
    with repository._connection() as connection:
        connection.execute(
            "UPDATE semantic_v3_runs SET state = 'awaiting_approval' WHERE run_id = ?",
            (run_id,),
        )
    return repository.approve_semantic_v3_run(run_id, proposal_sha256)


def initialized_repository(workspace: Path) -> SQLiteRepository:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    _initialized_run(repository, "run-a")
    repository.claim_semantic_v3_running_slot("run-a")
    return repository


def test_only_one_workspace_run_can_own_execution_lock(workspace: Path) -> None:
    repository = initialized_repository(workspace)

    repository.acquire_semantic_v3_execution("run-a", "unit-1", "nonce-a")

    with pytest.raises(RepositoryError, match="semantic execution is busy"):
        repository.acquire_semantic_v3_execution("run-a", "unit-1", "nonce-b")


def test_only_one_workspace_run_can_be_operationally_running(workspace: Path) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    _initialized_run(repository, "run-a")
    _initialized_run(repository, "run-b")

    repository.claim_semantic_v3_running_slot("run-a")

    with pytest.raises(RepositoryError, match="semantic execution is busy"):
        repository.claim_semantic_v3_running_slot("run-b")
    assert repository.run_state("run-b") is SemanticRunState.PREPARED


def test_proposal_requires_exact_canonical_bytes_and_plan_binding(workspace: Path) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    repository.create_semantic_revision("revision-1", "source-1", _digest("policy"), "v3")
    repository.bind_semantic_v3_revision(
        SemanticRevisionBinding(
            "revision-1", _digest("manifest"), _digest("policy"), _digest("catalog-file"),
            _digest("catalog"), "v3", _digest("genesis"), _digest("checkpoint"), _digest("approval"),
        )
    )
    envelope = _proposal("run-a")
    with pytest.raises(RepositoryError, match="canonical"):
        repository.create_semantic_v3_proposal(envelope, b'{"z":1,"a":2}', b"{}")


def test_receipt_replay_and_invalid_commit_leave_lock_and_state_unchanged(
    workspace: Path,
) -> None:
    repository = initialized_repository(workspace)
    repository.acquire_semantic_v3_execution("run-a", "unit-1", "nonce-a")
    repository.issue_semantic_v3_input("run-a", "nonce-a", _digest("input"), 3)
    receipt = SemanticReceiptCommit(
        run_id="run-a", unit_id="unit-1", lease_nonce="nonce-a", input_sha256=_digest("input"),
        output_sha256=_digest("output"), result=PackRecordRef(0, 7, _digest("result")),
        action=FrontierAction.DEFER_ON_DEMAND, validator_version="validator-v1",
        receipt_sha256=_digest("receipt"), active_seconds=1.5,
    )

    with pytest.raises(RepositoryError, match="result pack"):
        repository.commit_semantic_v3_receipt(
            SemanticReceiptCommit(**{**receipt.__dict__, "result_pack_root": "wrong"})
        )
    assert repository.execution_lock() == ("run-a", "unit-1", "nonce-a", "issued")
    assert repository.receipt_count("run-a") == 0

    committed = SemanticReceiptCommit(
        **{
            **receipt.__dict__,
            "result_pack_length": 7,
            "result_pack_root": _digest("result-pack"),
        }
    )
    repository.commit_semantic_v3_receipt(committed)
    repository.commit_semantic_v3_receipt(committed)
    with pytest.raises(RepositoryError, match="receipt replay conflicts"):
        repository.commit_semantic_v3_receipt(
            SemanticReceiptCommit(**{**committed.__dict__, "output_sha256": _digest("other")})
        )
    assert repository.receipt_count("run-a") == 1


def test_runtime_deferred_is_bounded_and_streamed_with_exact_lineage(workspace: Path) -> None:
    repository = initialized_repository(workspace)
    repository.acquire_semantic_v3_execution("run-a", "unit-1", "nonce-a")
    repository.issue_semantic_v3_input("run-a", "nonce-a", _digest("input"), 3)
    audit = SemanticDeferredAuditRow(
        "node-1", "budget", _digest("node"), '{"offset":1}', 1, "source-1", "complete", 0,
        "selector-v1", _digest("feature"),
    )
    receipt = SemanticReceiptCommit(
        "run-a", "unit-1", "nonce-a", _digest("input"), _digest("output"),
        PackRecordRef(0, 7, _digest("result")), FrontierAction.DEFER_ON_DEMAND,
        "validator-v1", _digest("receipt"), 1.0, 7, _digest("result-pack"),
        (SemanticRuntimeDeferredRow(audit, "unit-1", 1),),
    )
    repository.commit_semantic_v3_receipt(receipt)

    assert list(repository.iter_semantic_v3_runtime_deferred("run-a", fetch_size=1)) == [
        SemanticRuntimeDeferredRow(audit, "unit-1", 1)
    ]
    assert list(repository.iter_semantic_v3_runtime_deferred_audit("run-a", fetch_size=1)) == [audit]


def test_first_checkpoint_rejects_a_root_not_chained_from_revision_genesis(workspace: Path) -> None:
    repository = initialized_repository(workspace)
    with repository._read_connection() as connection:
        aggregate_root = connection.execute(
            "SELECT aggregate_root FROM semantic_v3_aggregates WHERE run_id = 'run-a'"
        ).fetchone()["aggregate_root"]

    with pytest.raises(RepositoryError, match="checkpoint authority"):
        repository.checkpoint_semantic_v3_run(
            SemanticCheckpoint(
                "run-a", 1, 0, 0, "0" * 64, _digest("queue"), aggregate_root,
                "{}", "selector-v1", _digest("not-genesis"), _digest("checkpoint-1"),
            )
        )


def test_audit_freeze_rejects_a_runtime_population_different_from_receipts(
    workspace: Path,
) -> None:
    repository = initialized_repository(workspace)
    repository.acquire_semantic_v3_execution("run-a", "unit-1", "nonce-a")
    repository.issue_semantic_v3_input("run-a", "nonce-a", _digest("input"), 3)
    audit = SemanticDeferredAuditRow(
        "node-1", "budget", _digest("node"), '{"offset":1}', 1, "source-1", "complete", 0,
        "selector-v1", _digest("feature"),
    )
    repository.commit_semantic_v3_receipt(
        SemanticReceiptCommit(
            "run-a", "unit-1", "nonce-a", _digest("input"), _digest("output"),
            PackRecordRef(0, 7, _digest("result")), FrontierAction.DEFER_ON_DEMAND,
            "validator-v1", _digest("receipt"), 1.0, 7, _digest("result-pack"),
            (SemanticRuntimeDeferredRow(audit, "unit-1", 1),),
        )
    )
    with repository._read_connection() as connection:
        aggregate_root = connection.execute(
            "SELECT aggregate_root FROM semantic_v3_aggregates WHERE run_id = 'run-a'"
        ).fetchone()["aggregate_root"]

    with pytest.raises(RepositoryError, match="audit population authority"):
        repository.commit_semantic_v3_audit_freeze(
            SemanticAuditFreeze(
                "run-a", (), (), (),
                SemanticCheckpoint(
                    "run-a", 1, 1, 7, _digest("result-pack"), _digest("queue"),
                    aggregate_root, "{}", "selector-v1", _digest("genesis"),
                    _digest("audit-checkpoint"),
                ),
            )
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("active_seconds", 2.0),
        ("result_pack_length", 8),
        ("result_pack_root", _digest("other-result-pack")),
        ("runtime_deferred", ()),
    ),
)
def test_receipt_replay_requires_the_complete_durable_commit_binding(
    workspace: Path, field: str, value: object,
) -> None:
    repository = initialized_repository(workspace)
    repository.acquire_semantic_v3_execution("run-a", "unit-1", "nonce-a")
    repository.issue_semantic_v3_input("run-a", "nonce-a", _digest("input"), 3)
    audit = SemanticDeferredAuditRow(
        "node-1", "budget", _digest("node"), '{"offset":1}', 1, "source-1", "complete", 0,
        "selector-v1", _digest("feature"),
    )
    receipt = SemanticReceiptCommit(
        "run-a", "unit-1", "nonce-a", _digest("input"), _digest("output"),
        PackRecordRef(0, 7, _digest("result")), FrontierAction.DEFER_ON_DEMAND,
        "validator-v1", _digest("receipt"), 1.0, 7, _digest("result-pack"),
        (SemanticRuntimeDeferredRow(audit, "unit-1", 1),),
    )
    repository.commit_semantic_v3_receipt(receipt)

    with pytest.raises(RepositoryError, match="receipt replay conflicts"):
        repository.commit_semantic_v3_receipt(
            SemanticReceiptCommit(**{**receipt.__dict__, field: value})
        )
    assert repository.receipt_count("run-a") == 1


def test_repeated_unit_addition_cannot_exceed_approved_phase_budget(workspace: Path) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    _initialized_run(repository, "run-a")
    repository.add_semantic_v3_units(
        "run-a",
        tuple(
            SemanticPlannedUnit(
                f"orientation-{index}", FrontierPhase.ORIENTATION, (f"node-{index}",), (),
                1, index, "source-1",
            )
            for index in range(1, 64)
        ),
    )

    with pytest.raises(RepositoryError, match="semantic budget"):
        repository.add_semantic_v3_units(
            "run-a",
            (SemanticPlannedUnit("over-budget", FrontierPhase.ORIENTATION, ("node-x",), (), 1, 99, "source-1"),),
        )
    assert repository.run_state("run-a") is SemanticRunState.PAUSED_BUDGET


def test_audit_freeze_rejects_member_not_in_the_stored_initial_population(workspace: Path) -> None:
    repository = initialized_repository(workspace)
    audit = SemanticDeferredAuditRow(
        "node-1", "budget", _digest("node"), '{"offset":1}', 1, "source-1", "complete", 0,
        "selector-v1", _digest("feature"),
    )
    repository.record_semantic_v3_initial_deferred("run-a", iter((audit,)))
    with repository._read_connection() as connection:
        aggregate_root = connection.execute(
            "SELECT aggregate_root FROM semantic_v3_aggregates WHERE run_id = 'run-a'"
        ).fetchone()["aggregate_root"]

    with pytest.raises(RepositoryError, match="audit membership"):
        repository.commit_semantic_v3_audit_freeze(
            SemanticAuditFreeze(
                "run-a", (audit,), (),
                (SemanticAuditMember(0, "relevance", "foreign", _digest("node"), "audit-unit"),),
                SemanticCheckpoint(
                    "run-a", 1, 0, 0, "0" * 64, _digest("queue"), aggregate_root,
                    "{}", "selector-v1", _digest("genesis"), _digest("arbitrary"),
                ),
            )
        )


def test_checkpoint_rejects_an_arbitrary_root_even_with_correct_order(workspace: Path) -> None:
    repository = initialized_repository(workspace)
    with repository._read_connection() as connection:
        aggregate_root = connection.execute(
            "SELECT aggregate_root FROM semantic_v3_aggregates WHERE run_id = 'run-a'"
        ).fetchone()["aggregate_root"]

    with pytest.raises(RepositoryError, match="checkpoint authority"):
        repository.checkpoint_semantic_v3_run(
            SemanticCheckpoint(
                "run-a", 1, 0, 0, "0" * 64, _digest("queue"), aggregate_root,
                "{}", "selector-v1", _digest("genesis"), _digest("arbitrary"),
            )
        )


def test_receipt_rejects_approval_private_context_drift(workspace: Path) -> None:
    repository = initialized_repository(workspace)
    repository.acquire_semantic_v3_execution("run-a", "unit-1", "nonce-a")
    repository.issue_semantic_v3_input("run-a", "nonce-a", _digest("input"), 3)
    with repository._connection() as connection:
        connection.execute(
            "UPDATE semantic_v3_approvals SET private_context_scope = 'drifted' WHERE run_id = 'run-a'"
        )

    with pytest.raises(RepositoryError, match="receipt authority"):
        repository.commit_semantic_v3_receipt(
            SemanticReceiptCommit(
                "run-a", "unit-1", "nonce-a", _digest("input"), _digest("output"),
                PackRecordRef(0, 7, _digest("result")), FrontierAction.ACCEPT_BOUNDARY,
                "validator-v1", _digest("receipt"), 1.0, 7, _digest("result-pack"),
            )
        )


def test_per_unit_input_and_active_time_budget_pause_without_receipt(workspace: Path) -> None:
    repository = initialized_repository(workspace)
    repository.acquire_semantic_v3_execution("run-a", "unit-1", "nonce-a")
    repository.issue_semantic_v3_input("run-a", "nonce-a", _digest("input"), 262_145)

    assert repository.run_state("run-a") is SemanticRunState.PAUSED_BUDGET
    assert repository.receipt_count("run-a") == 0


@pytest.mark.parametrize("include_valid_unit", (False, True))
def test_queue_rejects_per_unit_input_estimates_without_partial_insert(
    workspace: Path, include_valid_unit: bool,
) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    _initialized_run(repository, "run-a")
    oversized = SemanticPlannedUnit(
        "oversized", FrontierPhase.ORIENTATION, ("node-oversized",), (),
        FRONTIER_BUDGET.max_unit_input_bytes + 1, 0, "source-1",
    )
    units = (
        (SemanticPlannedUnit("valid", FrontierPhase.ORIENTATION, ("node-valid",), (), 1, 0, "source-1"),)
        if include_valid_unit else ()
    ) + (oversized,)
    with repository._read_connection() as connection:
        before_units = connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_units WHERE run_id = 'run-a'"
        ).fetchone()[0]
        before_aggregate = connection.execute(
            "SELECT aggregate_root FROM semantic_v3_aggregates WHERE run_id = 'run-a'"
        ).fetchone()["aggregate_root"]

    with pytest.raises(RepositoryError, match="per-unit input"):
        repository.add_semantic_v3_units("run-a", units)

    with repository._read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_units WHERE run_id = 'run-a'"
        ).fetchone()[0] == before_units
        assert connection.execute(
            "SELECT aggregate_root FROM semantic_v3_aggregates WHERE run_id = 'run-a'"
        ).fetchone()["aggregate_root"] == before_aggregate


@pytest.mark.parametrize("initial_node_digest", (_digest("node"), _digest("different-node")))
def test_audit_freeze_rejects_duplicate_node_across_initial_and_runtime_populations(
    workspace: Path, initial_node_digest: str,
) -> None:
    repository = initialized_repository(workspace)
    repository.acquire_semantic_v3_execution("run-a", "unit-1", "nonce-a")
    repository.issue_semantic_v3_input("run-a", "nonce-a", _digest("input"), 3)
    runtime_audit = SemanticDeferredAuditRow(
        "duplicate-node", "budget", _digest("node"), '{"offset":1}', 1, "source-1",
        "complete", 0, "selector-v1", _digest("feature"),
    )
    repository.commit_semantic_v3_receipt(
        SemanticReceiptCommit(
            "run-a", "unit-1", "nonce-a", _digest("input"), _digest("output"),
            PackRecordRef(0, 7, _digest("result")), FrontierAction.DEFER_ON_DEMAND,
            "validator-v1", _digest("receipt"), 1.0, 7, _digest("result-pack"),
            (SemanticRuntimeDeferredRow(runtime_audit, "unit-1", 1),),
        )
    )
    initial_audit = SemanticDeferredAuditRow(
        "duplicate-node", "catalog", initial_node_digest, '{"offset":2}', 2, "source-1",
        "complete", 0, "selector-v1", _digest("initial-feature"),
    )
    repository.record_semantic_v3_initial_deferred("run-a", iter((initial_audit,)))
    with repository._read_connection() as connection:
        aggregate_root = connection.execute(
            "SELECT aggregate_root FROM semantic_v3_aggregates WHERE run_id = 'run-a'"
        ).fetchone()["aggregate_root"]

    with pytest.raises(RepositoryError, match="duplicate"):
        repository.commit_semantic_v3_audit_freeze(
            SemanticAuditFreeze(
                "run-a", (initial_audit,), (SemanticRuntimeDeferredRow(runtime_audit, "unit-1", 1),), (),
                SemanticCheckpoint(
                    "run-a", 1, 1, 7, _digest("result-pack"), _digest("queue"), aggregate_root,
                    "{}", "selector-v1", _digest("genesis"), _digest("arbitrary"),
                ),
            )
        )
    with repository._read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_audit_members WHERE run_id = 'run-a'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_checkpoints WHERE run_id = 'run-a'"
        ).fetchone()[0] == 0


def test_claim_revalidates_the_canonical_proposal_when_all_projections_are_aligned(
    workspace: Path,
) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    _initialized_run(repository, "run-a")
    with repository._connection() as connection:
        drifted_policy = _digest("aligned-policy-drift")
        connection.execute(
            "UPDATE semantic_v3_proposals SET source_policy_sha256 = ? WHERE run_id = 'run-a'",
            (drifted_policy,),
        )
        connection.execute(
            "UPDATE semantic_v3_approvals SET source_policy_sha256 = ? WHERE run_id = 'run-a'",
            (drifted_policy,),
        )
        connection.execute(
            "UPDATE semantic_v3_revision_bindings SET source_policy_sha256 = ? WHERE revision_id = 'revision-1'",
            (drifted_policy,),
        )

    with pytest.raises(RepositoryError, match="proposal authority"):
        repository.claim_semantic_v3_running_slot("run-a")
    assert repository.run_state("run-a") is SemanticRunState.PREPARED


def test_semantic_v3_authority_schema_durably_records_source_and_project_authority(
    workspace: Path,
) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    expected = {
        "source_approval_sha256", "parent_checkpoint_root", "project_boundary_sha256",
        "parent_proposal_sha256", "evidence_policy_sha256",
    }
    with repository._read_connection() as connection:
        proposal_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(semantic_v3_proposals)")
        }
        approval_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(semantic_v3_approvals)")
        }
        revision_binding_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(semantic_v3_revision_bindings)")
        }
    assert expected <= proposal_columns
    assert expected <= approval_columns
    assert "source_approval_sha256" in revision_binding_columns


def test_claim_rejects_durable_source_approval_drift(workspace: Path) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    _initialized_run(repository, "run-a")
    with repository._connection() as connection:
        connection.execute(
            "UPDATE semantic_v3_approvals SET source_approval_sha256 = ? WHERE run_id = 'run-a'",
            (_digest("drifted-source-approval"),),
        )

    with pytest.raises(RepositoryError, match="receipt authority"):
        repository.claim_semantic_v3_running_slot("run-a")
    assert repository.run_state("run-a") is SemanticRunState.PREPARED


def test_queue_rejects_aligned_project_id_authority_drift_without_mutation(workspace: Path) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    binding = SemanticRevisionBinding(
        "revision-1", _digest("manifest"), _digest("policy"), _digest("catalog-file"),
        _digest("catalog"), "v3", _digest("genesis"), _digest("checkpoint"), _digest("approval"),
    )
    repository.create_semantic_revision("revision-1", "source-1", _digest("policy"), "v3")
    repository.bind_semantic_v3_revision(binding)
    base = _proposal("deepening-run").proposal
    plan = {"run_id": "deepening-run", "units": ["unit-1"]}
    plan_bytes = semantic_canonical_json_bytes(plan)
    proposal = SemanticExecutionProposal(
        **{
            **base.__dict__,
            "approval_kind": "project_deepening",
            "plan_sha256": sha256(plan_bytes).hexdigest(),
            "purpose": "project-deepening",
            "phase_bindings": (
                SemanticPhaseBinding(FrontierPhase.DEEPENING, DEEPENING_BUDGET, 1, 1, 1),
            ),
            "budget": DEEPENING_BUDGET,
            "project_id": "project-1",
            "project_boundary_sha256": _digest("project-boundary"),
            "parent_proposal_sha256": _digest("parent-proposal"),
            "evidence_policy_sha256": _digest("evidence-policy"),
        }
    )
    envelope = SemanticProposalEnvelope(proposal, semantic_proposal_sha256(proposal))
    repository.create_semantic_v3_proposal(
        envelope, semantic_canonical_json_bytes(proposal), plan_bytes,
    )
    repository.create_semantic_v3_run(envelope)
    repository.add_semantic_v3_units(
        "deepening-run",
        (SemanticPlannedUnit("unit-1", FrontierPhase.DEEPENING, ("node-1",), (), 1, 0, "source-1"),),
    )
    _approve_legacy_fixture_run(repository, "deepening-run", envelope.proposal_sha256)
    with repository._connection() as connection:
        connection.execute(
            "UPDATE semantic_v3_runs SET project_id = 'project-drift' WHERE run_id = 'deepening-run'",
        )
        connection.execute(
            "UPDATE semantic_v3_approvals SET project_id = 'project-drift' WHERE run_id = 'deepening-run'",
        )
    with repository._read_connection() as connection:
        before = connection.execute(
            "SELECT COUNT(*) AS unit_count, (SELECT aggregate_root FROM semantic_v3_aggregates WHERE run_id = 'deepening-run') AS aggregate_root FROM semantic_v3_units WHERE run_id = 'deepening-run'"
        ).fetchone()

    with pytest.raises(RepositoryError, match="proposal authority"):
        repository.add_semantic_v3_units(
            "deepening-run",
            (SemanticPlannedUnit("new-unit", FrontierPhase.DEEPENING, ("node-new",), (), 1, 0, "source-1"),),
        )
    with repository._read_connection() as connection:
        after = connection.execute(
            "SELECT COUNT(*) AS unit_count, (SELECT aggregate_root FROM semantic_v3_aggregates WHERE run_id = 'deepening-run') AS aggregate_root FROM semantic_v3_units WHERE run_id = 'deepening-run'"
        ).fetchone()
    assert tuple(after) == tuple(before)


def test_claim_rejects_revision_source_approval_drift(workspace: Path) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    _initialized_run(repository, "run-a")
    with repository._connection() as connection:
        connection.execute(
            "UPDATE semantic_v3_revision_bindings SET source_approval_sha256 = ? WHERE revision_id = 'revision-1'",
            (_digest("drifted-revision-source-approval"),),
        )

    with pytest.raises(RepositoryError, match="receipt authority"):
        repository.claim_semantic_v3_running_slot("run-a")
    assert repository.run_state("run-a") is SemanticRunState.PREPARED


def test_queue_uses_canonical_budget_after_projection_is_relaxed(workspace: Path) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    _initialized_run(repository, "run-a")
    with repository._connection() as connection:
        proposal = connection.execute(
            "SELECT total_budget_json, phase_bindings_json FROM semantic_v3_proposals WHERE run_id = 'run-a'"
        ).fetchone()
        total = json.loads(proposal["total_budget_json"])
        phases = json.loads(proposal["phase_bindings_json"])
        total["max_unit_input_bytes"] = FRONTIER_BUDGET.max_unit_input_bytes + 1
        for phase in phases:
            if phase["phase"] == FrontierPhase.ORIENTATION.value:
                phase["hard_budget"]["max_unit_input_bytes"] = (
                    FRONTIER_BUDGET.max_unit_input_bytes + 1
                )
        connection.execute(
            """UPDATE semantic_v3_proposals
               SET total_budget_json = ?, phase_bindings_json = ? WHERE run_id = 'run-a'""",
            (
                semantic_canonical_json_bytes(total).decode("utf-8"),
                semantic_canonical_json_bytes(phases).decode("utf-8"),
            ),
        )
    with repository._read_connection() as connection:
        before = connection.execute(
            "SELECT COUNT(*) AS unit_count, (SELECT aggregate_root FROM semantic_v3_aggregates WHERE run_id = 'run-a') AS aggregate_root FROM semantic_v3_units WHERE run_id = 'run-a'"
        ).fetchone()

    with pytest.raises(RepositoryError, match="proposal authority"):
        repository.add_semantic_v3_units(
            "run-a",
            (SemanticPlannedUnit(
                "oversized", FrontierPhase.ORIENTATION, ("node-oversized",), (),
                FRONTIER_BUDGET.max_unit_input_bytes + 1, 0, "source-1",
            ),),
        )

    with repository._read_connection() as connection:
        after = connection.execute(
            "SELECT COUNT(*) AS unit_count, (SELECT aggregate_root FROM semantic_v3_aggregates WHERE run_id = 'run-a') AS aggregate_root FROM semantic_v3_units WHERE run_id = 'run-a'"
        ).fetchone()
    assert tuple(after) == tuple(before)


def test_queue_rejects_aligned_source_authority_drift_without_mutation(workspace: Path) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    _initialized_run(repository, "run-a")
    with repository._connection() as connection:
        drifted = _digest("aligned-source-approval-drift")
        connection.execute(
            "UPDATE semantic_v3_proposals SET source_approval_sha256 = ? WHERE run_id = 'run-a'",
            (drifted,),
        )
        connection.execute(
            "UPDATE semantic_v3_approvals SET source_approval_sha256 = ? WHERE run_id = 'run-a'",
            (drifted,),
        )
    with repository._read_connection() as connection:
        before = connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_units WHERE run_id = 'run-a'"
        ).fetchone()[0]

    with pytest.raises(RepositoryError, match="proposal authority"):
        repository.add_semantic_v3_units(
            "run-a",
            (SemanticPlannedUnit("new-unit", FrontierPhase.ORIENTATION, ("node-new",), (), 1, 0, "source-1"),),
        )

    assert repository.receipt_count("run-a") == 0
    with repository._read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_units WHERE run_id = 'run-a'"
        ).fetchone()[0] == before


@pytest.mark.parametrize(
    ("binding_column", "drift_label"),
    (
        ("source_approval_sha256", "run-create-source-approval-drift"),
        ("manifest_sha256", "run-create-manifest-drift"),
        ("source_policy_sha256", "run-create-policy-drift"),
        ("catalog_logical_root", "run-create-catalog-drift"),
    ),
)
def test_run_creation_rejects_revision_authority_drift_without_durable_rows(
    workspace: Path, binding_column: str, drift_label: str,
) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    envelope = _stored_proposal(repository, "run-a")
    with repository._connection() as connection:
        connection.execute(
            f"UPDATE semantic_v3_revision_bindings SET {binding_column} = ? WHERE revision_id = 'revision-1'",
            (_digest(drift_label),),
        )

    with pytest.raises(RepositoryError, match="semantic run proposal authority"):
        repository.create_semantic_v3_run(envelope)

    with repository._read_connection() as connection:
        counts = tuple(
            connection.execute(
                """SELECT
                       (SELECT COUNT(*) FROM semantic_v3_runs WHERE run_id = 'run-a'),
                       (SELECT COUNT(*) FROM semantic_v3_aggregates WHERE run_id = 'run-a'),
                       (SELECT COUNT(*) FROM semantic_v3_deferred WHERE run_id = 'run-a')"""
            ).fetchone()
        )
        proposal_state = connection.execute(
            "SELECT state FROM semantic_v3_proposals WHERE run_id = 'run-a'"
        ).fetchone()[0]
    assert counts == (0, 0, 0)
    assert proposal_state == "proposed"


def test_run_creation_rejects_aligned_proposal_projection_drift_without_durable_rows(
    workspace: Path,
) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    envelope = _stored_proposal(repository, "run-a")
    aligned_drift = _digest("aligned-run-create-policy-drift")
    with repository._connection() as connection:
        connection.execute(
            "UPDATE semantic_v3_proposals SET source_policy_sha256 = ? WHERE run_id = 'run-a'",
            (aligned_drift,),
        )
        connection.execute(
            "UPDATE semantic_v3_revision_bindings SET source_policy_sha256 = ? WHERE revision_id = 'revision-1'",
            (aligned_drift,),
        )

    with pytest.raises(RepositoryError, match="semantic run proposal authority"):
        repository.create_semantic_v3_run(envelope)

    with repository._read_connection() as connection:
        assert tuple(
            connection.execute(
                """SELECT
                       (SELECT COUNT(*) FROM semantic_v3_runs WHERE run_id = 'run-a'),
                       (SELECT COUNT(*) FROM semantic_v3_aggregates WHERE run_id = 'run-a'),
                       (SELECT COUNT(*) FROM semantic_v3_deferred WHERE run_id = 'run-a')"""
            ).fetchone()
        ) == (0, 0, 0)


@pytest.mark.parametrize(
    ("proposal_column", "drift_value"),
    (
        ("project_id", "project-drift"),
        ("project_boundary_sha256", _digest("project-boundary-drift")),
        ("parent_proposal_sha256", _digest("parent-proposal-drift")),
        ("evidence_policy_sha256", _digest("evidence-policy-drift")),
    ),
)
def test_run_creation_rejects_deepening_projection_drift_without_durable_rows(
    workspace: Path, proposal_column: str, drift_value: str,
) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    envelope = _stored_proposal(repository, "deepening-run", project_deepening=True)
    with repository._connection() as connection:
        connection.execute(
            f"UPDATE semantic_v3_proposals SET {proposal_column} = ? WHERE run_id = 'deepening-run'",
            (drift_value,),
        )

    with pytest.raises(RepositoryError, match="semantic run proposal authority"):
        repository.create_semantic_v3_run(envelope)

    with repository._read_connection() as connection:
        assert tuple(
            connection.execute(
                """SELECT
                       (SELECT COUNT(*) FROM semantic_v3_runs WHERE run_id = 'deepening-run'),
                       (SELECT COUNT(*) FROM semantic_v3_aggregates WHERE run_id = 'deepening-run'),
                       (SELECT COUNT(*) FROM semantic_v3_deferred WHERE run_id = 'deepening-run')"""
            ).fetchone()
        ) == (0, 0, 0)


@pytest.mark.parametrize(
    ("binding_column", "drift_label"),
    (
        ("source_approval_sha256", "approval-source-approval-drift"),
        ("manifest_sha256", "approval-manifest-drift"),
        ("source_policy_sha256", "approval-policy-drift"),
        ("catalog_logical_root", "approval-catalog-drift"),
    ),
)
def test_approval_rejects_revision_authority_drift_without_state_change(
    workspace: Path, binding_column: str, drift_label: str,
) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    envelope = _stored_proposal(repository, "run-a")
    repository.create_semantic_v3_run(envelope)
    with repository._connection() as connection:
        connection.execute(
            f"UPDATE semantic_v3_revision_bindings SET {binding_column} = ? WHERE revision_id = 'revision-1'",
            (_digest(drift_label),),
        )

    with pytest.raises(RepositoryError, match="authority"):
        repository.approve_semantic_v3_run("run-a", envelope.proposal_sha256)

    with repository._read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_approvals WHERE run_id = 'run-a'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT state FROM semantic_v3_proposals WHERE run_id = 'run-a'"
        ).fetchone()[0] == "proposed"
        assert connection.execute(
            "SELECT state FROM semantic_v3_runs WHERE run_id = 'run-a'"
        ).fetchone()[0] == SemanticRunState.PREPARED.value


def test_approval_rejects_aligned_proposal_projection_drift_without_state_change(
    workspace: Path,
) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    envelope = _stored_proposal(repository, "run-a")
    repository.create_semantic_v3_run(envelope)
    aligned_drift = _digest("aligned-approval-policy-drift")
    with repository._connection() as connection:
        connection.execute(
            "UPDATE semantic_v3_proposals SET source_policy_sha256 = ? WHERE run_id = 'run-a'",
            (aligned_drift,),
        )
        connection.execute(
            "UPDATE semantic_v3_revision_bindings SET source_policy_sha256 = ? WHERE revision_id = 'revision-1'",
            (aligned_drift,),
        )

    with pytest.raises(RepositoryError, match="authority"):
        repository.approve_semantic_v3_run("run-a", envelope.proposal_sha256)

    with repository._read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_approvals WHERE run_id = 'run-a'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT state FROM semantic_v3_proposals WHERE run_id = 'run-a'"
        ).fetchone()[0] == "proposed"
        assert connection.execute(
            "SELECT state FROM semantic_v3_runs WHERE run_id = 'run-a'"
        ).fetchone()[0] == SemanticRunState.PREPARED.value


@pytest.mark.parametrize(
    ("table", "column", "drift_value"),
    (
        ("semantic_v3_runs", "project_id", "approval-project-id-drift"),
        (
            "semantic_v3_proposals",
            "project_boundary_sha256",
            _digest("approval-project-boundary-drift"),
        ),
    ),
)
def test_approval_rejects_deepening_projection_drift_without_state_change(
    workspace: Path, table: str, column: str, drift_value: str,
) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    envelope = _stored_proposal(repository, "deepening-run", project_deepening=True)
    repository.create_semantic_v3_run(envelope)
    with repository._connection() as connection:
        connection.execute(
            f"UPDATE {table} SET {column} = ? WHERE run_id = 'deepening-run'",
            (drift_value,),
        )

    with pytest.raises(RepositoryError, match="authority"):
        repository.approve_semantic_v3_run("deepening-run", envelope.proposal_sha256)

    with repository._read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_approvals WHERE run_id = 'deepening-run'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT state FROM semantic_v3_proposals WHERE run_id = 'deepening-run'"
        ).fetchone()[0] == "proposed"
        assert connection.execute(
            "SELECT state FROM semantic_v3_runs WHERE run_id = 'deepening-run'"
        ).fetchone()[0] == SemanticRunState.PREPARED.value


@pytest.mark.parametrize("project_deepening", (False, True), ids=("frontier", "deepening"))
@pytest.mark.parametrize(
    "boundary",
    ("proposal", "run", "approval", "queue", "claim", "acquire", "issue", "receipt"),
)
def test_current_checkpoint_drift_fails_closed_at_every_authority_boundary(
    workspace: Path, project_deepening: bool, boundary: str,
) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    run_id = "deepening-run" if project_deepening else "run-a"

    if boundary == "proposal":
        _bind_revision(repository)
        envelope, proposal_bytes, plan_bytes = _proposal_material(
            run_id, project_deepening=project_deepening,
        )
    else:
        envelope = _stored_proposal(
            repository, run_id, project_deepening=project_deepening,
        )
        proposal_bytes = plan_bytes = b""
        if boundary in {"run", "approval", "queue", "claim", "acquire", "issue", "receipt"}:
            if boundary != "run":
                repository.create_semantic_v3_run(envelope)
        if boundary in {"claim", "acquire", "issue", "receipt"}:
            repository.add_semantic_v3_units(
                run_id, (_unit("unit-1", project_deepening=project_deepening),),
            )
            _approve_legacy_fixture_run(repository, run_id, envelope.proposal_sha256)
        if boundary in {"acquire", "issue", "receipt"}:
            repository.claim_semantic_v3_running_slot(run_id)
        if boundary in {"issue", "receipt"}:
            repository.acquire_semantic_v3_execution(run_id, "unit-1", "nonce-1")
        if boundary == "receipt":
            repository.issue_semantic_v3_input(
                run_id, "nonce-1", _digest("input-1"), 1,
            )

    with repository._connection() as connection:
        connection.execute(
            "UPDATE semantic_v3_revision_bindings SET current_checkpoint_root = ? WHERE revision_id = 'revision-1'",
            (_digest("external-checkpoint-drift"),),
        )
    before = _lifecycle_snapshot(repository, run_id)

    with pytest.raises(RepositoryError, match="authority"):
        if boundary == "proposal":
            repository.create_semantic_v3_proposal(envelope, proposal_bytes, plan_bytes)
        elif boundary == "run":
            repository.create_semantic_v3_run(envelope)
        elif boundary == "approval":
            repository.approve_semantic_v3_run(run_id, envelope.proposal_sha256)
        elif boundary == "queue":
            repository.add_semantic_v3_units(
                run_id, (_unit("unit-1", project_deepening=project_deepening),),
            )
        elif boundary == "claim":
            repository.claim_semantic_v3_running_slot(run_id)
        elif boundary == "acquire":
            repository.acquire_semantic_v3_execution(run_id, "unit-1", "nonce-1")
        elif boundary == "issue":
            repository.issue_semantic_v3_input(
                run_id, "nonce-1", _digest("input-1"), 1,
            )
        else:
            repository.commit_semantic_v3_receipt(_receipt(run_id, "unit-1", 1))

    assert _lifecycle_snapshot(repository, run_id) == before


@pytest.mark.parametrize("project_deepening", (False, True), ids=("frontier", "deepening"))
@pytest.mark.parametrize("parent_checkpoint_root", (None, ""), ids=("null", "empty"))
def test_parent_checkpoint_root_is_required_for_every_proposal_kind(
    project_deepening: bool, parent_checkpoint_root: object,
) -> None:
    proposal = _proposal_material(
        "deepening-run" if project_deepening else "run-a",
        project_deepening=project_deepening,
    )[0].proposal

    with pytest.raises(ValueError, match="parent checkpoint"):
        SemanticExecutionProposal(
            **{**proposal.__dict__, "parent_checkpoint_root": parent_checkpoint_root}
        )


def test_frontier_and_deepening_project_authority_nullability_is_exact() -> None:
    frontier = _proposal_material("run-a")[0].proposal
    assert (
        frontier.project_id,
        frontier.project_boundary_sha256,
        frontier.parent_proposal_sha256,
        frontier.evidence_policy_sha256,
    ) == (None, None, None, None)
    with pytest.raises(ValueError, match="frontier proposal"):
        SemanticExecutionProposal(**{**frontier.__dict__, "project_id": "project-1"})

    deepening = _proposal_material("deepening-run", project_deepening=True)[0].proposal
    for field in (
        "project_id",
        "project_boundary_sha256",
        "parent_proposal_sha256",
        "evidence_policy_sha256",
    ):
        with pytest.raises(ValueError, match="deepening proposal"):
            SemanticExecutionProposal(**{**deepening.__dict__, field: None})


def test_first_run_checkpoint_chains_from_immutable_proposal_parent(
    workspace: Path,
) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    envelope = _initialized_run(repository, "run-a")
    with repository._read_connection() as connection:
        aggregate_root = connection.execute(
            "SELECT aggregate_root FROM semantic_v3_aggregates WHERE run_id = 'run-a'"
        ).fetchone()[0]
    checkpoint = SemanticCheckpoint(
        "run-a",
        1,
        0,
        0,
        "0" * 64,
        _digest("queue"),
        aggregate_root,
        "{}",
        "selector-v1",
        envelope.proposal.parent_checkpoint_root,
        "0" * 64,
    )
    checkpoint = SemanticCheckpoint(
        **{
            **checkpoint.__dict__,
            "checkpoint_root": repository._semantic_v3_checkpoint_root(checkpoint),
        }
    )

    repository.checkpoint_semantic_v3_run(checkpoint)
    repository.claim_semantic_v3_running_slot("run-a")

    with repository._read_connection() as connection:
        assert connection.execute(
            "SELECT current_checkpoint_root FROM semantic_v3_revision_bindings WHERE revision_id = 'revision-1'"
        ).fetchone()[0] == checkpoint.checkpoint_root
    assert repository.run_state("run-a") is SemanticRunState.RUNNING


def test_checkpoint_cadence_advances_current_root_without_blocking_the_same_run(
    workspace: Path,
) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    genesis = _digest("genesis")
    envelope = _stored_proposal(repository, "run-a", current_checkpoint_root=genesis)
    repository.create_semantic_v3_run(envelope)
    repository.add_semantic_v3_units(
        "run-a", tuple(_unit(f"unit-{sequence}") for sequence in range(1, 18)),
    )
    _approve_legacy_fixture_run(repository, "run-a", envelope.proposal_sha256)
    repository.claim_semantic_v3_running_slot("run-a")

    for sequence in range(1, 18):
        unit_id = f"unit-{sequence}"
        repository.acquire_semantic_v3_execution("run-a", unit_id, f"nonce-{sequence}")
        repository.issue_semantic_v3_input(
            "run-a", f"nonce-{sequence}", _digest(f"input-{sequence}"), 1,
        )
        repository.commit_semantic_v3_receipt(_receipt("run-a", unit_id, sequence))

    with repository._read_connection() as connection:
        checkpoint = connection.execute(
            "SELECT checkpoint_root FROM semantic_v3_checkpoints WHERE run_id = 'run-a'"
        ).fetchone()[0]
        current = connection.execute(
            "SELECT current_checkpoint_root FROM semantic_v3_revision_bindings WHERE revision_id = 'revision-1'"
        ).fetchone()[0]
    assert checkpoint == current
    assert checkpoint != envelope.proposal.parent_checkpoint_root
    assert repository.receipt_count("run-a") == 17


def test_irregular_checkpoint_sequences_remain_monotonic_across_automatic_cadences(
    workspace: Path,
) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    genesis = _digest("genesis")
    envelope = _stored_proposal(repository, "run-a", current_checkpoint_root=genesis)
    repository.create_semantic_v3_run(envelope)
    repository.add_semantic_v3_units(
        "run-a", tuple(_unit(f"unit-{sequence}") for sequence in range(1, 33)),
    )
    _approve_legacy_fixture_run(repository, "run-a", envelope.proposal_sha256)
    repository.claim_semantic_v3_running_slot("run-a")

    for sequence in range(1, 8):
        unit_id = f"unit-{sequence}"
        repository.acquire_semantic_v3_execution("run-a", unit_id, f"nonce-{sequence}")
        repository.issue_semantic_v3_input(
            "run-a", f"nonce-{sequence}", _digest(f"input-{sequence}"), 1,
        )
        repository.commit_semantic_v3_receipt(_receipt("run-a", unit_id, sequence))

    repository.pause_semantic_v3_run("run-a", "manual checkpoint")
    with repository._read_connection() as connection:
        aggregate_root = connection.execute(
            "SELECT aggregate_root FROM semantic_v3_aggregates WHERE run_id = 'run-a'"
        ).fetchone()[0]
    irregular = SemanticCheckpoint(
        "run-a", 1, 7, 7, _digest("result-pack-7"), "0" * 64,
        aggregate_root, "{}", "selector-v1", envelope.proposal.parent_checkpoint_root,
        "0" * 64,
    )
    irregular = SemanticCheckpoint(
        **{
            **irregular.__dict__,
            "checkpoint_root": repository._semantic_v3_checkpoint_root(irregular),
        }
    )
    repository.checkpoint_semantic_v3_run(irregular)
    repository.claim_semantic_v3_running_slot("run-a")

    for sequence in range(8, 33):
        unit_id = f"unit-{sequence}"
        repository.acquire_semantic_v3_execution("run-a", unit_id, f"nonce-{sequence}")
        repository.issue_semantic_v3_input(
            "run-a", f"nonce-{sequence}", _digest(f"input-{sequence}"), 1,
        )
        repository.commit_semantic_v3_receipt(_receipt("run-a", unit_id, sequence))

    with repository._read_connection() as connection:
        receipt_rows = connection.execute(
            """SELECT sequence, unit_id, input_bytes, active_seconds
               FROM semantic_v3_receipts WHERE run_id = 'run-a' ORDER BY sequence"""
        ).fetchall()
        checkpoint_rows = connection.execute(
            """SELECT sequence, receipt_sequence, result_pack_length, result_pack_root,
                      queue_root, aggregate_root, resume_cursor_json,
                      audit_ordered_set_sha256, audit_population_sha256, selector_version,
                      previous_checkpoint_root, checkpoint_root
               FROM semantic_v3_checkpoints WHERE run_id = 'run-a' ORDER BY sequence"""
        ).fetchall()
        run_usage = tuple(connection.execute(
            """SELECT used_units, used_input_bytes, used_active_seconds,
                      result_pack_length, result_pack_root
               FROM semantic_v3_runs WHERE run_id = 'run-a'"""
        ).fetchone())
        aggregate_usage = tuple(connection.execute(
            """SELECT validated_count, used_units, used_input_bytes, used_active_seconds
               FROM semantic_v3_aggregates WHERE run_id = 'run-a'"""
        ).fetchone())
        lease_count = connection.execute(
            "SELECT COUNT(*) FROM semantic_v3_leases WHERE run_id = 'run-a'"
        ).fetchone()[0]
        current_checkpoint_root = connection.execute(
            """SELECT current_checkpoint_root FROM semantic_v3_revision_bindings
               WHERE revision_id = 'revision-1'"""
        ).fetchone()[0]

    assert [tuple(row) for row in receipt_rows] == [
        (sequence, f"unit-{sequence}", 1, 1.0) for sequence in range(1, 33)
    ]
    assert run_usage == (32, 32, 32.0, 32, _digest("result-pack-32"))
    assert aggregate_usage == (32, 32, 32, 32.0)
    assert repository.execution_lock() is None
    assert lease_count == 0
    assert [row[0] for row in checkpoint_rows] == [1, 2, 3]
    assert [row[1] for row in checkpoint_rows] == [7, 16, 32]

    previous_root = envelope.proposal.parent_checkpoint_root
    for row in checkpoint_rows:
        assert row[10] == previous_root
        payload = {
            "run_id": "run-a",
            "sequence": row[0],
            "receipt_sequence": row[1],
            "result_pack_length": row[2],
            "result_pack_root": row[3],
            "queue_root": row[4],
            "aggregate_root": row[5],
            "resume_cursor_json": row[6],
            "audit_ordered_set_sha256": row[7],
            "audit_population_sha256": row[8],
            "selector_version": row[9],
            "previous_checkpoint_root": row[10],
        }
        assert row[11] == sha256(
            b"portfolio-maker-semantic-v3-checkpoint\0"
            + semantic_canonical_json_bytes(payload)
        ).hexdigest()
        previous_root = row[11]
    assert current_checkpoint_root == previous_root


def test_external_checkpoint_drift_after_cadence_preserves_issued_receipt_state(
    workspace: Path,
) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    genesis = _digest("genesis")
    envelope = _stored_proposal(repository, "run-a", current_checkpoint_root=genesis)
    repository.create_semantic_v3_run(envelope)
    repository.add_semantic_v3_units(
        "run-a", tuple(_unit(f"unit-{sequence}") for sequence in range(1, 18)),
    )
    _approve_legacy_fixture_run(repository, "run-a", envelope.proposal_sha256)
    repository.claim_semantic_v3_running_slot("run-a")
    for sequence in range(1, 17):
        unit_id = f"unit-{sequence}"
        repository.acquire_semantic_v3_execution("run-a", unit_id, f"nonce-{sequence}")
        repository.issue_semantic_v3_input(
            "run-a", f"nonce-{sequence}", _digest(f"input-{sequence}"), 1,
        )
        repository.commit_semantic_v3_receipt(_receipt("run-a", unit_id, sequence))
    repository.acquire_semantic_v3_execution("run-a", "unit-17", "nonce-17")
    repository.issue_semantic_v3_input("run-a", "nonce-17", _digest("input-17"), 1)
    with repository._connection() as connection:
        connection.execute(
            "UPDATE semantic_v3_revision_bindings SET current_checkpoint_root = ? WHERE revision_id = 'revision-1'",
            (_digest("post-checkpoint-external-drift"),),
        )
    before = _lifecycle_snapshot(repository, "run-a")

    with pytest.raises(RepositoryError, match="receipt authority"):
        repository.commit_semantic_v3_receipt(_receipt("run-a", "unit-17", 17))

    assert _lifecycle_snapshot(repository, "run-a") == before


@pytest.mark.parametrize("project_deepening", (False, True), ids=("frontier", "deepening"))
def test_exact_receipt_replay_revalidates_current_checkpoint_authority(
    workspace: Path, project_deepening: bool,
) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    run_id = "deepening-run" if project_deepening else "run-a"
    envelope = _stored_proposal(
        repository, run_id, project_deepening=project_deepening,
    )
    repository.create_semantic_v3_run(envelope)
    repository.add_semantic_v3_units(
        run_id, (_unit("unit-1", project_deepening=project_deepening),),
    )
    _approve_legacy_fixture_run(repository, run_id, envelope.proposal_sha256)
    repository.claim_semantic_v3_running_slot(run_id)
    repository.acquire_semantic_v3_execution(run_id, "unit-1", "nonce-1")
    repository.issue_semantic_v3_input(run_id, "nonce-1", _digest("input-1"), 1)
    receipt = _receipt(run_id, "unit-1", 1)
    repository.commit_semantic_v3_receipt(receipt)
    with repository._connection() as connection:
        connection.execute(
            "UPDATE semantic_v3_revision_bindings SET current_checkpoint_root = ? WHERE revision_id = 'revision-1'",
            (_digest("replay-checkpoint-drift"),),
        )
    before = _lifecycle_snapshot(repository, run_id)

    with pytest.raises(RepositoryError, match="receipt authority"):
        repository.commit_semantic_v3_receipt(receipt)

    assert _lifecycle_snapshot(repository, run_id) == before
