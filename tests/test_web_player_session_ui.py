"""Focused Slice 6c route, template, pagination, and read-only regressions."""

from __future__ import annotations

import html
import re
import sqlite3
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.exceptions import StarletteDeprecationWarning

from armactl import player_log_events
from armactl.web.auth.setup import setup_owner_user
from armactl.web.i18n import LANGUAGE_COOKIE_NAME
from armactl.web.services import (
    player_registry,
    player_session_stats,
)

PLAYER_ALPHA_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_BRAVO_ID = "22222222-2222-4222-8222-222222222222"
PLAYER_CHARLIE_ID = "33333333-3333-4333-8333-333333333333"


def _client(app):
    with warnings.catch_warnings():
        warnings.simplefilter("error", StarletteDeprecationWarning)
        from fastapi.testclient import TestClient

        return TestClient(app)


def _form_token(body: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', body)
    assert match is not None
    return match.group(1)


def _login(client, username: str, password: str):
    token = _form_token(client.get("/login").text)
    return client.post(
        "/login",
        data={
            "username": username,
            "password": password,
            "csrf_token": token,
        },
        follow_redirects=False,
    )


def _authed_client(tmp_path: Path):
    from armactl.web.app import create_app

    setup_owner_user(tmp_path, "owner", "player session ui password")
    client = _client(create_app(data_root=tmp_path))
    response = _login(client, "owner", "player session ui password")
    assert response.status_code == 303
    return client


def _open_session(
    db_path: Path,
    reliable_id: str = PLAYER_ALPHA_ID,
    *,
    name: str = "Alpha",
    opened_at: str,
    source: str = player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
    **kwargs,
) -> player_registry.PlayerSessionRecord:
    result = player_registry.observe_player_session(
        db_path,
        reliable_id=reliable_id,
        display_name=name,
        source=source,
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
        observed_at=opened_at,
        **kwargs,
    )
    assert result.session is not None
    return result.session


def _close_session(
    db_path: Path,
    reliable_id: str = PLAYER_ALPHA_ID,
    *,
    closed_at: str,
    source: str = player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
    reason: str = player_registry.PLAYER_SESSION_END_REASON_STALE_ABSENCE,
) -> player_registry.PlayerSessionRecord:
    result = player_registry.close_player_session(
        db_path,
        reliable_id=reliable_id,
        close_observed_at=closed_at,
        source=source,
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
        end_reason=reason,
    )
    assert result.session is not None
    return result.session


def _event(
    event_type: str,
    occurred_at: str,
    **kwargs,
) -> player_log_events.PlayerLogEvent:
    source = (
        player_log_events.SOURCE_SCRIPT_FACTION_JOIN
        if event_type == player_log_events.EVENT_TYPE_FACTION_JOIN
        else player_log_events.SOURCE_SCRIPT_KILL
    )
    return player_log_events.PlayerLogEvent(
        event_type=event_type,
        source=source,
        confidence=player_log_events.CONFIDENCE_HIGH,
        occurred_at=occurred_at,
        observed_at=occurred_at,
        time_source=player_log_events.EVENT_TIME_SOURCE_CALLER_OCCURRED_AT,
        time_confidence=kwargs.pop(
            "time_confidence",
            player_log_events.EVENT_TIME_CONFIDENCE_EXACT,
        ),
        raw_source_ref=kwargs.pop(
            "raw_source_ref",
            "/private/player.log:42?token=secret",
        ),
        **kwargs,
    )


def _record_freshness(
    db_path: Path,
    *,
    covered_from: str,
    covered_through: str,
) -> None:
    scope = player_session_stats.CURRENT_STATS_INGEST_SCOPE
    player_registry.upsert_player_log_ingest_checkpoints(
        db_path,
        [
            player_registry.PlayerLogIngestCheckpoint(
                scope=scope,
                source_key="ui-fixture",
                source_label="allowlisted console log",
                size_bytes=64,
                mtime_ns=1,
                fingerprint="ui-fixture",
                status="scanned",
                last_scanned_at=covered_through,
                updated_at=covered_through,
                next_offset=64,
                coverage_started_at=covered_from,
            )
        ],
    )
    player_registry.record_player_log_ingest_freshness(
        db_path,
        scope=scope,
        status=player_registry.PLAYER_LOG_INGEST_STATUS_FRESH,
        last_run_at=covered_through,
        scanned_files=1,
        parsed_events=1,
        stored_events=1,
        skipped_files=0,
        checkpoint_updated=True,
        coverage_started_at=covered_from,
    )


def _recent_times() -> tuple[str, str, str, str, str]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return tuple(
        (now - timedelta(minutes=minutes)).isoformat()
        for minutes in (6, 5, 4, 3, 1)
    )


def _href_with_text(body: str, text: str) -> str:
    match = re.search(
        rf'href="([^"]+)"[^>]*>{re.escape(text)}</a>',
        body,
    )
    assert match is not None
    return html.unescape(match.group(1))


def test_detail_route_auth_permission_and_normal_access(
    tmp_path: Path,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app

    unauthenticated = _client(create_app(data_root=tmp_path))
    response = unauthenticated.get(
        "/players/sessions/1",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    setup_owner_user(tmp_path, "owner", "permission password")
    set_web_owner_permissions(set())
    denied = _client(create_app(data_root=tmp_path))
    _login(denied, "owner", "permission password")
    denied_response = denied.get(
        "/players/sessions/1",
        follow_redirects=False,
    )
    assert denied_response.status_code == 403
    assert denied_response.text == "Permission denied."

    set_web_owner_permissions({"players:view"})
    allowed = _client(create_app(data_root=tmp_path))
    _login(allowed, "owner", "permission password")
    allowed_response = allowed.get(
        "/players/sessions/1",
        follow_redirects=False,
    )
    assert allowed_response.status_code == 200
    assert "Player session detail is unavailable." in allowed_response.text
    assert 'href="/players/bans"' not in allowed_response.text

    set_web_owner_permissions({"players:view", "players:moderate"})
    moderator = _client(create_app(data_root=tmp_path))
    _login(moderator, "owner", "permission password")
    moderator_response = moderator.get(
        "/players/sessions/1",
        follow_redirects=False,
    )
    assert moderator_response.status_code == 200
    assert 'href="/players/bans"' in moderator_response.text


def test_list_and_detail_gets_are_query_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from armactl.web.jobs import player_sessions as player_session_jobs
    from armactl.web.services import (
        player_live_session_scanner,
        player_sessionizer,
    )

    client = _authed_client(tmp_path)
    missing_db = tmp_path / "default" / "players.db"
    assert client.get("/players/sessions").status_code == 200
    assert client.get("/players/sessions/1").status_code == 200
    assert not missing_db.exists()

    opened, _event_one, _event_two, _closed, _fresh = _recent_times()
    session = _open_session(missing_db, opened_at=opened)
    before_bytes = missing_db.read_bytes()
    before_mtime = missing_db.stat().st_mtime_ns

    def fail(*args, **kwargs):
        raise AssertionError("player-session GET attempted mutation work")

    monkeypatch.setattr(player_registry, "ensure_player_registry_db", fail)
    monkeypatch.setattr(player_registry, "ingest_player_log_events", fail)
    monkeypatch.setattr(
        player_live_session_scanner,
        "scan_live_player_sessions_once",
        fail,
    )
    monkeypatch.setattr(
        player_sessionizer,
        "sessionize_stored_player_log_events",
        fail,
    )
    for worker_name in (
        "start_player_live_session_scan_worker",
        "start_player_log_sessionization_worker",
        "start_player_session_maintenance_worker",
    ):
        monkeypatch.setattr(player_session_jobs, worker_name, fail)

    assert client.get("/players/sessions").status_code == 200
    assert client.get(f"/players/sessions/{session.session_id}").status_code == 200
    assert missing_db.read_bytes() == before_bytes
    assert missing_db.stat().st_mtime_ns == before_mtime


def test_list_search_filters_and_query_state_are_integrated(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    opened, alias_at, closed, later, _fresh = _recent_times()
    alpha = _open_session(
        db_path,
        PLAYER_ALPHA_ID,
        name="Alpha Base",
        opened_at=opened,
    )
    _close_session(
        db_path,
        PLAYER_ALPHA_ID,
        closed_at=closed,
    )
    _open_session(
        db_path,
        PLAYER_BRAVO_ID,
        name="Bravo Base",
        opened_at=later,
    )
    for reliable_id in (PLAYER_ALPHA_ID, PLAYER_BRAVO_ID):
        player_registry.record_current_players_snapshot(
            db_path,
            [
                player_registry.PlayerObservation(
                    reliable_id=reliable_id,
                    display_name="Shared Alias",
                    source=player_registry.PLAYER_SESSION_SOURCE_RCON_ROSTER,
                )
            ],
            observed_at=alias_at,
        )
    client = _authed_client(tmp_path)

    aliases = client.get(
        "/players/sessions",
        params={"q": "Shared Alias"},
    )
    assert aliases.status_code == 200
    assert "Alpha Base" in aliases.text
    assert "Bravo Base" in aliases.text
    assert aliases.text.count(">Open session</a>") == 2

    filtered = client.get(
        "/players/sessions",
        params={
            "q": "Shared Alias",
            "player_id": PLAYER_ALPHA_ID,
            "status": "closed",
            "end_reason": "stale_absence",
            "source": "scanner.checkpoint",
            "from": opened,
            "to": closed,
            "limit": "7",
        },
    )
    assert filtered.status_code == 200
    assert "Alpha Base" in filtered.text
    assert "Bravo Base" not in filtered.text
    assert f'value="{PLAYER_ALPHA_ID}"' in filtered.text
    assert 'value="closed" selected' in filtered.text
    assert 'value="stale_absence" selected' in filtered.text
    assert 'value="scanner.checkpoint" selected' in filtered.text
    assert f'name="from" value="{opened}"' in filtered.text
    assert f'name="to" value="{closed}"' in filtered.text
    detail_url = _href_with_text(filtered.text, "Open session")
    detail_query = parse_qs(urlsplit(detail_url).query)
    assert detail_url.startswith(f"/players/sessions/{alpha.session_id}?")
    assert detail_query == {
        "q": ["Shared Alias"],
        "player_id": [PLAYER_ALPHA_ID],
        "status": ["closed"],
        "end_reason": ["stale_absence"],
        "source": ["scanner.checkpoint"],
        "from": [opened],
        "to": [closed],
        "limit": ["7"],
    }


@pytest.mark.parametrize(
    "params",
    [
        {"status": "invalid"},
        {"player_id": "not-a-reliable-id"},
        {"from": "2026-07-10T12:00:00"},
        {"before_time": "bad", "before_session_id": "1"},
        {"before_time": "2026-07-10T12:00:00+00:00"},
    ],
)
def test_invalid_list_filters_and_cursors_fail_closed(
    tmp_path: Path,
    params: dict[str, str],
):
    db_path = tmp_path / "default" / "players.db"
    opened, *_rest = _recent_times()
    _open_session(db_path, name="Must Not Leak Into Invalid Results", opened_at=opened)
    client = _authed_client(tmp_path)

    response = client.get("/players/sessions", params=params)

    assert response.status_code == 200
    assert "Must Not Leak Into Invalid Results" not in response.text
    assert (
        "Session filters are invalid." in response.text
        or "This older-page link is invalid." in response.text
    )


def test_session_keyset_older_link_preserves_allowlisted_filters(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for index, reliable_id in enumerate(
        (PLAYER_ALPHA_ID, PLAYER_BRAVO_ID, PLAYER_CHARLIE_ID)
    ):
        opened_at = (now - timedelta(minutes=10 - index)).isoformat()
        _open_session(
            db_path,
            reliable_id,
            name=f"Pager {index}",
            opened_at=opened_at,
        )
    client = _authed_client(tmp_path)

    first = client.get(
        "/players/sessions",
        params={"q": "Pager", "status": "open", "limit": "2"},
    )
    older_url = _href_with_text(first.text, "Older")
    older_query = parse_qs(urlsplit(older_url).query)
    assert older_query["q"] == ["Pager"]
    assert older_query["status"] == ["open"]
    assert older_query["limit"] == ["2"]
    assert set(older_query) == {
        "q",
        "status",
        "limit",
        "before_time",
        "before_session_id",
    }
    refresh_url = _href_with_text(first.text, "Refresh list")
    refresh_query = parse_qs(urlsplit(refresh_url).query)
    assert refresh_query == {
        "q": ["Pager"],
        "status": ["open"],
        "limit": ["2"],
    }

    second = client.get(older_url)
    assert second.status_code == 200
    first_names = {
        name
        for name in ("Pager 0", "Pager 1", "Pager 2")
        if name in first.text
    }
    second_names = {
        name
        for name in ("Pager 0", "Pager 1", "Pager 2")
        if name in second.text
    }
    assert len(first_names) == 2
    assert len(second_names) == 1
    assert first_names.isdisjoint(second_names)


def test_session_page_hides_manual_tools_and_filters_until_needed(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    opened, *_rest = _recent_times()
    _open_session(db_path, name="Disclosure Player", opened_at=opened)
    client = _authed_client(tmp_path)

    response = client.get("/players/sessions")

    assert response.status_code == 200
    assert (
        "Opening or refreshing this page reads the latest stored sessions; "
        "it does not run a scan."
    ) in response.text
    assert "Manual tools are only for diagnostics or recovery." in response.text
    assert (
        '<details class="player-session-disclosure '
        'player-session-tools-disclosure">'
    ) in response.text
    assert (
        '<details class="player-session-disclosure '
        'player-session-filter-disclosure">'
    ) in response.text
    assert 'action="/players/sessions/scan-live"' in response.text
    assert 'action="/players/sessions/sessionize-log-events"' in response.text
    assert 'action="/players/sessions/maintenance"' in response.text

    filtered = client.get(
        "/players/sessions",
        params={"q": "Disclosure Player"},
    )
    assert filtered.status_code == 200
    assert (
        '<details class="player-session-disclosure '
        'player-session-filter-disclosure" open>'
    ) in filtered.text


def test_detail_invalid_not_found_and_legacy_states_are_sanitized(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    opened, *_rest = _recent_times()
    session = _open_session(db_path, opened_at=opened)
    client = _authed_client(tmp_path)

    invalid = client.get("/players/sessions/not-an-id")
    missing = client.get(f"/players/sessions/{session.session_id + 100}")
    assert invalid.status_code == 404
    assert missing.status_code == 404
    assert "Player session not found." in invalid.text
    assert "invalid_id" not in invalid.text
    assert "ValueError" not in invalid.text

    legacy_root = tmp_path / "legacy-root"
    legacy_db = legacy_root / "default" / "players.db"
    legacy_db.parent.mkdir(parents=True)
    with sqlite3.connect(legacy_db) as connection:
        connection.execute(
            "CREATE TABLE player_sessions("
            "session_id INTEGER PRIMARY KEY, reliable_id TEXT NOT NULL)"
        )
    legacy_client = _authed_client(legacy_root)
    legacy = legacy_client.get("/players/sessions/1")
    assert legacy.status_code == 200
    assert "Player session detail is unavailable." in legacy.text
    assert "sqlite" not in legacy.text.lower()
    assert str(legacy_db) not in legacy.text


def test_detail_renders_proven_zero_reconnect_and_truth_safe_fields(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    opened, first_close, reconnected, gameplay, fresh = _recent_times()
    first = _open_session(
        db_path,
        name="Alpha First",
        opened_at=opened,
        source_ref="/private/open.log:1?token=secret",
        rpl_identity="rpl-secret",
        connection_id="connection-secret",
        session_player_id="session-player-secret",
        be_slot="17",
    )
    _close_session(
        db_path,
        closed_at=first_close,
        reason=player_registry.PLAYER_SESSION_END_REASON_DISCONNECT,
    )
    reopened = player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha Last",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
        observed_at=reconnected,
    )
    assert reopened.reconnected is True
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha Last",
        source=player_registry.PLAYER_SESSION_SOURCE_SCRIPT_FACTION_JOIN,
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_MEDIUM,
        observed_at=gameplay,
        faction="US_Army",
    )
    closed = _close_session(db_path, closed_at=fresh)
    player_registry.ingest_player_log_events(
        db_path,
        [
            _event(
                player_log_events.EVENT_TYPE_FACTION_JOIN,
                gameplay,
                player_id=PLAYER_ALPHA_ID,
                player_name="Alpha Last",
                player_faction="US_Army",
            )
        ],
        ingested_at=fresh,
    )
    _record_freshness(
        db_path,
        covered_from=(
            datetime.fromisoformat(opened) - timedelta(minutes=1)
        ).isoformat(),
        covered_through=fresh,
    )
    client = _authed_client(tmp_path)

    response = client.get(
        f"/players/sessions/{closed.session_id}",
        params={"q": "Alpha", "status": "closed", "limit": "25"},
    )

    assert response.status_code == 200
    body = response.text
    assert first.session_id == closed.session_id
    assert "Alpha Last" in body
    assert "First recorded nickname" in body
    assert "Alpha First" in body
    assert "Stored as closed from recorded evidence" in body
    assert "Reconnect merges" in body
    assert ">1</dd>" in body
    assert "Last reconnect evidence" in body
    assert "Last gameplay evidence" in body
    assert "US_Army" in body
    assert "Last-known faction (not guaranteed current)" in body
    assert body.count("<strong>0</strong>") == 3
    assert 'time data-local-time datetime="' in body
    back_url = _href_with_text(body, "Back to sessions")
    assert parse_qs(urlsplit(back_url).query) == {
        "q": ["Alpha"],
        "status": ["closed"],
        "limit": ["25"],
    }
    for forbidden in (
        PLAYER_ALPHA_ID,
        "/private/",
        "token=secret",
        "rpl-secret",
        "connection-secret",
        "session-player-secret",
        "server_run_key",
        "play_session_id",
        "K/D",
        "Role",
        "playtime",
        "exact joined",
    ):
        assert forbidden not in body


def test_detail_unavailable_stats_use_dashes_not_fake_zeroes(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    opened, *_rest = _recent_times()
    session = _open_session(db_path, opened_at=opened)
    client = _authed_client(tmp_path)

    response = client.get(f"/players/sessions/{session.session_id}")

    assert response.status_code == 200
    assert "Stats availability" in response.text
    assert (
        "Stats unavailable because player log ingest freshness is unavailable."
        in response.text
    )
    assert "<strong>0</strong>" not in response.text
    assert response.text.count("<strong>—</strong>") == 3
    assert "Session timeline is unavailable." in response.text


def test_timeline_is_bounded_paginated_scoped_and_redacted(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    opened, event_one, event_two, event_three, fresh = _recent_times()
    session = _open_session(db_path, opened_at=opened)
    player_registry.ingest_player_log_events(
        db_path,
        [
            _event(
                player_log_events.EVENT_TYPE_KILL,
                event_one,
                victim_id=PLAYER_BRAVO_ID,
                victim_name="Timeline Oldest Victim",
                instigator_id=PLAYER_ALPHA_ID,
                instigator_name="Alpha",
                damage_type="Rifle",
            ),
            _event(
                player_log_events.EVENT_TYPE_FACTION_JOIN,
                event_two,
                player_id=PLAYER_ALPHA_ID,
                player_name="Timeline Middle Player",
                player_faction="US",
            ),
            _event(
                player_log_events.EVENT_TYPE_KILL,
                event_three,
                victim_id=PLAYER_ALPHA_ID,
                victim_name="Timeline Newest Victim",
                instigator_id=PLAYER_BRAVO_ID,
                instigator_name="Bravo",
                hit_zone="Torso",
            ),
            _event(
                player_log_events.EVENT_TYPE_KILL,
                fresh,
                victim_id=PLAYER_BRAVO_ID,
                victim_name="Wrong Player Event",
                instigator_id=PLAYER_CHARLIE_ID,
                instigator_name="Charlie",
            ),
            _event(
                player_log_events.EVENT_TYPE_KILL,
                event_two,
                victim_id=PLAYER_BRAVO_ID,
                victim_name="Ambiguous Event",
                instigator_id=PLAYER_ALPHA_ID,
                instigator_name="Alpha",
                time_confidence=(
                    player_log_events.EVENT_TIME_CONFIDENCE_AMBIGUOUS
                ),
            ),
        ],
        ingested_at=fresh,
    )
    _record_freshness(
        db_path,
        covered_from=(
            datetime.fromisoformat(opened) - timedelta(minutes=1)
        ).isoformat(),
        covered_through=fresh,
    )
    client = _authed_client(tmp_path)

    first = client.get(
        f"/players/sessions/{session.session_id}",
        params={"timeline_limit": "2", "q": "Alpha"},
    )
    assert first.status_code == 200
    assert "Timeline Newest Victim" in first.text
    assert "Timeline Middle Player" in first.text
    assert "Timeline Oldest Victim" not in first.text
    assert "Wrong Player Event" not in first.text
    assert "Ambiguous Event" not in first.text
    assert "data-player-history-row" in first.text
    assert 'data-player-history-panel="details" hidden' in first.text
    assert 'data-player-history-panel="diagnostics" hidden' in first.text

    older_url = _href_with_text(first.text, "Older events")
    older_query = parse_qs(urlsplit(older_url).query)
    assert older_query["q"] == ["Alpha"]
    assert older_query["timeline_limit"] == ["2"]
    assert set(older_query) == {
        "q",
        "limit",
        "timeline_limit",
        "timeline_before_time",
        "timeline_before_event_id",
    }
    older = client.get(older_url)
    assert older.status_code == 200
    assert "Timeline Oldest Victim" in older.text
    assert "Timeline Newest Victim" not in older.text
    rendered = first.text + older.text
    for forbidden in (
        PLAYER_ALPHA_ID,
        PLAYER_BRAVO_ID,
        PLAYER_CHARLIE_ID,
        "/private/",
        "token=secret",
        "source_ref",
        "connection_id",
        "rpl_identity",
        "session_player_id",
        "BattlEye",
        "server_run_key",
        "play_session_id",
    ):
        assert forbidden not in rendered


def test_timeline_invalid_cursor_and_unavailable_window_are_controlled(
    tmp_path: Path,
):
    db_path = tmp_path / "default" / "players.db"
    opened, event_one, _event_two, _event_three, fresh = _recent_times()
    session = _open_session(db_path, opened_at=opened)
    player_registry.ingest_player_log_events(
        db_path,
        [
            _event(
                player_log_events.EVENT_TYPE_FACTION_JOIN,
                event_one,
                player_id=PLAYER_ALPHA_ID,
                player_name="Timeline Cursor Event",
            )
        ],
        ingested_at=fresh,
    )
    _record_freshness(
        db_path,
        covered_from=(
            datetime.fromisoformat(opened) - timedelta(minutes=1)
        ).isoformat(),
        covered_through=fresh,
    )
    client = _authed_client(tmp_path)

    invalid = client.get(
        f"/players/sessions/{session.session_id}",
        params={
            "timeline_before_time": "bad",
            "timeline_before_event_id": "1",
        },
    )
    assert invalid.status_code == 200
    assert "This older-event link is invalid." in invalid.text
    assert "Timeline Cursor Event" not in invalid.text

    unavailable_root = tmp_path / "unavailable"
    unavailable_db = unavailable_root / "default" / "players.db"
    unavailable_session = _open_session(unavailable_db, opened_at=opened)
    unavailable_client = _authed_client(unavailable_root)
    unavailable = unavailable_client.get(
        f"/players/sessions/{unavailable_session.session_id}"
    )
    assert unavailable.status_code == 200
    assert "Session timeline is unavailable." in unavailable.text


def test_session_list_and_detail_render_in_ukrainian(tmp_path: Path):
    db_path = tmp_path / "default" / "players.db"
    opened, _event_one, _event_two, _event_three, _fresh = _recent_times()
    session = _open_session(db_path, opened_at=opened)
    client = _authed_client(tmp_path)
    client.cookies.set(LANGUAGE_COOKIE_NAME, "uk", path="/")

    session_list = client.get("/players/sessions")
    assert session_list.status_code == 200
    assert "Сесії гравців" in session_list.text

    detail = client.get(f"/players/sessions/{session.session_id}")
    assert detail.status_code == 200
    assert "Підсумок сесії" in detail.text
    assert "Хронологія сесії" in detail.text


def test_session_presentation_keeps_compact_columns_and_local_time_contract():
    repo_root = Path(__file__).parents[1]
    list_template = (
        repo_root / "src/armactl/web/templates/players_sessions.html"
    ).read_text(encoding="utf-8")
    detail_template = (
        repo_root / "src/armactl/web/templates/player_session_detail.html"
    ).read_text(encoding="utf-8")
    css = (repo_root / "src/armactl/web/static/css/app.css").read_text(
        encoding="utf-8"
    )
    script = (
        repo_root / "src/armactl/web/static/js/players_sessions.js"
    ).read_text(encoding="utf-8")

    assert list_template.count("<th>") == 6
    assert '<th>{{ t("Player ID") }}</th>' not in list_template
    assert '<th>{{ t("Evidence source") }}</th>' not in list_template
    assert "data-player-session-row hidden" in list_template
    assert "Open session" in list_template
    assert "player-session-tools-disclosure" in list_template
    assert "player-session-filter-disclosure" in list_template
    assert "page.refresh_url" in list_template
    assert "format_web_timestamp" in list_template
    assert 'type="datetime-local"' in list_template
    assert 'type="hidden" name="from"' in list_template
    assert 'type="hidden" name="to"' in list_template
    assert "toISOString()" in script
    assert "hasExplicitTimezone" in script
    assert "@media (max-width: 760px)" in css
    assert ".player-session-disclosure" in css
    assert ".player-session-timeline-table" in css
    assert "format_web_timestamp" in detail_template
    assert "data-player-history-row" in detail_template
    assert "raw_source_ref" not in detail_template
    assert "play_session_id" not in detail_template
