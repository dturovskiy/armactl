"""URL helpers for file-browser DTO links."""

from __future__ import annotations

from urllib.parse import quote


def query_path(relative_path: str) -> str:
    """Encode a browser relative path for query-string use."""
    return quote(relative_path, safe="")


def files_href(root_id: str, relative_path: str = "") -> str:
    """Return the directory-listing href for one browser path."""
    if not relative_path:
        return f"/files/{root_id}"
    return f"/files/{root_id}?path={query_path(relative_path)}"


def preview_href(root_id: str, relative_path: str) -> str:
    """Return the preview href for one browser path."""
    return f"/files/{root_id}/preview?path={query_path(relative_path)}"


def replace_href(root_id: str, relative_path: str) -> str:
    """Return the explicit replacement POST target for one browser path."""
    return f"/files/{root_id}/replace?path={query_path(relative_path)}"


def edit_href(root_id: str, relative_path: str) -> str:
    """Return the safe text editor href for one browser path."""
    return f"/files/{root_id}/edit?path={query_path(relative_path)}"


def download_href(root_id: str, relative_path: str) -> str:
    """Return the download href for one browser path."""
    return f"/files/{root_id}/download?path={query_path(relative_path)}"
