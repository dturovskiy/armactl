"""Tests for armactl package integrity manifests."""

from pathlib import Path

from armactl.integrity import (
    SERVER_BINARY_NAME,
    check_package_integrity,
    clear_install_marker,
    mark_install_started,
    write_package_manifest,
)


def test_package_manifest_detects_missing_recorded_file(tmp_path: Path) -> None:
    server_dir = tmp_path / "server"
    addons_dir = server_dir / "addons"
    addons_dir.mkdir(parents=True)
    (server_dir / SERVER_BINARY_NAME).write_text("fake binary", encoding="utf-8")
    addon_file = addons_dir / "worlds.pak"
    addon_file.write_text("fake addon", encoding="utf-8")

    write_package_manifest(server_dir)
    ok = check_package_integrity(server_dir)
    assert ok.complete is True
    assert ok.expected_files == 2

    addon_file.unlink()
    broken = check_package_integrity(server_dir)
    assert broken.complete is False
    assert broken.status == "missing_files"
    assert broken.missing_files == ["addons/worlds.pak"]


def test_install_marker_prevents_complete_status(tmp_path: Path) -> None:
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (server_dir / SERVER_BINARY_NAME).write_text("fake binary", encoding="utf-8")
    write_package_manifest(server_dir)

    mark_install_started(server_dir)
    installing = check_package_integrity(server_dir)
    assert installing.complete is False
    assert installing.status == "installing"

    clear_install_marker(server_dir)
    assert check_package_integrity(server_dir).complete is True


def test_steam_incomplete_state_prevents_complete_status(tmp_path: Path) -> None:
    server_dir = tmp_path / "server"
    steamapps = server_dir / "steamapps"
    steamapps.mkdir(parents=True)
    (server_dir / SERVER_BINARY_NAME).write_text("fake binary", encoding="utf-8")
    (steamapps / "appmanifest_1874900.acf").write_text(
        '"AppState"\n{\n    "StateFlags" "2"\n}\n',
        encoding="utf-8",
    )
    write_package_manifest(server_dir)

    result = check_package_integrity(server_dir)
    assert result.complete is False
    assert result.status == "steam_incomplete"
