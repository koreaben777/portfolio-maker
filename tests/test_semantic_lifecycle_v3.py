from __future__ import annotations

from hashlib import sha256
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
from portfolio_maker.infrastructure.sqlite_repository import RepositoryError, SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def _digest(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _proposal(run_id: str, revision_id: str = "revision-1") -> SemanticProposalEnvelope:
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
        parent_checkpoint_root=_digest("checkpoint"),
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


def _initialized_run(repository: SQLiteRepository, run_id: str) -> SemanticProposalEnvelope:
    binding = SemanticRevisionBinding(
        revision_id="revision-1",
        manifest_sha256=_digest("manifest"),
        source_policy_sha256=_digest("policy"),
        catalog_file_sha256=_digest("catalog-file"),
        catalog_logical_root=_digest("catalog"),
        lifecycle_schema_version="v3",
        lifecycle_genesis_root=_digest("genesis"),
        current_checkpoint_root=_digest("checkpoint"),
    )
    try:
        repository.create_semantic_revision("revision-1", "source-1", _digest("policy"), "v3")
    except RepositoryError:
        pass
    try:
        repository.bind_semantic_v3_revision(binding)
    except RepositoryError:
        pass
    envelope = _proposal(run_id)
    plan = {"run_id": run_id, "units": ["unit-1"]}
    plan_bytes = semantic_canonical_json_bytes(plan)
    proposal = envelope.proposal
    proposal = SemanticExecutionProposal(
        **{**proposal.__dict__, "plan_sha256": sha256(plan_bytes).hexdigest()}
    )
    envelope = SemanticProposalEnvelope(proposal, semantic_proposal_sha256(proposal))
    repository.create_semantic_v3_proposal(
        envelope, semantic_canonical_json_bytes(proposal), plan_bytes
    )
    repository.create_semantic_v3_run(envelope)
    repository.add_semantic_v3_units(
        run_id,
        (
            SemanticPlannedUnit(
                "unit-1", FrontierPhase.ORIENTATION, ("node-1",), (), 1, 0, "source-1"
            ),
        ),
    )
    repository.approve_semantic_v3_run(run_id, envelope.proposal_sha256)
    return envelope


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
            _digest("catalog"), "v3", _digest("genesis"), _digest("checkpoint"),
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
    assert expected <= proposal_columns
    assert expected <= approval_columns


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


def test_claim_rejects_durable_project_authority_drift(workspace: Path) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    binding = SemanticRevisionBinding(
        "revision-1", _digest("manifest"), _digest("policy"), _digest("catalog-file"),
        _digest("catalog"), "v3", _digest("genesis"), _digest("checkpoint"),
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
    repository.approve_semantic_v3_run("deepening-run", envelope.proposal_sha256)
    with repository._connection() as connection:
        connection.execute(
            "UPDATE semantic_v3_approvals SET project_boundary_sha256 = ? WHERE run_id = 'deepening-run'",
            (_digest("drifted-boundary"),),
        )

    with pytest.raises(RepositoryError, match="receipt authority"):
        repository.claim_semantic_v3_running_slot("deepening-run")
    assert repository.run_state("deepening-run") is SemanticRunState.PREPARED
