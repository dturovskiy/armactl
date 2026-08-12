(() => {
  const root = document.querySelector("[data-current-players-root]");
  if (!root) {
    return;
  }

  const DEFAULT_POLL_INTERVAL_MS = 60000;
  const parsedInterval = Number.parseInt(
    root.dataset.currentPlayersIntervalMs || String(DEFAULT_POLL_INTERVAL_MS),
    10,
  );
  const intervalMs = Number.isFinite(parsedInterval)
    ? Math.max(parsedInterval, DEFAULT_POLL_INTERVAL_MS)
    : DEFAULT_POLL_INTERVAL_MS;
  const endpoint = root.dataset.currentPlayersEndpoint || "/players/current.json";

  const labels = {
    ageTemplate: root.dataset.currentPlayersAgeTemplate || "{seconds}s ago",
    countTemplate:
      root.dataset.currentPlayersCountTemplate ||
      "Showing {count} of {total} current player(s)",
    countOnlyTemplate:
      root.dataset.currentPlayersCountOnlyTemplate ||
      "Named roster unavailable; {source} reports {count} current player(s). No player names are available.",
    staleWarning:
      root.dataset.currentPlayersStaleWarningLabel ||
      "Showing cached named roster because the live roster source is unavailable. These rows are stale and not guaranteed live.",
    unavailableWarning:
      root.dataset.currentPlayersUnavailableWarningLabel ||
      "Live roster source unavailable; keeping the previously rendered rows. They are not guaranteed live.",
    emptyOnline: root.dataset.currentPlayersEmptyOnlineLabel || "No current players online.",
    emptySearch:
      root.dataset.currentPlayersEmptySearchLabel || "No current players match search.",
    emptyUnavailable:
      root.dataset.currentPlayersEmptyUnavailableLabel || "Current player list unavailable.",
    details: root.dataset.currentPlayersDetailsLabel || "Details",
    identityId: root.dataset.currentPlayersIdentityIdLabel || "Identity ID",
    noReliableId: root.dataset.currentPlayersNoReliableIdLabel || "No reliable ID",
    placeholder: root.dataset.currentPlayersPlaceholderLabel || "—",
    rosterSource: root.dataset.currentPlayersRosterSourceLabel || "RCON roster",
    source: root.dataset.currentPlayersSourceLabel || "Source",
    stale: root.dataset.currentPlayersStaleLabel || "Stale",
    staleRoster: root.dataset.currentPlayersStaleRosterLabel || "Stale roster",
    fresh: root.dataset.currentPlayersFreshLabel || "Fresh",
    available: root.dataset.currentPlayersAvailableLabel || "Available",
    technicalSource: root.dataset.currentPlayersTechnicalSourceLabel || "Technical source",
    updated: root.dataset.currentPlayersUpdatedLabel || "Updated",
    firstObserved:
      root.dataset.currentPlayersFirstObservedLabel || "Session first observed",
    statsSource: root.dataset.currentPlayersStatsSourceLabel || "Stats source",
    statsSourceValue:
      root.dataset.currentPlayersStatsSourceValue || "Stored fresh play-session log evidence",
    statsAvailability:
      root.dataset.currentPlayersStatsAvailabilityLabel || "Stats availability",
    statsUnavailable:
      root.dataset.currentPlayersStatsUnavailableLabel ||
      "Stats unavailable because proven play-session and fresh log coverage are not available.",
    statsFreshness:
      root.dataset.currentPlayersStatsFreshnessLabel || "Log ingest freshness",
    statsWindowStart:
      root.dataset.currentPlayersStatsWindowStartLabel || "Stats window start",
    statsWindowEnd:
      root.dataset.currentPlayersStatsWindowEndLabel || "Stats covered through",
    statsReconnect:
      root.dataset.currentPlayersStatsReconnectLabel || "Reconnect window",
    statsReconnectValue:
      root.dataset.currentPlayersStatsReconnectValue || "Merged within reconnect grace",
    factionEvidenceTitle:
      root.dataset.currentPlayersFactionEvidenceTitle ||
      "Last-known faction from fresh play-session log evidence; not guaranteed current.",
    unavailable: root.dataset.currentPlayersUnavailableLabel || "unavailable",
    unknown: root.dataset.currentPlayersUnknownLabel || "Unknown",
  };

  const searchInput = root.querySelector("[data-current-players-search]");
  const countSummary = root.querySelector("[data-current-players-count-summary]");
  const sourceNode = root.querySelector("[data-current-players-source]");
  const statusNode = root.querySelector("[data-current-players-status]");
  const cacheStatusNode = root.querySelector("[data-current-players-cache-status]");
  const ageNode = root.querySelector("[data-current-players-age]");
  const staleNode = root.querySelector("[data-current-players-stale]");
  const freshNode = root.querySelector("[data-current-players-fresh]");
  const freshnessUnavailableNode = root.querySelector(
    "[data-current-players-freshness-unavailable]",
  );
  const rosterAvailableNode = root.querySelector("[data-current-players-roster-available]");
  const countSourceNode = root.querySelector("[data-current-players-count-source]");
  const observedCountNode = root.querySelector("[data-current-players-observed-count]");
  const warningNode = root.querySelector("[data-current-players-warning]");
  const refreshErrorLabelNode = root.querySelector(
    "[data-current-players-refresh-error-label]",
  );
  const errorNode = root.querySelector("[data-current-players-error]");
  const errorItemNode = root.querySelector("[data-current-players-error-item]");
  const tableWrap = root.querySelector("[data-current-players-table]");
  const tableBody = root.querySelector("[data-current-players-tbody]");
  const emptyNode = root.querySelector("[data-current-players-empty]");

  function currentSearchQuery() {
    if (searchInput) {
      return searchInput.value || "";
    }
    return new URLSearchParams(window.location.search).get("player_search") || "";
  }

  function currentPlayersUrl() {
    const url = new URL(endpoint, window.location.origin);
    const query = currentSearchQuery().trim();
    if (query) {
      url.searchParams.set("player_search", query);
    } else {
      url.searchParams.delete("player_search");
    }
    return url;
  }

  function numericCount(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) {
      return 0;
    }
    return Math.trunc(number);
  }

  function boundedCount(value) {
    return String(numericCount(value));
  }

  function observedCount(data) {
    if (data && data.observed_count !== undefined) {
      return numericCount(data.observed_count);
    }
    return numericCount(data ? data.total_count : 0);
  }

  function updateCountSummary(data) {
    if (!countSummary) {
      return;
    }
    const totalCount =
      data.total_count !== undefined ? data.total_count : data.observed_count;
    countSummary.textContent = labels.countTemplate
      .replace("{count}", boundedCount(data.filtered_count))
      .replace("{total}", boundedCount(totalCount));
  }

  function ageText(value) {
    if (value === null || value === undefined) {
      return labels.unknown;
    }
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds < 0) {
      return labels.unknown;
    }
    return labels.ageTemplate.replace("{seconds}", String(Math.trunc(seconds)));
  }

  function setText(node, value) {
    if (node) {
      node.textContent = value === null || value === undefined ? "" : String(value);
    }
  }

  function statusPillTone(value) {
    const normalized = String(value || "")
      .trim()
      .toLowerCase()
      .replace(/[\s-]+/g, "_");
    if (
      ["active", "available", "fresh", "hit", "ok", "persistent", "refresh", "running", "success", "updated"].includes(
        normalized,
      )
    ) {
      return "success";
    }
    if (
      normalized.includes("stale") ||
      ["disabled", "incomplete", "starting", "updating", "warning"].includes(normalized)
    ) {
      return "warning";
    }
    if (
      normalized.includes("error") ||
      ["danger", "failed", "failure"].includes(normalized)
    ) {
      return "error";
    }
    return "unavailable";
  }

  function setStatusPill(node, value, tone = statusPillTone(value)) {
    if (!node) {
      return;
    }
    setText(node, value);
    node.className = `status-pill status-pill-${tone}`;
  }

  function setError(message) {
    if (!errorNode) {
      return;
    }
    const text = String(message || "");
    errorNode.textContent = text;
    errorNode.hidden = !text;
    if (refreshErrorLabelNode) {
      refreshErrorLabelNode.hidden = !text;
    }
    if (errorItemNode) {
      errorItemNode.hidden = !text;
    }
  }

  function clippedReliableId(value) {
    const id = String(value || "");
    if (id.length <= 14) {
      return id;
    }
    return id.slice(0, 8) + "..." + id.slice(-5);
  }

  function textCell(text, className) {
    const cell = document.createElement("td");
    const span = document.createElement(className === "strong" ? "strong" : "span");
    if (className && className !== "strong") {
      span.className = className;
    } else if (className === "strong") {
      span.className = "player-current-name wrap-value";
    }
    span.textContent = String(text || "");
    cell.append(span);
    return cell;
  }

  function isMissingValue(value) {
    return value === null || value === undefined || value === "";
  }

  function placeholderCell(className = "") {
    const cell = document.createElement("td");
    cell.className = ["player-current-placeholder", className]
      .filter(Boolean)
      .join(" ");
    cell.textContent = labels.placeholder;
    return cell;
  }

  function nullableTextCell(value, className = "", options = {}) {
    if (isMissingValue(value)) {
      return placeholderCell(className);
    }
    const cell = textCell(value, className);
    if (options.title) {
      cell.title = options.title;
    }
    return cell;
  }

  function statCell(value) {
    if (isMissingValue(value)) {
      return placeholderCell("player-stat-number");
    }
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return placeholderCell("player-stat-number");
    }
    const cell = document.createElement("td");
    cell.className = "player-stat-number";
    cell.textContent = String(Math.trunc(number));
    return cell;
  }

  function guardedStatCell(player, field) {
    if (!player || player.stats_available !== true) {
      return placeholderCell("player-stat-number");
    }
    return statCell(player[field]);
  }

  function guardedFactionCell(player) {
    if (!player || player.stats_available !== true) {
      return placeholderCell();
    }
    return nullableTextCell(player.faction, "", { title: labels.factionEvidenceTitle });
  }

  function statusCell(data) {
    const cell = document.createElement("td");
    const pill = document.createElement("span");
    const staleRoster = data.stale_named_roster === true;
    pill.className = staleRoster ? "status-pill status-pill-warning" : "status-pill";
    pill.textContent = staleRoster
      ? labels.staleRoster
      : String(data.status || labels.unknown);
    const freshness = document.createElement("span");
    freshness.className = "player-row-subtle";
    freshness.textContent = ageText(data.age_seconds);
    cell.append(pill, freshness);
    return cell;
  }

  function detailLine(label, value, options = {}) {
    const line = document.createElement("span");
    line.className = "player-diagnostic-line";
    const labelNode = document.createElement("strong");
    labelNode.textContent = label + ":";
    const valueNode = document.createElement("span");
    let renderedValue = valueNode;
    if (options.valueNode instanceof Node) {
      renderedValue = options.valueNode;
    } else {
      if (options.className) {
        valueNode.className = options.className;
      }
      if (options.title) {
        valueNode.title = options.title;
      }
      valueNode.textContent = String(value || "");
    }
    line.append(labelNode, document.createTextNode(" "), renderedValue);
    return line;
  }

  function timestampNode(value) {
    const timestamp = String(value || "");
    const timeNode = document.createElement("time");
    timeNode.dataset.localTime = "";
    timeNode.setAttribute("datetime", timestamp);
    timeNode.setAttribute("title", timestamp);
    const formatter = window.armactlFormatLocalTime;
    timeNode.textContent =
      typeof formatter === "function" ? formatter(timestamp) || timestamp : timestamp;
    return timeNode;
  }

  function humanSourceText(data) {
    const source = String((data && data.source) || "");
    if (source.toLowerCase().startsWith("rcon.roster") || data.roster_available === true) {
      return labels.rosterSource;
    }
    return source || labels.unavailable;
  }

  function hasCurrentPlayerDetails() {
    return true;
  }

  function currentPlayerDetails(player, data) {
    const details = [];
    const reliableId = String(player.reliable_id || "");
    if (reliableId) {
      details.push(
        detailLine(labels.identityId, clippedReliableId(reliableId), {
          className: "player-id-cell player-id-clip",
          title: reliableId,
        }),
      );
    } else {
      details.push(
        detailLine(labels.identityId, labels.noReliableId, { className: "muted" }),
      );
    }
    details.push(detailLine(labels.source, humanSourceText(data)));
    const source = String(player.source || "");
    if (source) {
      details.push(detailLine(labels.technicalSource, source));
    } else {
      details.push(
        detailLine(labels.technicalSource, labels.unknown, { className: "muted" }),
      );
    }
    const updatedAt = String(data.updated_at || data.collected_at || "");
    if (updatedAt) {
      details.push(detailLine(labels.updated, "", { valueNode: timestampNode(updatedAt) }));
    } else {
      details.push(detailLine(labels.updated, labels.unknown, { className: "muted" }));
    }
    const firstObservedAt = String(player.first_observed_at || "");
    if (firstObservedAt) {
      details.push(
        detailLine(labels.firstObserved, "", {
          valueNode: timestampNode(firstObservedAt),
        }),
      );
    } else {
      details.push(
        detailLine(labels.firstObserved, labels.placeholder, { className: "muted" }),
      );
    }
    if (player.stats_available === true) {
      details.push(
        detailLine(labels.statsSource, player.stats_source_label || labels.statsSourceValue),
      );
      const freshnessAt = String(player.stats_freshness_at || "");
      if (freshnessAt) {
        details.push(
          detailLine(labels.statsFreshness, "", { valueNode: timestampNode(freshnessAt) }),
        );
      }
      const windowStart = String(player.stats_window_started_at || "");
      if (windowStart) {
        details.push(
          detailLine(labels.statsWindowStart, "", { valueNode: timestampNode(windowStart) }),
        );
      }
      const windowEnd = String(player.stats_window_ended_at || "");
      if (windowEnd) {
        details.push(
          detailLine(labels.statsWindowEnd, "", { valueNode: timestampNode(windowEnd) }),
        );
      }
      if (player.stats_reconnect_merged === true) {
        details.push(detailLine(labels.statsReconnect, labels.statsReconnectValue));
      }
    } else {
      details.push(
        detailLine(
          labels.statsAvailability,
          player.stats_unavailable_reason || labels.statsUnavailable,
          { className: "muted" },
        ),
      );
      const freshnessAt = String(player.stats_freshness_at || "");
      if (freshnessAt) {
        details.push(
          detailLine(labels.statsFreshness, "", { valueNode: timestampNode(freshnessAt) }),
        );
      }
    }
    return details;
  }

  function actionsCell(hasDetails, detailsId) {
    const cell = document.createElement("td");
    const actions = document.createElement("div");
    actions.className = "player-current-actions";
    if (hasDetails) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button-link secondary-link player-current-action-button";
      button.dataset.currentPlayerToggle = "";
      button.dataset.currentPlayerTarget = detailsId;
      button.setAttribute("aria-controls", detailsId);
      button.setAttribute("aria-expanded", "false");
      button.textContent = labels.details;
      actions.append(button);
    } else {
      const empty = document.createElement("span");
      empty.className = "muted";
      empty.textContent = "-";
      actions.append(empty);
    }
    cell.append(actions);
    return cell;
  }

  function detailRow(player, data, detailsId) {
    const row = document.createElement("tr");
    row.id = detailsId;
    row.className = "player-current-details-row";
    row.dataset.currentPlayerRow = "";
    row.setAttribute("hidden", "");
    const cell = document.createElement("td");
    cell.colSpan = 8;
    const panel = document.createElement("div");
    panel.className = "player-current-detail-panel";
    panel.setAttribute("role", "region");
    const detailCell = document.createElement("div");
    detailCell.className = "player-current-detail-cell";
    detailCell.append(...currentPlayerDetails(player, data));
    panel.append(detailCell);
    cell.append(panel);
    row.append(cell);
    return row;
  }

  function playerRows(player, index, data) {
    const detailsId = "current-player-details-poll-" + String(index);
    const hasDetails = hasCurrentPlayerDetails(player, data);
    const mainRow = document.createElement("tr");
    mainRow.className = "player-current-main-row";
    mainRow.append(
      textCell(player.display_name || "", "strong"),
      statusCell(data),
      guardedStatCell(player, "kills"),
      guardedStatCell(player, "deaths"),
      guardedStatCell(player, "teamkills"),
      guardedFactionCell(player),
      placeholderCell(),
      actionsCell(hasDetails, detailsId),
    );
    if (!hasDetails) {
      return [mainRow];
    }
    return [mainRow, detailRow(player, data, detailsId)];
  }

  function countSourceText(data) {
    return String((data && data.count_source) || labels.unknown);
  }

  function countOnlyMessage(data) {
    return labels.countOnlyTemplate
      .replace("{source}", countSourceText(data))
      .replace("{count}", String(observedCount(data)));
  }

  function warningMessage(data, preserveRenderedRows) {
    if (data.stale_named_roster === true) {
      return labels.staleWarning;
    }
    if (observedCount(data) > 0 && data.roster_available === false) {
      return countOnlyMessage(data);
    }
    if (data.roster_available === false && data.available !== true) {
      return preserveRenderedRows
        ? labels.unavailableWarning
        : labels.emptyUnavailable;
    }
    return "";
  }

  function setWarning(message) {
    if (!warningNode) {
      return;
    }
    const text = String(message || "");
    warningNode.textContent = text;
    warningNode.hidden = !text;
  }

  function shouldPreserveRenderedRows(data) {
    const players = Array.isArray(data.players) ? data.players : [];
    const hasRenderedRows = Boolean(
      tableBody && tableBody.querySelector(".player-current-main-row"),
    );
    return (
      hasRenderedRows &&
      players.length === 0 &&
      data.stale_named_roster !== true &&
      data.roster_available === false &&
      observedCount(data) === 0 &&
      data.available !== true
    );
  }

  function emptyMessage(data) {
    const count = observedCount(data);
    if (
      count > 0 &&
      data.roster_available === false &&
      data.stale_named_roster !== true
    ) {
      return countOnlyMessage(data);
    }
    if (currentSearchQuery().trim()) {
      return labels.emptySearch;
    }
    if (data.available === true) {
      return labels.emptyOnline;
    }
    return labels.emptyUnavailable;
  }

  function updateRows(data) {
    if (!tableBody || !tableWrap || !emptyNode) {
      return;
    }
    const players = Array.isArray(data.players) ? data.players : [];
    const rows = [];
    players.forEach((player, index) => {
      rows.push(...playerRows(player, index + 1, data));
    });
    tableBody.replaceChildren(...rows);
    tableWrap.hidden = players.length === 0;
    emptyNode.hidden = players.length > 0;
    if (players.length === 0) {
      emptyNode.textContent = emptyMessage(data);
    }
  }

  function setExpanded(button, expanded) {
    button.setAttribute("aria-expanded", expanded ? "true" : "false");
  }

  function setDetailsRowOpen(row, open) {
    if (open) {
      row.removeAttribute("hidden");
    } else {
      row.setAttribute("hidden", "");
    }
  }


  root.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    const button = target.closest("[data-current-player-toggle]");
    if (!button || !root.contains(button)) {
      return;
    }
    const targetId = button.dataset.currentPlayerTarget;
    if (!targetId) {
      return;
    }
    const detailsRow = document.getElementById(targetId);
    if (!detailsRow) {
      return;
    }
    const shouldOpen = detailsRow.hasAttribute("hidden");
    setDetailsRowOpen(detailsRow, shouldOpen);
    setExpanded(button, shouldOpen);
  });

  function applyPayload(data) {
    if (!data || typeof data !== "object" || !Array.isArray(data.players)) {
      throw new Error("Current players payload is invalid.");
    }
    const preserveRenderedRows = shouldPreserveRenderedRows(data);
    if (!preserveRenderedRows) {
      updateCountSummary(data);
      setText(sourceNode, data.source || labels.unavailable);
      setStatusPill(cacheStatusNode, data.cache_status || labels.unknown);
      const cacheAge =
        data.cache_age_seconds !== undefined ? data.cache_age_seconds : data.age_seconds;
      setText(ageNode, ageText(cacheAge));
      setText(countSourceNode, countSourceText(data));
      setText(observedCountNode, observedCount(data));
    }
    const status = data.status || labels.unknown;
    setStatusPill(
      statusNode,
      status,
      data.is_stale === true ? "warning" : statusPillTone(status),
    );
    const freshness = String(
      data.freshness ||
        (data.is_stale === true
          ? "stale"
          : data.available === true
            ? "fresh"
            : "unavailable"),
    );
    if (staleNode) {
      staleNode.textContent = labels.stale;
      staleNode.hidden = freshness !== "stale";
    }
    if (freshNode) {
      freshNode.textContent = labels.fresh;
      freshNode.hidden = freshness !== "fresh";
    }
    if (freshnessUnavailableNode) {
      freshnessUnavailableNode.textContent = labels.unavailable;
      freshnessUnavailableNode.hidden = freshness !== "unavailable";
    }
    const rosterAvailable = data.roster_available === true;
    setStatusPill(
      rosterAvailableNode,
      rosterAvailable ? labels.available : labels.unavailable,
      rosterAvailable ? "success" : "unavailable",
    );
    setError(data.refresh_error || data.error || "");
    setWarning(warningMessage(data, preserveRenderedRows));
    if (!preserveRenderedRows) {
      updateRows(data);
    }
  }

  function markUnavailable() {
    setStatusPill(statusNode, labels.unavailable, "unavailable");
    if (staleNode) {
      staleNode.textContent = labels.stale;
      staleNode.hidden = false;
    }
    if (freshNode) {
      freshNode.hidden = true;
    }
    if (freshnessUnavailableNode) {
      freshnessUnavailableNode.hidden = true;
    }
    setStatusPill(rosterAvailableNode, labels.unavailable, "unavailable");
    setWarning(labels.unavailableWarning);
  }

  async function refreshCurrentPlayers() {
    try {
      const response = await fetch(currentPlayersUrl().toString(), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error("Current players request failed: " + response.status);
      }
      applyPayload(await response.json());
    } catch (_error) {
      markUnavailable();
    }
  }

  window.setInterval(refreshCurrentPlayers, intervalMs);
})();
