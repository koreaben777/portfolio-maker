from portfolio_maker.workspace import WorkspacePaths


def test_workspace_paths_create_expected_directories(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()

    assert paths.root == workspace / ".portfolio-maker"
    assert paths.db_path == workspace / ".portfolio-maker" / "portfolio.db"
    assert paths.reviews_dir.is_dir()
    assert paths.artifacts_dir.is_dir()
    assert paths.local_snapshots_dir.is_dir()
    assert paths.github_snapshots_dir.is_dir()
    assert paths.logs_dir.is_dir()
