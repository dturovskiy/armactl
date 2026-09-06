"""Route and template tests for web management pages."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from web_route_helpers import _client, _form_token, _login, _session_cookie_name, _set_cookie

from armactl.state import PortInfo, ServerState
from armactl.web.auth.cookies import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from armactl.web.auth.setup import setup_owner_user
from armactl.web.auth.users import get_user_by_username
from armactl.web.i18n import LANGUAGE_COOKIE_NAME


def test_hidden_attribute_has_global_css_rule() -> None:
    css_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "armactl"
        / "web"
        / "static"
        / "css"
        / "app.css"
    )
    css = css_path.read_text()

    assert "[hidden]" in css
    assert "display: none !important;" in css


def _management_state(*, has_config: bool = True) -> ServerState:
    if not has_config:
        return ServerState()
    return ServerState(
        server_installed=True,
        binary_exists=True,
        config_exists=True,
        service_exists=True,
        timer_exists=True,
        server_running=True,
        instance_root="/srv/armactl-data/default",
        install_dir="/srv/armactl-data/default/server",
        config_path="/srv/armactl-data/default/config/config.json",
        ports=PortInfo(game=2400, a2s=17778, rcon=20000),
    )


def _config_data() -> dict:
    return {
        "bindPort": 2400,
        "a2s": {"port": 17778},
        "rcon": {"port": 20000, "password": "raw-rcon-secret"},
        "game": {
            "name": "Read Only Server",
            "scenarioId": "Scenario.conf",
            "maxPlayers": 42,
            "visible": True,
            "gameProperties": {"battlEye": False},
            "admins": [{"identityId": "ABC123"}],
        },
    }


def _install_management_page_fakes(monkeypatch, *, has_config: bool = True) -> None:
    from armactl.web.page_models import admins as admins_model
    from armactl.web.page_models import bot as bot_model
    from armactl.web.page_models import common as common_model
    from armactl.web.page_models import config as config_model
    from armactl.web.page_models import mods as mods_model
    from armactl.web.page_models import players as players_page_model

    monkeypatch.setattr(
        common_model.discovery,
        "discover",
        lambda instance, save=False: _management_state(has_config=has_config),
    )
    monkeypatch.setattr(config_model.config_manager, "load_config", lambda path: _config_data())
    monkeypatch.setattr(admins_model.config_manager, "load_config", lambda path: _config_data())
    monkeypatch.setattr(
        mods_model.mods_manager,
        "get_mods",
        lambda path: [{"modId": "mod-a", "name": "Mod A", "version": "1.0"}],
    )
    monkeypatch.setattr(
        mods_model.mods_state,
        "mods_state_path_for_config",
        lambda path: Path("/srv/armactl-data/default/mods-state.json"),
    )
    monkeypatch.setattr(
        mods_model.mods_state,
        "load_disabled_mods",
        lambda path: [{"modId": "mod-b", "name": "Mod B Disabled", "version": ""}],
    )
    monkeypatch.setattr(
        admins_model.admins_manager,
        "admins_state_path_for_config",
        lambda path: Path("/srv/armactl-data/default/config/admins-state.json"),
    )
    monkeypatch.setattr(
        admins_model.admins_manager,
        "load_admins",
        lambda path: [
            {"identityId": "ABC123", "name": "Local Captain", "source": "local"}
        ],
    )
    monkeypatch.setattr(
        players_page_model,
        "load_player_moderation_panel",
        lambda instance, query="": players_page_model.PlayerModerationPanel(
            available=True,
            query=query,
            players=(),
            total_count=0,
            filtered_count=0,
            source="rcon.roster",
            status="available",
            error="",
        ),
    )
    if has_config:
        monkeypatch.setattr(
            bot_model.bot_config,
            "load_bot_config",
            lambda instance: SimpleNamespace(
                enabled=True,
                token="raw-bot-token-secret",
                admin_chat_ids=["1", "2"],
                language="uk",
                env_path=Path("/srv/armactl-data/default/bot/.env"),
            ),
        )
        monkeypatch.setattr(
            bot_model.discord_stats,
            "load_discord_stats_config",
            lambda instance: SimpleNamespace(
                enabled=True,
                webhook_configured=lambda: True,
                masked_webhook_url=lambda: "configured (...hook)",
                interval_seconds=30,
                message_id="message-id",
                env_path=Path("/srv/armactl-data/default/bot/discord-stats.env"),
            ),
        )
    else:
        monkeypatch.setattr(
            bot_model.bot_config,
            "load_bot_config",
            lambda instance: (_ for _ in ()).throw(
                RuntimeError("config path is not available")
            ),
        )
        monkeypatch.setattr(
            bot_model.discord_stats,
            "load_discord_stats_config",
            lambda instance: (_ for _ in ()).throw(
                RuntimeError("discord stats config is not available")
            ),
        )
    monkeypatch.setattr(
        bot_model.paths,
        "bot_service_file",
        lambda: Path("/nonexistent/armactl-bot.service"),
    )
    monkeypatch.setattr(
        bot_model.paths,
        "discord_stats_service_file",
        lambda: Path("/nonexistent/armactl-discord-stats.service"),
    )


def _fail_if_management_page_loads(monkeypatch) -> None:
    from armactl.web.page_models import bot as bot_model
    from armactl.web.page_models import common as common_model

    def fail(*args, **kwargs):
        raise AssertionError("management page backend should not load")

    monkeypatch.setattr(common_model.discovery, "discover", fail)
    monkeypatch.setattr(bot_model.bot_config, "load_bot_config", fail)


def test_unauthenticated_management_pages_redirect_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    for path in ("/config", "/mods", "/admins", "/bot"):
        response = client.get(path, follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_authenticated_owner_can_view_management_pages(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner management password"
    setup_owner_user(tmp_path, "owner", password)
    _install_management_page_fakes(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    config_response = client.get("/config", follow_redirects=False)
    mods_response = client.get("/mods", follow_redirects=False)
    admins_response = client.get("/admins", follow_redirects=False)
    bot_response = client.get("/bot", follow_redirects=False)

    assert config_response.status_code == 200
    assert "Read Only Server" in config_response.text
    assert "Scenario.conf" in config_response.text
    assert mods_response.status_code == 200
    assert "mod-a" in mods_response.text
    assert "Mod A" in mods_response.text
    assert admins_response.status_code == 200
    assert "ABC123" in admins_response.text
    assert "Local Captain" in admins_response.text
    assert "/static/js/admins.js" in admins_response.text
    assert "data-admin-edit-form" in admins_response.text
    assert bot_response.status_code == 200
    assert "Token configured" in bot_response.text
    assert "raw-bot-token-secret" not in bot_response.text

    management_html = "\n".join(
        response.text
        for response in (config_response, mods_response, admins_response, bot_response)
    )
    for raw_path in (
        "/srv/armactl-data/default",
        "/srv/armactl-data/default/server",
        "/srv/armactl-data/default/config/config.json",
        "/srv/armactl-data/default/mods-state.json",
        "/srv/armactl-data/default/config/admins-state.json",
        "/srv/armactl-data/default/bot/.env",
    ):
        assert raw_path not in management_html
    assert "config.json" in config_response.text
    assert "instance config" in config_response.text
    assert "server install" in config_response.text
    assert "disabled mods state" in mods_response.text
    assert "admins-state.json" in admins_response.text
    assert "bot config" in bot_response.text


def test_management_permission_denied_returns_controlled_403_and_skips_backend(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app

    password = "owner management password"
    setup_owner_user(tmp_path, "owner", password)
    set_web_owner_permissions(set())
    _install_management_page_fakes(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    for path in ("/config", "/mods", "/admins", "/bot"):
        response = client.get(path, follow_redirects=False)

        assert response.status_code == 403
        assert response.text == "Permission denied."
        assert "Traceback" not in response.text



def test_management_pages_render_controlled_empty_states(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner management password"
    setup_owner_user(tmp_path, "owner", password)
    _install_management_page_fakes(monkeypatch, has_config=False)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    responses = {
        path: client.get(path, follow_redirects=False)
        for path in ("/config", "/mods", "/admins", "/bot")
    }

    assert responses["/config"].status_code == 200
    assert "Config data is unavailable." in responses["/config"].text
    assert "config path is not available" in responses["/config"].text
    assert "Mods data is unavailable." in responses["/mods"].text
    assert "Admins data is unavailable." in responses["/admins"].text
    assert "Bot data is unavailable." in responses["/bot"].text
    assert all("Traceback" not in response.text for response in responses.values())


def test_management_pages_render_ukrainian_labels(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner management password"
    setup_owner_user(tmp_path, "owner", password)
    _install_management_page_fakes(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    _set_cookie(client, LANGUAGE_COOKIE_NAME, "uk")

    config_response = client.get("/config", follow_redirects=False)
    mods_response = client.get("/mods", follow_redirects=False)
    admins_response = client.get("/admins", follow_redirects=False)
    bot_response = client.get("/bot", follow_redirects=False)

    assert config_response.status_code == 200
    assert "Конфіг сервера" in config_response.text
    assert "Активні моди" in mods_response.text
    assert "Офіційні ігрові адміни" in admins_response.text
    assert "Стан Telegram-бота" in bot_response.text


def test_management_pages_do_not_render_secrets(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner management password"
    setup_owner_user(tmp_path, "owner", password)
    user = get_user_by_username(tmp_path / "web" / "web.db", "owner")
    assert user is not None
    _install_management_page_fakes(monkeypatch)
    client = _client(create_app(data_root=tmp_path))
    login_response = _login(client, "owner", password)
    session_token = login_response.cookies.get(_session_cookie_name(client))

    html = "\n".join(
        client.get(path, follow_redirects=False).text
        for path in ("/config", "/mods", "/admins", "/bot")
    )

    assert password not in html
    assert user.password_hash not in html
    assert session_token
    assert session_token not in html
    assert "raw-rcon-secret" not in html
    assert "raw-bot-token-secret" not in html
    assert SESSION_COOKIE_NAME not in html
    assert CSRF_COOKIE_NAME not in html


def test_bot_discord_settings_post_does_not_echo_webhook_secret(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.routes import bot as bot_route
    from armactl.web.services.discord_stats_actions import DiscordStatsSettingsResult

    password = "owner management password"
    setup_owner_user(tmp_path, "owner", password)
    _install_management_page_fakes(monkeypatch)
    captured: dict[str, object] = {}

    def fake_save_discord_settings(**kwargs):
        captured.update(kwargs)
        return DiscordStatsSettingsResult(
            success=True,
            message="Discord statistics settings saved.",
            enabled=bool(kwargs["enabled"]),
            interval_seconds=int(kwargs["interval_seconds"]),
            webhook_configured=True,
            webhook_changed=True,
        )

    monkeypatch.setattr(
        bot_route.discord_stats_actions,
        "save_discord_stats_settings_and_audit",
        fake_save_discord_settings,
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    form_response = client.get("/bot", follow_redirects=False)
    csrf_token = _form_token(form_response.text)

    webhook = "https://discord.com/api/webhooks/123456/super-secret-webhook-token"
    response = client.post(
        "/bot/discord",
        data={
            "csrf_token": csrf_token,
            "discord_enabled": "1",
            "discord_webhook_url": webhook,
            "discord_interval_seconds": "10",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert captured["enabled"] is True
    assert captured["webhook_url"] == webhook
    assert captured["interval_seconds"] == 10
    assert "Discord statistics settings saved." in response.text
    assert webhook not in response.text
    assert "super-secret-webhook-token" not in response.text


def test_bot_discord_settings_requires_manage_permission(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app
    from armactl.web.auth.permissions import BOT_VIEW
    from armactl.web.routes import bot as bot_route

    password = "owner management password"
    setup_owner_user(tmp_path, "owner", password)
    set_web_owner_permissions({BOT_VIEW})
    _install_management_page_fakes(monkeypatch)
    monkeypatch.setattr(
        bot_route.discord_stats_actions,
        "save_discord_stats_settings_and_audit",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not save")),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    form_response = client.get("/bot", follow_redirects=False)
    csrf_token = _form_token(form_response.text)

    response = client.post(
        "/bot/discord",
        data={
            "csrf_token": csrf_token,
            "discord_enabled": "1",
            "discord_webhook_url": "https://discord.com/api/webhooks/123456/secret",
            "discord_interval_seconds": "10",
        },
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Permission denied."

def test_bot_discord_action_post_runs_controlled_action(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.routes import bot as bot_route
    from armactl.web.services.discord_stats_actions import DiscordStatsActionResult

    password = "owner management password"
    setup_owner_user(tmp_path, "owner", password)
    _install_management_page_fakes(monkeypatch)
    captured: dict[str, object] = {}

    def fake_run_discord_action(**kwargs):
        captured.update(kwargs)
        return DiscordStatsActionResult(
            success=True,
            message="Discord statistics message published.",
            action=str(kwargs["action"]),
        )

    monkeypatch.setattr(
        bot_route.discord_stats_actions,
        "run_discord_stats_action_and_audit",
        fake_run_discord_action,
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    form_response = client.get("/bot", follow_redirects=False)
    csrf_token = _form_token(form_response.text)

    response = client.post(
        "/bot/discord/action",
        data={"csrf_token": csrf_token, "discord_action": "publish"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert captured["action"] == "publish"
    assert captured["username"] == "owner"
    assert "Discord statistics message published." in response.text
    assert "bot-control-grid" in response.text
    assert "bot-action-grid" in response.text
    assert "Publish now" in response.text
    assert "Install service" in response.text
    assert "Restart publisher" in response.text
    assert "action-danger" in response.text
    assert "Discord statistics service is not installed" in response.text
    assert 'value="restart" class="bot-action-button action-warning" disabled' in response.text


def test_bot_discord_action_requires_manage_permission(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app
    from armactl.web.auth.permissions import BOT_VIEW
    from armactl.web.routes import bot as bot_route

    password = "owner management password"
    setup_owner_user(tmp_path, "owner", password)
    set_web_owner_permissions({BOT_VIEW})
    _install_management_page_fakes(monkeypatch)
    monkeypatch.setattr(
        bot_route.discord_stats_actions,
        "run_discord_stats_action_and_audit",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not run")),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    form_response = client.get("/bot", follow_redirects=False)
    csrf_token = _form_token(form_response.text)

    response = client.post(
        "/bot/discord/action",
        data={"csrf_token": csrf_token, "discord_action": "publish"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Permission denied."
