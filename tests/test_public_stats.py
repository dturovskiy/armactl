from __future__ import annotations

import types
from pathlib import Path

from click.testing import CliRunner

import armactl.metrics as metrics
import armactl.public_stats as public_stats
import armactl.status_summary as status_summary
from armactl.cli import main
from armactl.player_view import PlayerView
from armactl.state import ServerState


def _patch_public_stats_sources(monkeypatch) -> None:
    state = ServerState(
        server_installed=True,
        config_exists=True,
        server_running=True,
        config_path="/srv/secret/config.json",
        service_name="armareforger.service",
    )
    monkeypatch.setattr(public_stats.discovery, "discover", lambda **kwargs: state)
    monkeypatch.setattr(
        public_stats,
        "get_service_status",
        lambda service_name: {"active_state": "active"},
    )
    monkeypatch.setattr(
        public_stats.status_summary,
        "load_status_summaries",
        lambda config_path: (
            status_summary.ConfigSummary(
                available=True,
                server_name="@everyone Reforger",
                scenario_id="{ABC}Missions/Test.conf",
                max_players=64,
            ),
            status_summary.ModsSummary(
                available=True,
                count=4,
                preview=[
                    status_summary.ModSummaryEntry("ABC123", "Weapons @here"),
                    status_summary.ModSummaryEntry("DEF456", "Vehicles"),
                ],
                remaining_count=2,
            ),
        ),
    )
    monkeypatch.setattr(
        public_stats.player_view,
        "query_player_view",
        lambda *args, **kwargs: PlayerView(
            available=True,
            current=2,
            max_players=64,
            map_name="ARM-Campaign_ScenarioName_Everon",
            entries=(
                types.SimpleNamespace(name="@here Player"),
                types.SimpleNamespace(name="Normal Player"),
            ),
            roster_available=True,
        ),
    )
    monkeypatch.setattr(
        public_stats.metrics,
        "query_server_fps_metrics",
        lambda config_dir: metrics.ServerFpsMetrics(
            available=True,
            fps=59.8,
            frame_avg_ms=16.7,
            frame_min_ms=15.0,
            frame_max_ms=18.2,
            age_seconds=4.0,
        ),
    )


def test_render_discord_stats_message_is_public_and_mention_safe(monkeypatch) -> None:
    _patch_public_stats_sources(monkeypatch)

    snapshot = public_stats.load_public_stats("default")
    text = public_stats.render_discord_stats_message(snapshot)

    assert "**@ everyone Reforger**" in text
    assert "📊 Server statistics" in text
    assert "🟢 **Online**" in text
    assert "🗺️ **Everon**" in text
    assert "```text" in text
    assert "Status    Map" in text
    assert "Online    Everon" in text
    assert "👥 Online:" in text
    assert "- @ here Player" in text
    assert "- Normal Player" in text
    assert "Telemetry:" not in text
    assert "Mod preview:" not in text
    assert "<t:" in text
    assert "Weapons @ here" not in text
    assert "@everyone" not in text
    assert "@here" not in text
    assert "/srv/secret" not in text


def test_stats_public_cli_renders_discord_message(monkeypatch) -> None:
    snapshot = public_stats.PublicStatsSnapshot(
        instance="default",
        generated_at="2026-06-26T10:00:00+00:00",
        lifecycle="running",
        running=True,
        service_state="active",
        server_name="Public Server",
        scenario_id="{ECC61978EDCC2B5A}Missions/23_Campaign.conf",
        map_name="#AR-Campaign_ScenarioName_Everon",
        players_available=True,
        player_count=3,
        max_players=64,
        player_names=("Denis", "Vova"),
        roster_available=True,
        fps_available=True,
        fps_stale=False,
        fps_text="60.0",
        telemetry_age_text="5s",
        mods_available=True,
        mod_count=12,
        mod_preview=("Weapons",),
        remaining_mod_count=11,
    )
    monkeypatch.setattr(public_stats, "load_public_stats", lambda instance: snapshot)

    result = CliRunner().invoke(main, ["stats", "public", "--format", "discord"])

    assert result.exit_code == 0
    assert "**Public Server**" in result.output
    assert "📊 Server statistics" in result.output
    assert "🗺️ **Conflict: Everon**" in result.output
    assert "Status    Map" in result.output
    assert "Online    Conflict: Everon" in result.output
    assert "👥 Online:" in result.output
    assert "- Denis" in result.output
    assert "- Vova" in result.output
    assert "`(running)`" not in result.output


