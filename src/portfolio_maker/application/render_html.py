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
    build_public_portfolio_payload,
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
        build_public_portfolio(
            PublicPortfolioRequest(workspace=request.workspace)
        )
        html_build = build_public_portfolio_payload(
            PublicPortfolioRequest(workspace=request.workspace),
            "portfolio_html",
        )
    except PublicPortfolioError as error:
        raise HtmlRenderError(str(error)) from error
    manifest = html_build.payload

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
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.record_artifact(
        "portfolio_html",
        1,
        json.dumps(
            _html_input_manifest(html_build.selection, html_build.payload),
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    return RenderHtmlResult(
        manifest_path=paths.portfolio_public_json_path,
        html_path=paths.portfolio_html_path,
    )


def _html_input_manifest(selection, payload: dict[str, object]) -> dict[str, object]:
    input_manifest = selection.input_manifest("portfolio_html")
    payload_bytes = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    input_manifest["manifest_sha256"] = hashlib.sha256(payload_bytes).hexdigest()
    return input_manifest
