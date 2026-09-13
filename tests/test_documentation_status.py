from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPOSITORY_ROOT / "docs"
CANONICAL_CHECKLIST = DOCS_ROOT / "checklist.md"
PLAN_REGISTER = DOCS_ROOT / "plans-register.md"
TASK_CHECKBOX = re.compile(r"^\s*-\s+\[[ xX]\]\s+", re.MULTILINE)


def test_only_canonical_checklist_contains_project_status_checkboxes() -> None:
    offenders = [
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in sorted(DOCS_ROOT.glob("*.md"))
        if path != CANONICAL_CHECKLIST
        and TASK_CHECKBOX.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []
    assert TASK_CHECKBOX.search(CANONICAL_CHECKLIST.read_text(encoding="utf-8"))


def test_plan_register_classifies_every_document() -> None:
    register = PLAN_REGISTER.read_text(encoding="utf-8")
    unclassified = [
        path.name
        for path in sorted(DOCS_ROOT.glob("*.md"))
        if path.name not in {CANONICAL_CHECKLIST.name, PLAN_REGISTER.name}
        and f"({path.name})" not in register
    ]

    assert unclassified == []
