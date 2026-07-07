(() => {
  const root = document.querySelector("[data-dashboard-root]");
  if (!root) {
    return;
  }

  const endpoint = root.dataset.dashboardEndpoint || "/dashboard/status.json";
  const parsedInterval = Number.parseInt(root.dataset.dashboardIntervalMs || "7000", 10);
  const intervalMs = Number.isFinite(parsedInterval)
    ? Math.min(Math.max(parsedInterval, 5000), 10000)
    : 7000;
  const staleAfterMs = Math.min(Math.max(intervalMs * 4, 30000), 60000);
  const repeatedFailureThreshold = 3;
  const liveStatus = document.querySelector("[data-dashboard-live-status]");
  const refreshState = {
    lastSuccessAt: null,
    failureCount: 0,
    inFlight: false,
  };

  function statusLabel(state) {
    if (!liveStatus) {
      return "";
    }
    if (state === "stale") {
      return liveStatus.dataset.staleLabel || "Dashboard data may be stale.";
    }
    if (state === "paused") {
      return liveStatus.dataset.pausedLabel || "Live refresh paused/reconnecting.";
    }
    return liveStatus.dataset.freshLabel || "Live refresh active";
  }

  function setLiveStatus(state) {
    if (!liveStatus) {
      return;
    }
    const safeState = state === "stale" || state === "paused" ? state : "fresh";
    liveStatus.dataset.refreshState = safeState;
    liveStatus.dataset.stale = safeState === "stale" ? "true" : "false";
    liveStatus.textContent = statusLabel(safeState);
  }

  function browserOffline() {
    return typeof navigator !== "undefined" && navigator.onLine === false;
  }

  function refreshShouldPause() {
    return document.hidden || browserOffline();
  }

  function applyFailureStatus() {
    if (refreshShouldPause()) {
      setLiveStatus("paused");
      return;
    }
    const now = Date.now();
    const lastSuccessAge =
      refreshState.lastSuccessAt === null ? null : now - refreshState.lastSuccessAt;
    const staleByAge = lastSuccessAge !== null && lastSuccessAge > staleAfterMs;
    const staleByFailures = refreshState.failureCount >= repeatedFailureThreshold;
    setLiveStatus(staleByAge || staleByFailures ? "stale" : "fresh");
  }

  function setFieldValue(field, value) {
    if (!field || value === undefined || value === null) {
      return;
    }
    document
      .querySelectorAll(`[data-dashboard-field="${field}"]`)
      .forEach((node) => {
        if (node.dataset.dashboardTimestamp === "true" && window.armactlSetLocalTime) {
          window.armactlSetLocalTime(node, String(value));
          return;
        }
        node.textContent = String(value);
      });
  }

  function setFieldStates(fieldStates) {
    if (!fieldStates || typeof fieldStates !== "object") {
      return;
    }
    Object.entries(fieldStates).forEach(([field, state]) => {
      const loading = state && state.loading === true;
      document
        .querySelectorAll(`[data-dashboard-field="${field}"]`)
        .forEach((node) => {
          node.dataset.dashboardLoading = loading ? "true" : "false";
        });
    });
  }

  function updateLifecycleClass(lifecycle) {
    document.querySelectorAll("[data-dashboard-lifecycle-class]").forEach((node) => {
      node.classList.forEach((className) => {
        if (className.startsWith("state-") || className.startsWith("status-pill-")) {
          node.classList.remove(className);
        }
      });
      const safeLifecycle = lifecycle || "unknown";
      node.classList.add("state-" + safeLifecycle);
      node.classList.add("status-pill-" + safeLifecycle);
    });
  }

  function clampPercent(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return null;
    }
    return Math.min(Math.max(number, 0), 100);
  }

  function metricText(metric) {
    if (metric && typeof metric.text === "string" && metric.text.trim()) {
      return metric.text;
    }
    return "unknown";
  }

  function updateMetricText(metricId, metric) {
    const loading = metric && metric.loading === true;
    document.querySelectorAll("[data-dashboard-metric-value]").forEach((node) => {
      if (node.dataset.dashboardMetricValue === metricId) {
        node.textContent = metricText(metric);
        node.dataset.dashboardLoading = loading ? "true" : "false";
      }
    });
  }

  function updateMetricFill(metricId, percent) {
    const width = percent === null ? 0 : percent;
    document.querySelectorAll("[data-dashboard-metric-fill]").forEach((node) => {
      if (node.dataset.dashboardMetricFill === metricId) {
        node.style.width = String(width) + "%";
      }
    });
  }

  function updateMetricContainers(metricId, metric, percent) {
    const loading = metric && metric.loading === true;
    const unavailable = !loading && (!metric || metric.available !== true || percent === null);
    document.querySelectorAll("[data-dashboard-meter]").forEach((node) => {
      if (node.dataset.dashboardMeter !== metricId) {
        return;
      }
      node.dataset.metricUnavailable = unavailable ? "true" : "false";
      node.dataset.metricLoading = loading ? "true" : "false";
      const bar = node.querySelector(".metric-bar");
      if (bar) {
        bar.setAttribute("aria-valuenow", String(percent === null ? 0 : percent));
      }
    });
  }

  function updateMeters(metrics) {
    if (!metrics || typeof metrics !== "object") {
      return;
    }
    Object.entries(metrics).forEach(([metricId, metric]) => {
      const percent = clampPercent(metric && metric.percent);
      updateMetricText(metricId, metric);
      updateMetricFill(metricId, percent);
      updateMetricContainers(metricId, metric, percent);
    });
  }

  function applyStatus(data) {
    if (!data || data.ok !== true || !data.fields) {
      throw new Error("Dashboard status payload is invalid.");
    }
    Object.entries(data.fields).forEach(([field, value]) => setFieldValue(field, value));
    setFieldStates(data.field_states);
    updateLifecycleClass(data.lifecycle);
    root.dataset.dashboardLifecycle = data.lifecycle || "unknown";
    updateMeters(data.metrics);
    refreshState.lastSuccessAt = Date.now();
    refreshState.failureCount = 0;
    setLiveStatus("fresh");
  }

  async function refreshDashboard() {
    if (refreshShouldPause()) {
      setLiveStatus("paused");
      return;
    }
    if (refreshState.inFlight) {
      return;
    }
    refreshState.inFlight = true;
    try {
      const response = await fetch(endpoint, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`Dashboard status request failed: ${response.status}`);
      }
      applyStatus(await response.json());
    } catch (_error) {
      refreshState.failureCount += 1;
      applyFailureStatus();
    } finally {
      refreshState.inFlight = false;
    }
  }

  function triggerImmediateRefresh() {
    if (!refreshState.inFlight) {
      refreshDashboard();
    }
  }

  if (refreshShouldPause()) {
    setLiveStatus("paused");
  }
  window.setTimeout(refreshDashboard, 1000);
  window.setInterval(refreshDashboard, intervalMs);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      setLiveStatus("paused");
      return;
    }
    setLiveStatus("paused");
    triggerImmediateRefresh();
  });
  window.addEventListener("focus", triggerImmediateRefresh);
  window.addEventListener("online", () => {
    setLiveStatus("paused");
    triggerImmediateRefresh();
  });
  window.addEventListener("offline", () => setLiveStatus("paused"));
})();
