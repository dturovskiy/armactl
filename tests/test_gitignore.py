"""Tests for repository-root runtime artifact ignore rules."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_gitignore_hides_root_server_artifacts_from_git_status() -> None:
    git_bin = shutil.which("git")
    if git_bin is None:
        pytest.skip("git is not installed")

    repo_root = Path(__file__).resolve().parents[1]
    artifacts = [
        "ArmaReforgerServer",
        "ArmaReforgerServerDiag",
        "CrashReporter",
        "addons/example.pak",
        "battleye/BEServer_x64.cfg",
        "battleye/EULA/eula.txt",
        "licenses/license.txt",
        "steamapps/appmanifest_1874900.acf",
        "steam_appid.txt",
        "License.txt",
        "Readme.txt",
        "docs/ServerHostingDocs.bat",
    ]

    result = subprocess.run(
        [git_bin, "check-ignore", "--no-index", "--stdin"],
        input="\n".join(artifacts) + "\n",
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    ignored = set(result.stdout.splitlines())
    assert ignored == set(artifacts)
