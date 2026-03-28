"""Tests for armactl mod import/export helpers."""

import json
from pathlib import Path

from armactl.mods_manager import export_mods, get_mods, import_mods


def _write_config(config_path: Path, mods: list[dict] | None = None) -> None:
    """Create a minimal valid config file for mod tests."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "bindAddress": "0.0.0.0",
        "bindPort": 2001,
        "publicAddress": "",
        "publicPort": 2001,
        "game": {
            "name": "Test Server",
            "scenarioId": "{TEST}Missions/Test.conf",
            "maxPlayers": 64,
            "mods": mods or [],
        },
    }
    config_path.write_text(json.dumps(payload, indent=4), encoding="utf-8")


def test_export_mods_writes_json_array(tmp_path: Path):
    """Export should write the active mod list to a JSON array file."""
    config_path = tmp_path / "config" / "config.json"
    export_path = tmp_path / "exports" / "mods.json"
    mods = [
        {"modId": "AAA111", "name": "Alpha"},
        {"modId": "BBB222", "name": "Bravo"},
    ]
    _write_config(config_path, mods)

    count = export_mods(config_path, export_path)

    assert count == 2
    assert export_path.is_file()
    assert json.loads(export_path.read_text(encoding="utf-8")) == mods


def test_import_mods_accepts_full_config_file(tmp_path: Path):
    """Import should accept a full config.json file with game.mods."""
    target_config = tmp_path / "target" / "config.json"
    source_config = tmp_path / "source" / "config.json"
    source_mods = [
        {"modId": "AAA111", "name": "Alpha"},
        {"modId": "BBB222", "name": "Bravo"},
    ]
    _write_config(target_config, [])
    _write_config(source_config, source_mods)

    added, skipped = import_mods(target_config, source_config, append=False)
    imported = get_mods(target_config)

    assert added == 2
    assert skipped == 0
    assert [mod["modId"] for mod in imported] == ["AAA111", "BBB222"]
    assert imported[0]["name"] == "Alpha"
    assert imported[0]["version"] == ""


def test_import_mods_append_skips_duplicates(tmp_path: Path):
    """Append mode should keep existing mods and skip duplicate IDs."""
    target_config = tmp_path / "target" / "config.json"
    import_file = tmp_path / "mods.json"
    _write_config(target_config, [{"modId": "AAA111", "name": "Alpha"}])
    import_file.write_text(
        json.dumps(
            [
                {"modId": "AAA111", "name": "Alpha Duplicate"},
                {"modId": "BBB222", "name": "Bravo"},
            ],
            indent=4,
        ),
        encoding="utf-8",
    )

    added, skipped = import_mods(target_config, import_file, append=True)
    imported = get_mods(target_config)

    assert added == 1
    assert skipped == 1
    assert [mod["modId"] for mod in imported] == ["AAA111", "BBB222"]


def test_template_config_roundtrip_large_mod_pack(tmp_path: Path):
    """The sample template config should round-trip as a large mod pack."""
    template_path = Path(__file__).resolve().parents[1] / "templates" / "config.json"
    template_payload = json.loads(template_path.read_text(encoding="utf-8"))
    template_mods = template_payload["game"]["mods"]
    target_config = tmp_path / "target" / "config.json"
    export_path = tmp_path / "roundtrip" / "mods.json"
    _write_config(target_config, [])

    added, skipped = import_mods(target_config, template_path, append=False)
    exported_count = export_mods(target_config, export_path)
    exported_mods = json.loads(export_path.read_text(encoding="utf-8"))

    assert len(template_mods) >= 50
    assert added == len(template_mods)
    assert skipped == 0
    assert exported_count == len(template_mods)
    assert len(exported_mods) == len(template_mods)
    assert exported_mods[0]["modId"] == template_mods[0]["modId"]
    assert exported_mods[-1]["modId"] == template_mods[-1]["modId"]
