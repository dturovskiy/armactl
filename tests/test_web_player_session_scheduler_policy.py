"""Tests for future automatic player-session scheduler policy constants."""

from __future__ import annotations

from datetime import timedelta

from armactl import player_current_cache_updater
from armactl.web.jobs import player_sessions
from armactl.web.services import player_registry
from armactl.web.services import player_session_scheduler_policy as policy


def test_future_session_scheduler_policy_is_disabled_and_conservative() -> None:
    policies = policy.automatic_job_policy_by_kind()

    assert policy.AUTOMATIC_SESSION_SCHEDULER_ENABLED is False
    assert set(policies) == {
        player_sessions.PLAYER_LIVE_SESSION_SCAN_JOB_KIND,
        player_sessions.PLAYER_LOG_SESSIONIZATION_JOB_KIND,
        player_sessions.PLAYER_SESSION_MAINTENANCE_JOB_KIND,
    }
    assert policy.AUTOMATIC_SESSION_JOB_KINDS == tuple(
        job_policy.job_kind for job_policy in policy.AUTOMATIC_SESSION_JOB_POLICIES
    )
    assert policy.CURRENT_ROSTER_CACHE_DEFAULT_INTERVAL == timedelta(
        seconds=player_current_cache_updater.DEFAULT_INTERVAL_SECONDS
    )
    assert (
        policies[player_sessions.PLAYER_LIVE_SESSION_SCAN_JOB_KIND].minimum_interval
        >= policy.CURRENT_ROSTER_CACHE_DEFAULT_INTERVAL * 2
    )
    assert all(
        job_policy.initial_failure_backoff <= job_policy.maximum_failure_backoff
        for job_policy in policies.values()
    )
    assert policy.ALLOWED_AUTOMATIC_CLOSE_END_REASONS == (
        player_registry.PLAYER_SESSION_END_REASON_DISCONNECT,
        player_registry.PLAYER_SESSION_END_REASON_SERVER_BOUNDARY,
        player_registry.PLAYER_SESSION_END_REASON_STALE_ABSENCE,
        player_registry.PLAYER_SESSION_END_REASON_STALE_TIMEOUT,
    )
    assert "a2s" in policy.COUNT_ONLY_EVIDENCE_SOURCES
    assert "source_ref" in policy.SANITIZED_ONLY_SESSION_FIELDS
    for forbidden in ("ip", "raw_log_line", "raw_log_path", "secret"):
        assert forbidden in policy.FORBIDDEN_AUTOMATIC_SESSION_DATA
    for forbidden in ("k_d", "role", "playtime", "banlist"):
        assert forbidden in policy.FORBIDDEN_AUTOMATIC_SESSION_FEATURES
