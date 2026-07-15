from __future__ import annotations

import json
from pathlib import Path
import re
from dataclasses import dataclass


class StaticSiteError(ValueError):
    pass


@dataclass(frozen=True)
class DeploymentArtifact:
    path: Path
    delivery_scope: str


def prepare_public_deployment(artifact: DeploymentArtifact) -> DeploymentArtifact:
    if artifact.delivery_scope != "open_public":
        raise StaticSiteError("public deployment requires open_public output")
    return artifact


def prepare_private_deployment(artifact: DeploymentArtifact) -> DeploymentArtifact:
    if artifact.delivery_scope not in {"restricted", "open_public"}:
        raise StaticSiteError("private deployment requires a validated delivery scope")
    return artifact


_ROOT_ASSET_REFERENCE = re.compile(r"(?:src|href)=\s*[\"']/")
_RELATIVE_ASSET_REFERENCE = re.compile(r"(?:src|href)=\s*[\"'](\./[^\"']+)[\"']")
_CSS_REFERENCE = re.compile(
    r'<link(?=[^>]*\brel=["\']stylesheet["\'])(?P<attrs>[^>]*)\bhref=["\']\./assets/(?P<name>[^"\']+)["\'][^>]*>'
)
_SCRIPT_REFERENCE = re.compile(
    r"<script(?P<attrs>[^>]*)src=\"\./assets/(?P<name>[^\"]+)\"(?P<tail>[^>]*)></script>"
)
_PRIVATE_WORKSPACE_PATH = re.compile(r"(?:^|[\\/])\.portfolio-maker(?:[\\/]|$)")
_UNSAFE_STATIC_MARKERS = (
    "fetch(",
    "xmlhttprequest",
    "portfolio.db",
    "file://",
)


def write_generated_data_module(path: Path, manifest: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_for_javascript(manifest)
    path.write_text(
        "export const portfolioData = " + payload + " as const;\n\n"
        "export default portfolioData;\n",
        encoding="utf-8",
    )
    return path


def _json_for_javascript(value: dict[str, object]) -> str:
    """Keep inline build output from interpreting data as HTML/script markup."""
    return (
        json.dumps(value, ensure_ascii=False, indent=2)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def validate_static_output(dist: Path) -> Path:
    index_path = dist / "index.html"
    if not dist.is_dir() or not index_path.is_file():
        raise StaticSiteError("Sites build did not produce dist/index.html")
    html = index_path.read_text(encoding="utf-8")
    if _ROOT_ASSET_REFERENCE.search(html):
        raise StaticSiteError("static HTML must use relative asset references")
    if "./" not in html and _RELATIVE_ASSET_REFERENCE.search(html) is None:
        raise StaticSiteError("static HTML has no relative asset entrypoint")
    for path in dist.rglob("*"):
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise StaticSiteError(f"static asset is not UTF-8 text: {path.name}") from error
        if _contains_unsafe_reference(content):
            raise StaticSiteError(f"unsafe runtime or private reference in {path.name}")
    return index_path


def inline_static_output(dist: Path) -> str:
    index_path = validate_static_output(dist)
    html = index_path.read_text(encoding="utf-8")

    def replace_css(match: re.Match[str]) -> str:
        asset = dist / "assets" / match.group("name")
        return "<style>\n" + _read_asset(asset) + "\n</style>"

    def replace_script(match: re.Match[str]) -> str:
        asset = dist / "assets" / match.group("name")
        return '<script type="module">\n' + _read_asset(asset) + "\n</script>"

    html = _CSS_REFERENCE.sub(replace_css, html)
    html = _SCRIPT_REFERENCE.sub(replace_script, html)
    if _RELATIVE_ASSET_REFERENCE.search(html):
        raise StaticSiteError("canonical portfolio HTML still references an external asset")
    if _contains_unsafe_reference(html):
        raise StaticSiteError("canonical portfolio HTML contains an unsafe reference")
    return html


def _contains_unsafe_reference(content: str) -> bool:
    lowered = content.casefold()
    return any(marker in lowered for marker in _UNSAFE_STATIC_MARKERS) or bool(
        _PRIVATE_WORKSPACE_PATH.search(lowered)
    )


def _read_asset(path: Path) -> str:
    if not path.is_file():
        raise StaticSiteError(f"Sites build asset is missing: {path.name}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise StaticSiteError(f"Sites build asset is not UTF-8 text: {path.name}") from error