def test_stats_public_cli_honors_global_json_output(monkeypatch) -> None:
    snapshot = public_stats.PublicStatsSnapshot(
        instance="default",
        generated_at="2026-06-26T10:00:00+00:00",
        lifecycle="stopped",
        running=False,
        service_state="inactive",
        server_name="Public Server",
        scenario_id="{ECC61978EDCC2B5A}Missions/23_Campaign.conf",
        map_name="#AR-Campaign_ScenarioName_Everon",
        players_available=False,
        player_count=None,
        max_players=None,
        player_names=(),
        roster_available=False,
        fps_available=False,
        fps_stale=False,
        fps_text="unavailable",
        telemetry_age_text="unknown",
        mods_available=False,
        mod_count=None,
        mod_preview=(),
        remaining_mod_count=0,
    )
    monkeypatch.setattr(public_stats, "load_public_stats", lambda instance: snapshot)

    result = CliRunner().invoke(main, ["--json-output", "stats", "public"])

    assert result.exit_code == 0
    assert '"server_name": "Public Server"' in result.output
    assert '"running": false' in result.output


def test_discord_stats_config_save_load_and_mask(tmp_path: Path) -> None:
    import stat

    from armactl.discord_stats import (
        DiscordStatsConfig,
        load_discord_stats_config,
        save_discord_stats_config,
    )

    env_path = tmp_path / "discord-stats.env"
    save_discord_stats_config(
        DiscordStatsConfig(
            instance="default",
            enabled=True,
            webhook_url="https://discord.com/api/webhooks/123456/secret-token",
            interval_seconds=45,
            message_id="987654",
            env_path=env_path,
        )
    )

    loaded = load_discord_stats_config("default", env_path=env_path)

    assert loaded.enabled is True
    assert loaded.webhook_configured() is True
    assert loaded.interval_seconds == 45
    assert loaded.message_id == "987654"
    assert loaded.masked_webhook_url() == "configured (...-token)"
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_discord_stats_config_rejects_non_discord_url(tmp_path: Path) -> None:
    from armactl.discord_stats import (
        DiscordStatsConfig,
        DiscordStatsConfigError,
        save_discord_stats_config,
    )

    try:
        save_discord_stats_config(
            DiscordStatsConfig(
                instance="default",
                enabled=True,
                webhook_url="https://example.com/hook",
                env_path=tmp_path / "discord-stats.env",
            )
        )
    except DiscordStatsConfigError as error:
        assert "discord.com" in str(error)
    else:
        raise AssertionError("expected DiscordStatsConfigError")


