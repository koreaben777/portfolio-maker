"""Opt-in early resource gate; synthetic data only, not full-home acceptance.

Run with PM_SEMANTIC_EARLY_SCALE=1. The child is a fresh interpreter, not an
OS page-cache flush. Repeated next is issued-input replay; repeated validate is exact receipt
replay. next_distinct issues a different unit; audit/full are separate phases.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

import pytest

from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.models import (
    ApproveSemanticExecutionRequest,
    NextSemanticWorkV3Request,
    PrepareSemanticIndexRequest,
    SemanticIndexStatusV3Request,
    ValidateSemanticWorkV3Request,
)
from portfolio_maker.application.semantic_index import prepare_semantic_index
from portfolio_maker.application.semantic_lifecycle_v3 import (
    approve_semantic_execution,
    next_semantic_work_v3,
    semantic_index_status_v3,
    validate_semantic_work_v3,
)
from portfolio_maker.workspace import WorkspacePaths


def _prepare_synthetic_catalog(workspace: Path, node_count: int):
    """Two independent project lanes, retaining an exact total node count."""
    root = workspace / "synthetic-source"
    root.mkdir(parents=True)
    for lane in ("project-a", "project-b"):
        project = root / lane
        project.mkdir()
        (project / "README.md").write_text("# Synthetic project\nIndependent executable library.\n", encoding="utf-8")
        (project / "pyproject.toml").write_text(f"[project]\nname = '{lane}'\n", encoding="utf-8")
        (project / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    # root + two directories + six project files = nine nodes.
    for ordinal in range(node_count - 9):
        project = root / ("project-a" if ordinal % 2 == 0 else "project-b")
        (project / f"module-{ordinal:05d}.py").write_text(
            f"def task_{ordinal}():\n    return {ordinal}\n", encoding="utf-8"
        )
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [root.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    prepared = prepare_semantic_index(PrepareSemanticIndexRequest(workspace, root))
    manifest = json.loads(
        (paths.semantic_revision_raw_dir(prepared.revision_id) / "manifest.v3.json")
        .read_text(encoding="utf-8")
    )
    assert manifest["structural_node_count"] == node_count
    approve_semantic_execution(ApproveSemanticExecutionRequest(
        workspace, prepared.revision_id, prepared.proposal_sha256
    ))
    return prepared


def _measure_child(workspace: Path, revision_id: str, run_id: str) -> dict:
    """Observe real entry points without replacing authority decisions."""
    import portfolio_maker.infrastructure.semantic_catalog as catalog_module
    import portfolio_maker.infrastructure.semantic_pack as pack_module
    import sqlite3
    from portfolio_maker.infrastructure.semantic_auth_index import AuthIndexReader
    from test_semantic_lifecycle_v3 import _write_valid_v3_exchange_output

    paths = WorkspacePaths.from_root(workspace)
    revision = paths.semantic_revision_raw_dir(revision_id)
    catalog_path = revision / "catalog.sqlite"
    structural_path = revision / "packs" / "structural.pm-pack"
    identities = {}
    for label, path in (("catalog", catalog_path), ("structural", structural_path)):
        stat = path.stat()
        identities[(stat.st_dev, stat.st_ino)] = label
    metrics = {}
    original_read, original_pread = os.read, os.pread
    original_rows = catalog_module._authoritative_rows
    original_node = catalog_module._node_from_row
    original_connect = sqlite3.connect
    original_decode = pack_module._decode_canonical_payload
    original_verify_prefix = pack_module._verify_pack_prefix
    original_query = AuthIndexReader._query
    original_page = AuthIndexReader.page

    def add_bytes(fd, data):
        stat = os.fstat(fd)
        label = identities.get((stat.st_dev, stat.st_ino))
        if label:
            metrics[f"{label}_explicit_read_bytes"] += len(data)
        return data

    def read(fd, size):
        return add_bytes(fd, original_read(fd, size))

    def pread(fd, size, offset):
        return add_bytes(fd, original_pread(fd, size, offset))

    def authority_rows(*args, **kwargs):
        for row in original_rows(*args, **kwargs):
            metrics["catalog_authority_rows"] += 1
            yield row

    def decode_node(row):
        metrics["catalog_node_decodes"] += 1
        return original_node(row)

    def decode_pack(payload):
        metrics["pack_record_decodes"] += 1
        return original_decode(payload)

    def verify_prefix(path, length, *, exact_length):
        if Path(path) == structural_path:
            metrics["structural_verification_calls"] += 1
        if Path(path) == paths.semantic_v3_result_pack_path(revision_id, run_id):
            metrics["result_prefix_calls"] += 1
            metrics["result_prefix_declared_bytes"] += length
        return original_verify_prefix(path, length, exact_length=exact_length)

    def auth_query(reader, *args, **kwargs):
        before = (reader.observer.node_reads, reader.observer.value_reads, reader.observer.explicit_read_bytes)
        result = original_query(reader, *args, **kwargs)
        after = (reader.observer.node_reads, reader.observer.value_reads, reader.observer.explicit_read_bytes)
        for name, old, new in zip(("auth_node_reads", "auth_value_reads", "auth_proof_bytes"), before, after):
            metrics[name] += new - old
        metrics["max_auth_operation_bytes"] = max(metrics["max_auth_operation_bytes"], after[2] - before[2])
        metrics["max_auth_retained_rows"] = max(metrics["max_auth_retained_rows"], reader.observer.max_retained_rows)
        return result

    def auth_page(reader, *args, **kwargs):
        result = original_page(reader, *args, **kwargs)
        metrics["max_page_rows"] = max(metrics["max_page_rows"], len(result.entries))
        return result

    def connect(database, *args, **kwargs):
        connection = original_connect(database, *args, **kwargs)
        if "catalog.sqlite" in str(database):
            def trace(_statement):
                metrics["catalog_queries"] += 1
            connection.set_trace_callback(trace)
        return connection

    os.read, os.pread = read, pread
    catalog_module._authoritative_rows = authority_rows
    catalog_module._node_from_row = decode_node
    pack_module._decode_canonical_payload = decode_pack
    pack_module._verify_pack_prefix = verify_prefix
    AuthIndexReader._query = auth_query
    AuthIndexReader.page = auth_page
    sqlite3.connect = connect
    observations = []

    def measure(operation, call_index, function):
        result_path = paths.semantic_v3_result_pack_path(revision_id, run_id)
        if result_path.exists():
            status = result_path.stat()
            identities[(status.st_dev, status.st_ino)] = "result"
        metrics.clear()
        metrics.update({name: 0 for name in (
            "catalog_explicit_read_bytes", "structural_explicit_read_bytes",
            "catalog_authority_rows", "catalog_node_decodes",
            "pack_record_decodes", "catalog_queries", "structural_verification_calls",
            "auth_node_reads", "auth_value_reads", "auth_proof_bytes",
            "max_auth_operation_bytes", "max_auth_retained_rows", "max_page_rows",
            "result_prefix_calls", "result_prefix_declared_bytes", "result_explicit_read_bytes",
        )})
        start = time.perf_counter()
        result = function()
        elapsed = time.perf_counter() - start
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        observations.append({
            "operation": operation, "call_index": call_index,
            "elapsed_seconds": elapsed,
            "process_peak_rss_bytes": peak_rss if sys.platform == "darwin" else peak_rss * 1024,
            **metrics,
        })
        return result

    try:
        for index in range(20):
            measure("status", index, lambda: semantic_index_status_v3(
                SemanticIndexStatusV3Request(workspace, run_id)
            ))
        for index in range(2):
            issued = measure("next", index, lambda: next_semantic_work_v3(
                NextSemanticWorkV3Request(workspace, run_id)
            ))
        _write_valid_v3_exchange_output(
            issued.input_path, paths.semantic_v3_exchange_output_path(revision_id, run_id)
        )
        for index in range(2):
            measure("validate", index, lambda: validate_semantic_work_v3(
                ValidateSemanticWorkV3Request(
                    workspace, run_id, issued.unit_id, issued.lease_nonce
                )
            ))
        second = measure("next_distinct", 0, lambda: next_semantic_work_v3(
            NextSemanticWorkV3Request(workspace, run_id)
        ))
        assert second.unit_id is not None and second.unit_id != issued.unit_id
        from portfolio_maker.application.semantic_prepare_v3 import _authenticated_seal
        from portfolio_maker.infrastructure.semantic_authenticated_catalog import AuthenticatedCatalogReader, verify_authenticated_catalog_full
        from portfolio_maker.infrastructure.semantic_catalog import CatalogSeal
        manifest = json.loads((revision / "manifest.v3.json").read_text())
        seal = _authenticated_seal(manifest)
        def audit_population():
            with AuthenticatedCatalogReader(revision, seal) as reader:
                return sum(1 for _ in reader.iter_frontier_deferred_for_audit(selector_version="adaptive-frontier-v1", fetch_size=64))
        audit_count = measure("audit_population", 0, audit_population)
        measure("full_verification", 0, lambda: verify_authenticated_catalog_full(
            revision, CatalogSeal(**{k:manifest["catalog"][k] for k in CatalogSeal.__dataclass_fields__}),
            pack_module.PackSeal(**{k:manifest["pack"][k] for k in pack_module.PackSeal.__dataclass_fields__}), seal,
        ))
    finally:
        os.read, os.pread = original_read, original_pread
        catalog_module._authoritative_rows = original_rows
        catalog_module._node_from_row = original_node
        pack_module._decode_canonical_payload = original_decode
        pack_module._verify_pack_prefix = original_verify_prefix
        AuthIndexReader._query = original_query
        AuthIndexReader.page = original_page
        sqlite3.connect = original_connect
    return {
        "audit_population_rows": audit_count,
        "status_p95_seconds": sorted(o["elapsed_seconds"] for o in observations if o["operation"] == "status")[18],
        "authenticated_pack_bytes": (revision / "authenticated.pm-pack").stat().st_size,
        "catalog_bytes": catalog_path.stat().st_size,
        "structural_pack_bytes": structural_path.stat().st_size,
        "observations": observations,
        "notes": [
            "fresh child process; OS filesystem cache is not flushed",
            "call_index 1 is warm repeat; next/validate repeat the same issued/receipted unit",
            "explicit read bytes exclude SQLite pager I/O; authority row counts observe full logical-root scans",
            "RSS is process high-water mark, not per-operation allocation",
            "synthetic injected output is not a semantic-quality evaluation",
            "audit_population measures iterator enumeration, not expansion-closure sample/digest equivalence",
            "full_verification is a separate measured phase; RSS includes earlier operations in the same child",
            "result bytes from a file first created during an operation are not all attributed; subsequent replay is attributed",

        ],
    }


@pytest.mark.skipif(
    os.environ.get("PM_SEMANTIC_EARLY_SCALE") != "1",
    reason="opt-in 1k/10k synthetic early resource gate",
)
def test_early_scale_steady_state_does_not_scan_catalog_or_structural_pack(tmp_path: Path):
    """Fails if bounded next/validate rehash or reserialize the full generation."""
    observations = []
    for count in (1_000, 10_000):
        workspace = tmp_path / str(count)
        prepare_start = time.perf_counter()
        prepared = _prepare_synthetic_catalog(workspace, count)
        prepare_elapsed = time.perf_counter() - prepare_start
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        environment["PYTHONPATH"] = os.pathsep.join((
            str(Path(__file__).resolve().parents[1] / "src"),
            str(Path(__file__).resolve().parent),
        ))
        child = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--measure",
             str(workspace), prepared.revision_id, prepared.run_id],
            text=True, capture_output=True, env=environment, timeout=240,
        )
        assert child.returncode == 0, child.stderr
        observations.append({"node_count": count, "prepare_and_approve_seconds": prepare_elapsed, **json.loads(child.stdout)})
    # Emit all observations before checking the gate, including both populations.
    print("EARLY_SCALE_METRICS=" + json.dumps(observations, sort_keys=True))
    violations = []
    for population in observations:
        for observation in population["observations"]:
            assert observation["max_page_rows"] <= 64
            assert observation["max_auth_retained_rows"] <= 65
            assert observation["max_auth_operation_bytes"] <= 16 * 1_048_576
            if observation["operation"] in ("audit_population", "full_verification"):
                continue
            if (observation["catalog_authority_rows"]
                    or observation["structural_verification_calls"]):
                violations.append({"node_count": population["node_count"], **observation})
    assert not violations, "steady-state full catalog/structural scan: " + json.dumps(violations)


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "--measure":
        raise SystemExit("usage: test_semantic_scale_v3.py --measure WORKSPACE REVISION RUN")
    print(json.dumps(_measure_child(Path(sys.argv[2]), sys.argv[3], sys.argv[4])))
