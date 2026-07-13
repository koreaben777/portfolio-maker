from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from portfolio_maker.application.models import (
    PublicPortfolioRequest,
    RenderHtmlRequest,
    RenderHtmlResult,
)
from portfolio_maker.application.public_portfolio import (
    PublicPortfolioError,
    build_public_portfolio,
)
from portfolio_maker.infrastructure.managed_files import remove_managed_file, write_managed_text
from portfolio_maker.infrastructure.static_site import (
    StaticSiteError,
    inline_static_output,
    validate_static_output,
    write_generated_data_module,
)
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


class HtmlRenderError(RuntimeError):
    pass


def render_html(request: RenderHtmlRequest) -> RenderHtmlResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    try:
        remove_managed_file(paths.portfolio_html_path, missing_ok=True)
    except OSError as error:
        raise HtmlRenderError("managed portfolio HTML could not be invalidated") from error

    site_dir = paths.workspace / "web" / "portfolio"
    if not site_dir.is_dir():
        raise HtmlRenderError(f"Sites project missing: {site_dir}")

    try:
        public_result = build_public_portfolio(
            PublicPortfolioRequest(workspace=request.workspace)
        )
    except PublicPortfolioError as error:
        raise HtmlRenderError(str(error)) from error
    try:
        manifest = json.loads(paths.portfolio_public_json_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HtmlRenderError("public portfolio manifest could not be read") from error
    if not isinstance(manifest, dict):
        raise HtmlRenderError("public portfolio manifest must be an object")

    try:
        with tempfile.TemporaryDirectory(prefix="portfolio-maker-sites-") as temp_dir:
            temp_site = Path(temp_dir) / "portfolio"
            shutil.copytree(
                site_dir,
                temp_site,
                ignore=shutil.ignore_patterns("dist", "node_modules"),
            )
            node_modules = site_dir / "node_modules"
            if node_modules.is_dir():
                (temp_site / "node_modules").symlink_to(
                    node_modules.resolve(),
                    target_is_directory=True,
                )
            generated_data_path = temp_site / "src" / "generated" / "portfolio-data.ts"
            write_generated_data_module(generated_data_path, manifest)
            subprocess.run(
                ["npm", "run", "build"],
                cwd=temp_site,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            temporary_dist = temp_site / "dist"
            validate_static_output(temporary_dist)
            html = inline_static_output(temporary_dist)
    except FileNotFoundError as error:
        raise HtmlRenderError("Sites build tool is unavailable") from error
    except subprocess.TimeoutExpired as error:
        raise HtmlRenderError("Sites build timed out") from error
    except subprocess.CalledProcessError as error:
        raise HtmlRenderError("Sites build failed") from error
    except StaticSiteError as error:
        raise HtmlRenderError(str(error)) from error

    write_managed_text(paths.portfolio_html_path, html)
    manifest_bytes = paths.portfolio_public_json_path.read_bytes()
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.record_artifact(
        "portfolio_html",
        1,
        json.dumps(
            _html_input_manifest(manifest, public_result, manifest_bytes),
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    return RenderHtmlResult(
        manifest_path=paths.portfolio_public_json_path,
        html_path=paths.portfolio_html_path,
    )


def _html_input_manifest(
    manifest: dict[str, object],
    public_result,
    manifest_bytes: bytes,
) -> dict[str, object]:
    selection = manifest.get("selection")
    if isinstance(selection, dict):
        result = dict(selection)
        result["artifact_kind"] = "portfolio_html"
        result["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        return result
    return {
        "claim_ids": list(public_result.claim_ids),
        "evidence_ids": list(public_result.evidence_ids),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }
