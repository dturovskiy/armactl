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
    emptyOnline: root.dataset.currentPlayersEmptyOnlineLabel || "No current players online.",
    emptySearch:
      root.dataset.currentPlayersEmptySearchLabel || "No current players match search.",
    emptyUnavailable:
      root.dataset.currentPlayersEmptyUnavailableLabel || "Current player list unavailable.",
    noReliableId: root.dataset.currentPlayersNoReliableIdLabel || "No reliable ID",
    stale: root.dataset.currentPlayersStaleLabel || "Stale",
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

  function boundedCount(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) {
      return "0";
    }
    return String(Math.trunc(number));
  }

  function updateCountSummary(data) {
    if (!countSummary) {
      return;
    }
    countSummary.textContent = labels.countTemplate
      .replace("{count}", boundedCount(data.filtered_count))
      .replace("{total}", boundedCount(data.total_count));
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
      span.className = "wrap-value";
    }
    span.textContent = String(text || "");
    cell.append(span);
    return cell;
  }

  function idCell(reliableId) {
    const cell = document.createElement("td");
    const id = String(reliableId || "");
    const span = document.createElement("span");
    if (id) {
      span.className = "player-id-cell player-id-clip";
      span.title = id;
      span.textContent = clippedReliableId(id);
    } else {
      span.className = "muted";
      span.textContent = labels.noReliableId;
    }
    cell.append(span);
    return cell;
  }

  function playerRow(player) {
    const row = document.createElement("tr");
    row.append(textCell(player.display_name || "", "strong"));
    row.append(idCell(player.reliable_id));
    row.append(textCell(player.source || "", "wrap-value"));
    return row;
  }

  function emptyMessage(data) {
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
    tableBody.replaceChildren(...players.map(playerRow));
    tableWrap.hidden = players.length === 0;
    emptyNode.hidden = players.length > 0;
    if (players.length === 0) {
      emptyNode.textContent = emptyMessage(data);
    }
  }

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
