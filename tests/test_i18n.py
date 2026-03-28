"""Tests for locale metadata and translation coverage."""

import ast
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCALES_DIR = REPO_ROOT / "src" / "armactl" / "locales"
LOCALIZED_MODULES = [
    REPO_ROOT / "src" / "armactl" / "tui" / "app.py",
    REPO_ROOT / "src" / "armactl" / "tui" / "screens.py",
    REPO_ROOT / "src" / "armactl" / "config_manager.py",
    REPO_ROOT / "src" / "armactl" / "mods_manager.py",
    REPO_ROOT / "src" / "armactl" / "service_manager.py",
    REPO_ROOT / "src" / "armactl" / "installer.py",
    REPO_ROOT / "src" / "armactl" / "repair.py",
]


def _extract_translation_keys(path: Path) -> set[str]:
    """Collect literal translation keys used via _() and tr()."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keys: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in {"_", "tr"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                keys.add(node.args[0].value)
            self.generic_visit(node)

    Visitor().visit(tree)
    return keys


def test_locale_files_have_required_metadata():
    """Each locale file should expose readable metadata."""
    locale_files = sorted(LOCALES_DIR.glob("*.json"))
    assert locale_files, "No locale files found."

    for locale_path in locale_files:
        payload = json.loads(locale_path.read_text(encoding="utf-8"))
        meta = payload.get("__meta__", {})
        assert meta.get("code"), f"{locale_path.name} is missing __meta__.code"
        assert meta.get("language"), f"{locale_path.name} is missing __meta__.language"
        assert isinstance(payload.get("translations", {}), dict)


def test_uk_locale_covers_all_localized_strings():
    """Ukrainian locale should cover all current translation keys."""
    used_keys: set[str] = set()
    for module_path in LOCALIZED_MODULES:
        used_keys.update(_extract_translation_keys(module_path))

    uk_payload = json.loads((LOCALES_DIR / "uk.json").read_text(encoding="utf-8"))
    translated_keys = set(uk_payload.get("translations", {}).keys())

    missing = sorted(used_keys - translated_keys)
    assert not missing, f"uk.json is missing translation keys: {missing}"
