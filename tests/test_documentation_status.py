from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPOSITORY_ROOT / "docs"
CANONICAL_CHECKLIST = DOCS_ROOT / "checklist.md"
PLAN_REGISTER = DOCS_ROOT / "plans-register.md"
TASK_CHECKBOX = re.compile(r"^\s*-\s+\[[ xX]\]\s+", re.MULTILINE)
COMPLETED_TASK_CHECKBOX = re.compile(r"^\s*-\s+\[[xX]\]\s+", re.MULTILINE)
PROCEDURAL_CHECKBOX_FILES = {
    REPOSITORY_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md",
}


def _public_markdown_paths() -> list[Path]:
    paths: set[Path] = set(REPOSITORY_ROOT.glob("*.md"))
    for root_name in (".github", "assets", "docs"):
        paths.update((REPOSITORY_ROOT / root_name).rglob("*.md"))
    return sorted(paths)


def test_only_canonical_checklist_contains_project_status_checkboxes() -> None:
    offenders = [
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in _public_markdown_paths()
        if path != CANONICAL_CHECKLIST
        and path not in PROCEDURAL_CHECKBOX_FILES
        and TASK_CHECKBOX.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []
    checklist = CANONICAL_CHECKLIST.read_text(encoding="utf-8")
    assert TASK_CHECKBOX.search(checklist)
    assert not COMPLETED_TASK_CHECKBOX.search(checklist)


def test_plan_register_classifies_every_document() -> None:
    register = PLAN_REGISTER.read_text(encoding="utf-8")
    unclassified = [
        path.relative_to(DOCS_ROOT).as_posix()
        for path in sorted(DOCS_ROOT.rglob("*.md"))
        if path.name not in {CANONICAL_CHECKLIST.name, PLAN_REGISTER.name}
        and f"({path.relative_to(DOCS_ROOT).as_posix()})" not in register
    ]

    assert unclassified == []