def test_publish_discord_stats_creates_and_updates_existing_message(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.discord_stats import (
        DiscordStatsConfig,
        load_discord_stats_config,
        publish_discord_stats,
        save_discord_stats_config,
    )

    env_path = tmp_path / "discord-stats.env"
    config = DiscordStatsConfig(
        instance="default",
        enabled=True,
        webhook_url="https://discord.com/api/webhooks/123456/secret-token",
        interval_seconds=60,
        env_path=env_path,
    )
    save_discord_stats_config(config)

    snapshot = public_stats.PublicStatsSnapshot(
        instance="default",
        generated_at="2026-06-26T10:00:00+00:00",
        lifecycle="running",
        running=True,
        service_state="active",
        server_name="Public Server",
        scenario_id="{ECC61978EDCC2B5A}Missions/23_Campaign.conf",
        map_name="#AR-Campaign_ScenarioName_Everon",
        players_available=True,
        player_count=3,
        max_players=64,
        player_names=("Denis",),
        roster_available=True,
        fps_available=True,
        fps_stale=False,
        fps_text="60.0",
        telemetry_age_text="5s",
        mods_available=True,
        mod_count=12,
        mod_preview=("Weapons",),
        remaining_mod_count=11,
    )
    monkeypatch.setattr("armactl.discord_stats.load_public_stats", lambda instance: snapshot)

    calls: list[tuple[str, str, dict[str, object]]] = []

    def fake_send(method, url, payload, *, timeout=15.0):
        calls.append((method, url, payload))
        if method == "POST":
            return {"id": "message-1"}
        if method == "PATCH":
            return {"id": "message-1"}
        raise AssertionError(method)

    monkeypatch.setattr("armactl.discord_stats._send_json_request", fake_send)

    created = publish_discord_stats(
        "default",
        config=load_discord_stats_config("default", env_path=env_path),
    )
    updated_config = load_discord_stats_config("default", env_path=env_path)
    updated = publish_discord_stats("default", config=updated_config)

    assert created.created is True
    assert updated.updated is True
    assert updated_config.message_id == "message-1"
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("?wait=true")
    assert calls[0][2]["allowed_mentions"] == {"parse": []}
    assert calls[1][0] == "PATCH"
    assert calls[1][1].endswith("/messages/message-1")
    assert "Public Server" in str(calls[1][2]["content"])


def test_publish_discord_stats_does_not_duplicate_on_transient_edit_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.discord_stats import (
        DiscordStatsConfig,
        DiscordStatsPublishError,
        load_discord_stats_config,
        publish_discord_stats,
        save_discord_stats_config,
    )

    env_path = tmp_path / "discord-stats.env"
    save_discord_stats_config(
        DiscordStatsConfig(
            instance="default",
            enabled=True,
            webhook_url="https://discord.com/api/webhooks/123456/secret-token",
            interval_seconds=60,
            message_id="message-1",
            env_path=env_path,
        )
    )
    snapshot = public_stats.PublicStatsSnapshot(
        instance="default",
        generated_at="2026-06-26T10:00:00+00:00",
        lifecycle="running",
        running=True,
        service_state="active",
        server_name="Public Server",
        scenario_id="{ECC61978EDCC2B5A}Missions/23_Campaign.conf",
        map_name="#AR-Campaign_ScenarioName_Everon",
        players_available=True,
        player_count=3,
        max_players=64,
        player_names=(),
        roster_available=True,
        fps_available=True,
        fps_stale=False,
        fps_text="60.0",
        telemetry_age_text="5s",
        mods_available=True,
        mod_count=12,
        mod_preview=(),
        remaining_mod_count=0,
    )
    monkeypatch.setattr("armactl.discord_stats.load_public_stats", lambda instance: snapshot)

    calls: list[str] = []

    def fail_patch(method, url, payload, *, timeout=15.0):
        calls.append(method)
        raise DiscordStatsPublishError("Discord webhook request failed.", status_code=500)

    monkeypatch.setattr("armactl.discord_stats._send_json_request", fail_patch)

    try:
        publish_discord_stats(
            "default",
            config=load_discord_stats_config("default", env_path=env_path),
        )
    except DiscordStatsPublishError as error:
        assert error.status_code == 500
    else:
        raise AssertionError("expected DiscordStatsPublishError")

    assert calls == ["PATCH"]


def test_render_discord_stats_service_unit_contains_execstart(tmp_path: Path, monkeypatch) -> None:
    from armactl import discord_stats

    python_bin = tmp_path / ".venv" / "bin" / "python"
    monkeypatch.setattr(discord_stats, "discord_stats_python_path", lambda: python_bin)
    monkeypatch.setattr(discord_stats, "resolve_linux_user", lambda: "defenders88")

    text = discord_stats.render_discord_stats_service_unit("default")

    assert "Description=armactl Discord Statistics Publisher (default)" in text
    assert "User=defenders88" in text
    assert f"ExecStart={python_bin} -m armactl --instance default stats discord run" in text
    assert "Restart=always" in text


def test_validate_discord_stats_service_config_requires_enabled(monkeypatch) -> None:
    from armactl import discord_stats

    monkeypatch.setattr(
        discord_stats,
        "load_discord_stats_config",
        lambda instance: discord_stats.DiscordStatsConfig(
            instance=instance,
            enabled=False,
            webhook_url="https://discord.com/api/webhooks/123456/secret-token",
        ),
    )

    errors = discord_stats.validate_discord_stats_service_config("default")

    assert "Discord statistics publishing must be enabled" in errors[0]


def test_stats_discord_service_status_cli(monkeypatch) -> None:
    from armactl import discord_stats

    monkeypatch.setattr(
        discord_stats,
        "get_discord_stats_service_status",
        lambda: {
            "service_name": "armactl-discord-stats.service",
            "service_file": "/etc/systemd/system/armactl-discord-stats.service",
            "installed": True,
            "active": True,
            "enabled": True,
            "active_state": "active",
            "main_pid": 123,
            "runtime": {"success": True, "message": "Discord stats runtime is ready."},
        },
    )

    result = CliRunner().invoke(main, ["stats", "discord", "service", "status"])

    assert result.exit_code == 0
    assert "Discord statistics service status." in result.output
    assert "armactl-discord-stats.service" in result.output
    assert "Active:         yes" in result.output
