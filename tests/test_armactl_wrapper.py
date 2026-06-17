"""Tests for the repo-local ./armactl wrapper."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "posix", reason="armactl wrapper is a POSIX shell script")
@pytest.mark.parametrize("stale_venv", [False, True])
def test_wrapper_does_not_bootstrap_without_tty(tmp_path: Path, stale_venv: bool) -> None:
    project = tmp_path / "repo"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname = 'armactl-test'\n",
        encoding="utf-8",
    )

    source = Path(__file__).resolve().parents[1] / "armactl"
    wrapper = project / "armactl"
    shutil.copy2(source, wrapper)
    wrapper.chmod(0o755)

    bootstrap = scripts / "bootstrap.sh"
    bootstrap.write_text(
        "#!/bin/sh\ntouch bootstrap-ran\nexit 42\n",
        encoding="utf-8",
    )
    bootstrap.chmod(0o755)

    if stale_venv:
        venv_bin = project / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        python_bin = venv_bin / "python"
        python_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        python_bin.chmod(0o755)
        (project / ".venv" / ".armactl-pyproject.sha256").write_text(
            "stale --prod\n",
            encoding="utf-8",
        )

    result = subprocess.run(
        [str(wrapper), "status"],
        cwd=project,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "interactive TTY" in result.stderr
    assert "./scripts/bootstrap.sh --prod" in result.stderr
    assert ".venv/bin/python -m armactl" in result.stderr
    assert not (project / "bootstrap-ran").exists()
