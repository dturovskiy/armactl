"""Tests for the repo-local ./armactl wrapper and bootstrap helper."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _write_pyproject(project: Path) -> None:
    (project / "pyproject.toml").write_text(
        "[project]\nname = 'armactl-test'\n",
        encoding="utf-8",
    )


def _pyproject_hash(project: Path) -> str:
    return hashlib.sha256((project / "pyproject.toml").read_bytes()).hexdigest()


def _copy_wrapper(project: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / "armactl"
    wrapper = project / "armactl"
    shutil.copy2(source, wrapper)
    wrapper.chmod(0o755)
    return wrapper


def _copy_bootstrap(project: Path) -> Path:
    scripts = project / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap.sh"
    bootstrap = scripts / "bootstrap.sh"
    shutil.copy2(source, bootstrap)
    bootstrap.chmod(0o755)
    return bootstrap


def _write_fake_venv_python(project: Path, *, exit_code: int = 0) -> Path:
    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    python_bin = venv_bin / "python"
    python_bin.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    python_bin.chmod(0o755)
    return python_bin


@pytest.mark.skipif(os.name != "posix", reason="armactl wrapper is a POSIX shell script")
@pytest.mark.parametrize("stale_venv", [False, True])
def test_wrapper_does_not_bootstrap_without_tty(tmp_path: Path, stale_venv: bool) -> None:
    project = tmp_path / "repo"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    _write_pyproject(project)
    wrapper = _copy_wrapper(project)

    bootstrap = scripts / "bootstrap.sh"
    bootstrap.write_text(
        "#!/bin/sh\ntouch bootstrap-ran\nexit 42\n",
        encoding="utf-8",
    )
    bootstrap.chmod(0o755)

    if stale_venv:
        _write_fake_venv_python(project)
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
    assert "./scripts/bootstrap.sh --check --prod" in result.stderr
    assert "./scripts/bootstrap.sh --prod" in result.stderr
    assert "ARMACTL_PYTHON=.venv/bin/python ./armactl" in result.stderr
    assert not (project / "bootstrap-ran").exists()


@pytest.mark.skipif(os.name != "posix", reason="armactl wrapper is a POSIX shell script")
def test_wrapper_uses_web_bootstrap_mode_for_web_commands(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    _write_pyproject(project)
    wrapper = _copy_wrapper(project)

    bootstrap = scripts / "bootstrap.sh"
    bootstrap.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
    bootstrap.chmod(0o755)

    result = subprocess.run(
        [str(wrapper), "web", "init"],
        cwd=project,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "interactive TTY" in result.stderr
    assert "./scripts/bootstrap.sh --check --web" in result.stderr
    assert "./scripts/bootstrap.sh --web" in result.stderr


@pytest.mark.skipif(os.name != "posix", reason="bootstrap helper is a POSIX shell script")
def test_bootstrap_help_does_not_enter_installer_path(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    bootstrap = _copy_bootstrap(project)

    result = subprocess.run(
        [str(bootstrap), "--help"],
        cwd=project,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Usage: ./scripts/bootstrap.sh" in result.stdout
    assert "--check" in result.stdout
    assert "apt" in result.stdout
    assert not (project / ".venv").exists()


@pytest.mark.skipif(os.name != "posix", reason="bootstrap helper is a POSIX shell script")
def test_bootstrap_check_reports_missing_venv_without_mutating(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _write_pyproject(project)
    bootstrap = _copy_bootstrap(project)

    result = subprocess.run(
        [str(bootstrap), "--check", "--web"],
        cwd=project,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "refresh needed for --web" in result.stdout
    assert "runtime Python is missing" in result.stdout
    assert "./scripts/bootstrap.sh --web" in result.stdout
    assert result.stderr == ""
    assert not (project / ".venv").exists()


@pytest.mark.skipif(os.name != "posix", reason="bootstrap helper is a POSIX shell script")
def test_bootstrap_check_accepts_matching_web_stamp(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _write_pyproject(project)
    bootstrap = _copy_bootstrap(project)
    _write_fake_venv_python(project)
    (project / ".venv" / ".armactl-pyproject.sha256").write_text(
        f"{_pyproject_hash(project)} --web\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(bootstrap), "--check", "--web"],
        cwd=project,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "OK for --web" in result.stdout


@pytest.mark.skipif(os.name != "posix", reason="bootstrap helper is a POSIX shell script")
def test_bootstrap_check_rejects_prod_stamp_for_web_mode(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _write_pyproject(project)
    bootstrap = _copy_bootstrap(project)
    _write_fake_venv_python(project)
    (project / ".venv" / ".armactl-pyproject.sha256").write_text(
        f"{_pyproject_hash(project)} --prod\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(bootstrap), "--check", "--web"],
        cwd=project,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "does not satisfy requested mode '--web'" in result.stdout
    assert "Do not edit" in result.stdout
