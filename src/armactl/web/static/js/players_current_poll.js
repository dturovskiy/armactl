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
      "Roster unavailable; A2S reports {count} current player(s).",
    emptyOnline: root.dataset.currentPlayersEmptyOnlineLabel || "No current players online.",
    emptySearch:
      root.dataset.currentPlayersEmptySearchLabel || "No current players match search.",
    emptyUnavailable:
      root.dataset.currentPlayersEmptyUnavailableLabel || "Current player list unavailable.",
    details: root.dataset.currentPlayersDetailsLabel || "Details",
    identityId: root.dataset.currentPlayersIdentityIdLabel || "Identity ID",
    noReliableId: root.dataset.currentPlayersNoReliableIdLabel || "No reliable ID",
    source: root.dataset.currentPlayersSourceLabel || "Source",
    stale: root.dataset.currentPlayersStaleLabel || "Stale",
    updated: root.dataset.currentPlayersUpdatedLabel || "Updated",
    unavailable: root.dataset.currentPlayersUnavailableLabel || "unavailable",
    unknown: root.dataset.currentPlayersUnknownLabel || "Unknown",
  };

  const searchInput = root.querySelector("[data-current-players-search]");
  const countSummary = root.querySelector("[data-current-players-count-summary]");
  const sourceNode = root.querySelector("[data-current-players-source]");
  const statusNode = root.querySelector("[data-current-players-status]");
  const ageNode = root.querySelector("[data-current-players-age]");
  const staleNode = root.querySelector("[data-current-players-stale]");
  const errorNode = root.querySelector("[data-current-players-error]");
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
      node.textContent = String(value || "");
    }
  }

  function setError(message) {
    if (!errorNode) {
      return;
    }
    const text = String(message || "");
    errorNode.textContent = text;
    errorNode.hidden = !text;
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

  function statusCell(data) {
    const cell = document.createElement("td");
    const pill = document.createElement("span");
    pill.className = "status-pill";
    pill.textContent = String(data.status || labels.unknown);
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

  function hasCurrentPlayerDetails(player, data) {
    return Boolean(
      player.reliable_id || player.source || data.updated_at || data.collected_at,
    );
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
    const source = String(player.source || "");
    if (source) {
      details.push(detailLine(labels.source, source));
    }
    const updatedAt = String(data.updated_at || data.collected_at || "");
    if (updatedAt) {
      details.push(detailLine(labels.updated, "", { valueNode: timestampNode(updatedAt) }));
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
    row.hidden = true;
    const cell = document.createElement("td");
    cell.colSpan = 3;
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
      actionsCell(hasDetails, detailsId),
    );
    if (!hasDetails) {
      return [mainRow];
    }
    return [mainRow, detailRow(player, data, detailsId)];
  }

  function emptyMessage(data) {
    const count = observedCount(data);
    if (count > 0 && data.roster_available === false) {
      return labels.countOnlyTemplate.replace("{count}", String(count));
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
    const target = document.getElementById(targetId);
    if (!target) {
      return;
    }
    const shouldOpen = target.hidden;
    target.hidden = !shouldOpen;
    setExpanded(button, shouldOpen);
  });

  function applyPayload(data) {
    if (!data || typeof data !== "object" || !Array.isArray(data.players)) {
      throw new Error("Current players payload is invalid.");
    }
    updateCountSummary(data);
    setText(sourceNode, data.source || labels.unavailable);
    setText(statusNode, data.status || labels.unknown);
    setText(ageNode, ageText(data.age_seconds));
    if (staleNode) {
      staleNode.textContent = labels.stale;
      staleNode.hidden = data.is_stale !== true;
    }
    setError(data.error || "");
    updateRows(data);
  }

  function markUnavailable() {
    setText(sourceNode, labels.unavailable);
    setText(statusNode, labels.unavailable);
    setText(ageNode, labels.unknown);
    if (staleNode) {
      staleNode.textContent = labels.stale;
      staleNode.hidden = false;
    }
    setError("");
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
